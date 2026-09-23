"""Authentication, session, rate-limit and audit primitives for Career Quest."""

from __future__ import annotations

import base64
import hashlib
import hmac
import math
import os
import secrets
import threading
import time
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


PBKDF2_ITERATIONS = 260_000


def _password_hash(password: str, salt: bytes) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, PBKDF2_ITERATIONS)


def _password_record(password: str) -> tuple[str, str]:
    salt = secrets.token_bytes(16)
    digest = _password_hash(password, salt)
    return base64.b64encode(salt).decode("ascii"), base64.b64encode(digest).decode("ascii")


@dataclass(frozen=True)
class User:
    username: str
    role: str
    display_name: str
    employee_id: str | None
    salt_b64: str
    digest_b64: str


@dataclass
class Session:
    token: str
    csrf_token: str
    username: str
    role: str
    display_name: str
    employee_id: str | None
    created_at: float
    expires_at: float

    def public(self) -> dict[str, Any]:
        return {
            "username": self.username,
            "role": self.role,
            "display_name": self.display_name,
            "employee_id": self.employee_id,
            "csrf_token": self.csrf_token,
            "expires_in": max(0, int(self.expires_at - time.time())),
        }


class AuthManager:
    """Thread-safe in-memory auth for the synthetic hackathon demo."""

    def __init__(self) -> None:
        self.session_ttl = max(300, int(os.getenv("CQ_SESSION_TTL", "7200")))
        self.max_failures = max(3, int(os.getenv("CQ_MAX_LOGIN_FAILURES", "5")))
        self.max_client_failures = max(self.max_failures, int(os.getenv("CQ_MAX_CLIENT_FAILURES", "20")))
        self.failure_window = max(30, int(os.getenv("CQ_LOGIN_WINDOW", "60")))
        self.lock_seconds = max(30, int(os.getenv("CQ_LOGIN_LOCK", "60")))
        self.demo_mode = os.getenv("CQ_DEMO_MODE", "1") == "1"
        password_vars_present = (
            "CQ_EMPLOYEE_PASSWORD" in os.environ,
            "CQ_HR_PASSWORD" in os.environ,
        )
        if any(password_vars_present) and not all(password_vars_present):
            raise RuntimeError("Set both CQ_EMPLOYEE_PASSWORD and CQ_HR_PASSWORD, or neither")
        self.quick_login = self.demo_mode and not any(password_vars_present)
        employee_password = os.getenv("CQ_EMPLOYEE_PASSWORD", "EmployeeDemo!2026")
        hr_password = os.getenv("CQ_HR_PASSWORD", "HRDemo!2026#")
        if not self.demo_mode and ("CQ_EMPLOYEE_PASSWORD" not in os.environ or "CQ_HR_PASSWORD" not in os.environ):
            raise RuntimeError("Production mode requires CQ_EMPLOYEE_PASSWORD and CQ_HR_PASSWORD")
        if not 12 <= len(employee_password) <= 128 or not 12 <= len(hr_password) <= 128:
            raise RuntimeError("Demo account passwords must contain 12 to 128 characters")
        if employee_password == hr_password:
            raise RuntimeError("Employee and HR passwords must be different")
        if not self.demo_mode and {
            employee_password,
            hr_password,
        } & {"EmployeeDemo!2026", "HRDemo!2026#"}:
            raise RuntimeError("Production mode cannot use shipped demo passwords")
        self.users = {
            "employee": self._make_user("employee", employee_password, "employee", "Аян Серик", "E0028"),
            "hr": self._make_user("hr", hr_password, "hr", "HR Business Partner", None),
        }
        self.sessions: dict[str, Session] = {}
        self.failures: dict[str, deque[float]] = {}
        self.client_failures: dict[str, deque[float]] = {}
        self.locked_until: dict[str, float] = {}
        self.client_locked_until: dict[str, float] = {}
        self.audit_events: deque[dict[str, Any]] = deque(maxlen=200)
        self._lock = threading.RLock()
        self._hash_slots = threading.BoundedSemaphore(2)
        self._inflight_clients: dict[str, int] = {}
        self._dummy_salt = secrets.token_bytes(16)
        self._dummy_digest = _password_hash(secrets.token_urlsafe(24), self._dummy_salt)
        self._rate_limit_audit_event: dict[str, Any] | None = None
        self._rate_limit_audit_at = 0.0

    @staticmethod
    def _make_user(username: str, password: str, role: str, display_name: str, employee_id: str | None) -> User:
        salt, digest = _password_record(password)
        return User(username, role, display_name, employee_id, salt, digest)

    @staticmethod
    def _key(username: str, client_id: str) -> str:
        return f"{client_id[:80]}:{username[:80].lower()}"

    def _prune_bucket(self, store: dict[str, deque[float]], key: str, now: float) -> deque[float]:
        attempts = store.get(key, deque())
        horizon = now - self.failure_window
        while attempts and attempts[0] < horizon:
            attempts.popleft()
        if attempts:
            store[key] = attempts
        else:
            store.pop(key, None)
        return attempts

    def _cleanup_rate_state(self, now: float) -> None:
        for key in list(self.failures):
            self._prune_bucket(self.failures, key, now)
        for key in list(self.client_failures):
            self._prune_bucket(self.client_failures, key, now)
        for store in (self.locked_until, self.client_locked_until):
            for key in [item for item, until in store.items() if until <= now]:
                del store[key]

    def _prune_sessions(self, now: float) -> None:
        for token in [token for token, session in self.sessions.items() if session.expires_at <= now]:
            del self.sessions[token]

    def authenticate(self, username: str, password: str, client_id: str) -> tuple[Session | None, str, int]:
        username = username.strip().lower()[:80]
        password = password[:128]
        now = time.time()
        key = self._key(username, client_id)
        client_key = client_id[:80]
        with self._lock:
            self._cleanup_rate_state(now)
            account_lock = self.locked_until.get(key, 0)
            client_lock = self.client_locked_until.get(client_key, 0)
            locked_until = max(account_lock, client_lock)
            if locked_until > now:
                retry_after = max(1, math.ceil(locked_until - now))
                self.audit("login_rate_limited", username or "unknown", "unknown", "denied", "Too many attempts")
                return None, "rate_limited", retry_after
            if self._inflight_clients.get(client_key, 0) >= 2:
                self.audit("login_rate_limited", username or "unknown", "unknown", "denied", "Concurrent attempt limit")
                return None, "rate_limited", 1
            self._inflight_clients[client_key] = self._inflight_clients.get(client_key, 0) + 1

            user = self.users.get(username)
            if user:
                salt = base64.b64decode(user.salt_b64)
                expected = base64.b64decode(user.digest_b64)
            else:
                salt = self._dummy_salt
                expected = self._dummy_digest
        # Keep expensive verification outside the state lock. A non-blocking
        # global slot plus the per-client reservation rejects bursts instead
        # of creating an unbounded queue of handler threads.
        if not self._hash_slots.acquire(blocking=False):
            with self._lock:
                remaining = self._inflight_clients.get(client_key, 1) - 1
                if remaining > 0:
                    self._inflight_clients[client_key] = remaining
                else:
                    self._inflight_clients.pop(client_key, None)
                self.audit("login_rate_limited", username or "unknown", "unknown", "denied", "Hash capacity reached")
            return None, "rate_limited", 1
        try:
            actual = _password_hash(password, salt)
        finally:
            self._hash_slots.release()
            with self._lock:
                remaining = self._inflight_clients.get(client_key, 1) - 1
                if remaining > 0:
                    self._inflight_clients[client_key] = remaining
                else:
                    self._inflight_clients.pop(client_key, None)
        valid = bool(user) and hmac.compare_digest(actual, expected)
        now = time.time()
        with self._lock:
            if not valid:
                attempts = self._prune_bucket(self.failures, key, now)
                client_attempts = self._prune_bucket(self.client_failures, client_key, now)
                attempts.append(now)
                client_attempts.append(now)
                self.failures[key] = attempts
                self.client_failures[client_key] = client_attempts
                if len(attempts) >= self.max_failures:
                    self.locked_until[key] = now + self.lock_seconds
                if len(client_attempts) >= self.max_client_failures:
                    self.client_locked_until[client_key] = now + self.lock_seconds
                self.audit("login_failure", username or "unknown", "unknown", "denied", "Invalid credentials")
                return None, "invalid_credentials", 0

            self.failures.pop(key, None)
            self.locked_until.pop(key, None)
            self._prune_sessions(now)
            # One live session per demo principal prevents unbounded session
            # growth and turns a new login into explicit token rotation.
            for token in [token for token, item in self.sessions.items() if item.username == user.username]:
                del self.sessions[token]
            session = Session(
                token=secrets.token_urlsafe(32),
                csrf_token=secrets.token_urlsafe(24),
                username=user.username,
                role=user.role,
                display_name=user.display_name,
                employee_id=user.employee_id,
                created_at=now,
                expires_at=now + self.session_ttl,
            )
            self.sessions[session.token] = session
            self.audit("login_success", user.username, user.role, "allowed", "Session created")
            return session, "ok", 0

    def session(self, token: str | None) -> Session | None:
        if not token:
            return None
        with self._lock:
            self._prune_sessions(time.time())
            return self.sessions.get(token)

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            session = self.sessions.pop(token, None)
            if session:
                self.audit("logout", session.username, session.role, "allowed", "Session revoked")

    def reset_runtime(self) -> None:
        """Clear volatile state between tests without changing user records."""
        with self._lock:
            self.sessions.clear()
            self.failures.clear()
            self.client_failures.clear()
            self.locked_until.clear()
            self.client_locked_until.clear()
            self._inflight_clients.clear()
            self.audit_events.clear()
            self._rate_limit_audit_event = None
            self._rate_limit_audit_at = 0.0

    def audit(self, action: str, username: str, role: str, outcome: str, detail: str) -> None:
        now = time.time()
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "action": action[:80],
            "actor": username[:80],
            "role": role[:30],
            "outcome": outcome[:20],
            "detail": detail.replace("\n", " ")[:180],
        }
        with self._lock:
            # A brute-force burst must not evict the rest of the security trail.
            # Collapse repeated 429 events into one rolling entry while retaining
            # the total number of blocked requests.
            if action == "login_rate_limited":
                previous = self._rate_limit_audit_event
                if previous is not None and now - self._rate_limit_audit_at <= 60 and any(item is previous for item in self.audit_events):
                    count = int(previous.get("count", 1)) + 1
                    previous.update(
                        {
                            "timestamp": event["timestamp"],
                            "actor": "multiple" if previous.get("actor") != event["actor"] else event["actor"],
                            "detail": f"Blocked login burst · {count} requests",
                            "count": count,
                        }
                    )
                    self._rate_limit_audit_at = now
                    return
                event["count"] = 1
                self._rate_limit_audit_event = event
                self._rate_limit_audit_at = now
            self.audit_events.appendleft(event)

    def security_summary(self) -> dict[str, Any]:
        with self._lock:
            self._prune_sessions(time.time())
            return {
                "active_sessions": len(self.sessions),
                "session_ttl_minutes": round(self.session_ttl / 60),
                "demo_mode": self.demo_mode,
                "controls": [
                    {"name": "Server-side RBAC", "status": "active", "detail": "employee и HR проверяются на каждом API-маршруте"},
                    {"name": "IDOR protection", "status": "active", "detail": "сотрудник не может запросить чужой employee_id"},
                    {"name": "Protected session", "status": "active", "detail": "случайный HttpOnly SameSite=Strict cookie"},
                    {"name": "CSRF protection", "status": "active", "detail": "изменяющие запросы требуют session-bound token"},
                    {"name": "Password hashing", "status": "active", "detail": f"PBKDF2-HMAC-SHA256 · {PBKDF2_ITERATIONS:,} итераций"},
                    {"name": "Brute-force guard", "status": "active", "detail": f"account + client limits; блокировка после {self.max_failures} попыток"},
                    {"name": "Security headers", "status": "active", "detail": "CSP, anti-clickjacking, nosniff, strict referrer policy"},
                    {"name": "Audit trail", "status": "active", "detail": "входы, отказы доступа и критичные изменения журналируются"},
                ],
                "audit_events": list(self.audit_events)[:30],
            }
