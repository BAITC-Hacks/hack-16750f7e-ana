"""Local accounts and expiring sessions; no third-party dependencies."""

import hashlib
import hmac
import secrets
import time
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
                raise AuthError("Слишком много попыток. Подождите минуту.", 429)
            record = self.accounts.get(username)
            digest = self._hash(password, record["salt"] if record else b"unknown-account")
            if not record or not hmac.compare_digest(digest, record["hash"]):
                self.attempts[client] = (count + 1, until)
                raise AuthError("Неверный логин или пароль")
            self.attempts.pop(client, None)
            self.sessions = {key: value for key, value in self.sessions.items() if value["expires"] > now}
            token = secrets.token_urlsafe(32)
            session = {"user": dict(record["user"]), "csrf": secrets.token_urlsafe(32), "expires": now + 8 * 3600}
            self.sessions[token] = session
            return token, session

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
