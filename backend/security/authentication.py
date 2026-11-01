"""Device pairing and token verification for the phone bridge."""
from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from dataclasses import dataclass

from database.repository import Repository


@dataclass
class PairingCode:
    code: str
    expires_at: float


class SecurityService:
    def __init__(self, repo: Repository):
        self.repo = repo
        salt = repo.get_setting("security.salt")
        if not salt:
            salt = secrets.token_hex(16)
            repo.set_setting("security.salt", salt)
        self._salt = bytes.fromhex(salt)

    @property
    def device_id(self) -> str:
        return self.repo.get_setting("security.device_id", "")

    def hash_token(self, token: str) -> str:
        return hmac.new(self._salt, token.encode("utf-8"), hashlib.sha256).hexdigest()

    def new_device_token(self) -> str:
        return secrets.token_urlsafe(32)

    def verify_device(self, device_id: str, token: str) -> bool:
        if not device_id or not token:
            return False
        row = self.repo.get_device(device_id)
        if not row or row.get("revoked"):
            return False
        expected = row["token_hash"]
        return hmac.compare_digest(expected, self.hash_token(token))


class PairingCodes:
    """Short-lived pairing codes held in memory (never written to disk)."""

    def __init__(self, ttl_seconds: int = 120):
        self.ttl = ttl_seconds
        self._codes: dict[str, float] = {}

    def create(self) -> PairingCode:
        self._prune()
        code = f"{secrets.randbelow(1_000_000):06d}"
        expires = time.time() + self.ttl
        self._codes[code] = expires
        return PairingCode(code=code, expires_at=expires)

    def consume(self, code: str) -> bool:
        self._prune()
        expires = self._codes.get(code.strip())
        if expires is None or expires < time.time():
            return False
        del self._codes[code.strip()]
        return True

    def _prune(self) -> None:
        now = time.time()
        for code in [c for c, exp in self._codes.items() if exp < now]:
            del self._codes[code]


class RateLimiter:
    """Small in-memory sliding window limiter for local API endpoints."""

    def __init__(self, limit: int = 30, window_seconds: int = 60):
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, list[float]] = {}

    def allow(self, key: str) -> bool:
        now = time.time()
        hits = [t for t in self._hits.get(key, []) if now - t < self.window]
        if len(hits) >= self.limit:
            self._hits[key] = hits
            return False
        hits.append(now)
        self._hits[key] = hits
        return True
