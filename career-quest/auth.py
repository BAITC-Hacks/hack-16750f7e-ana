"""Local accounts and expiring sessions; no third-party dependencies."""

import hashlib
import hmac
import secrets
import time
from collections import deque
from datetime import datetime, timezone
from threading import RLock


class AuthError(Exception):
    def __init__(self, message, status=401):
        super().__init__(message)
        self.status = status


class AuthStore:
    def __init__(self, accounts):
        self.lock = RLock()
        self.accounts = {}
        self.sessions = {}
        self.attempts = {}
        self.audit_events = deque(maxlen=200)
        self._rate_limit_audit_event = None
        self._rate_limit_audit_at = 0
        for username, password, role, employee_id in accounts:
            salt = secrets.token_bytes(16)
            self.accounts[username] = {"salt": salt, "hash": self._hash(password, salt),
                                       "user": {"username": username, "role": role, "employee_id": employee_id}}

    @staticmethod
    def _hash(password, salt):
        return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, 200_000)

    def login(self, username, password, client):
        with self.lock:
            now = time.time()
            self.attempts = {key: value for key, value in self.attempts.items() if value[1] > now}
            count, until = self.attempts.get(client, (0, now + 60))
            if count >= 5:
                self.audit("login_rate_limited", username, "unknown", "denied", "Blocked login")
                raise AuthError("Слишком много попыток. Подождите минуту.", 429)
            record = self.accounts.get(username)
            digest = self._hash(password, record["salt"] if record else b"unknown-account")
            if not record or not hmac.compare_digest(digest, record["hash"]):
                self.attempts[client] = (count + 1, now + 60 if count + 1 >= 5 else until)
                raise AuthError("Неверный логин или пароль")
            self.attempts.pop(client, None)
            self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > now and value["user"]["username"] != username}
            token = secrets.token_urlsafe(32)
            session = {"user": dict(record["user"]), "csrf": secrets.token_urlsafe(32), "expires": now + 8 * 3600}
            self.sessions[token] = session
            return token, session

    def security_summary(self):
        with self.lock:
            return {"audit_events": list(self.audit_events)[:30],
                    "active_sessions": sum(s["expires"] > time.time() for s in self.sessions.values())}

    def session(self, token):
        with self.lock:
            session = self.sessions.get(token)
            if not session or session["expires"] <= time.time():
                self.sessions.pop(token, None)
                raise AuthError("Войдите в приложение")
            return session

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token, None)

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
        with self.lock:
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
