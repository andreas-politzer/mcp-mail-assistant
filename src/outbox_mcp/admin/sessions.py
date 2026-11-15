"""Admin sessions and the sign-in throttle. Everything lives in memory of the admin process:
a restart signs everybody out, which is right for a rarely used tool. The mail service never
sees any of this."""

from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from typing import Callable, Dict, Optional

MAX_SESSIONS = 5


@dataclass
class _Session:
    created: float
    last_seen: float


class SessionStore:
    def __init__(self, idle_seconds: int, max_seconds: int, clock: Callable[[], float]) -> None:
        self._idle, self._max, self._clock = idle_seconds, max_seconds, clock
        self._sessions: Dict[str, _Session] = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def create(self) -> str:
        now = self._clock()
        token = secrets.token_urlsafe(32)
        with self._lock:
            self._drop_expired(now)
            while len(self._sessions) >= MAX_SESSIONS:  # keep the newest few
                oldest = min(self._sessions, key=lambda k: self._sessions[k].created)
                del self._sessions[oldest]
            self._sessions[self._key(token)] = _Session(now, now)
        return token

    def touch(self, token: Optional[str]) -> bool:
        """True if the token belongs to a live session; extends the idle time."""
        if not token:
            return False
        now = self._clock()
        with self._lock:
            key = self._key(token)
            session = self._sessions.get(key)
            if session is None:
                return False
            if now - session.last_seen > self._idle or now - session.created > self._max:
                del self._sessions[key]
                return False
            session.last_seen = now
            return True

    def revoke(self, token: Optional[str]) -> None:
        if token:
            with self._lock:
                self._sessions.pop(self._key(token), None)

    def _drop_expired(self, now: float) -> None:
        for key in [k for k, s in self._sessions.items() if now - s.last_seen > self._idle or now - s.created > self._max]:
            del self._sessions[key]

    def count(self) -> int:
        with self._lock:
            self._drop_expired(self._clock())
            return len(self._sessions)


class LoginThrottle:
    """After `max_failed` wrong passphrases, every sign-in is refused for `lockout` seconds. Global,
    not per client: there is one owner."""

    def __init__(self, max_failed: int, lockout_seconds: int, clock: Callable[[], float]) -> None:
        self._max, self._lockout, self._clock = max_failed, lockout_seconds, clock
        self._failed = 0
        self._locked_until = 0.0
        self._lock = threading.Lock()

    def locked(self) -> bool:
        with self._lock:
            return self._clock() < self._locked_until

    def failed(self) -> bool:
        """Record a failure. Returns True if this one started a lockout."""
        with self._lock:
            self._failed += 1
            if self._failed >= self._max:
                self._failed = 0
                self._locked_until = self._clock() + self._lockout
                return True
            return False

    def succeeded(self) -> None:
        with self._lock:
            self._failed = 0
