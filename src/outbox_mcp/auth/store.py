"""SQLite storage for the authorization server.

Only hashes of codes and tokens are stored. Every state change that must be atomic
(redeeming a code, rotating a refresh token) happens inside one ``BEGIN IMMEDIATE``
transaction, so two concurrent requests cannot both succeed.

All calls are short, synchronous SQLite operations. That is fine for one owner and a
handful of requests per hour; it would need a thread pool for real traffic.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from mcp.shared.auth import OAuthClientInformationFull

DEFAULT_ACCESS_TTL = 3600
DEFAULT_REFRESH_TTL = 90 * 86400

_TABLES = (
    "clients", "pending_requests", "auth_codes", "families",
    "access_tokens", "refresh_tokens", "login_failures",
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS clients (
    client_id TEXT PRIMARY KEY, info_json TEXT NOT NULL, created_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS pending_requests (
    request_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, params_json TEXT NOT NULL,
    created_at REAL NOT NULL, expires_at REAL NOT NULL, attempts INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS families (
    family_id TEXT PRIMARY KEY, client_id TEXT NOT NULL, created_at REAL NOT NULL,
    revoked INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS auth_codes (
    code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, scopes TEXT NOT NULL,
    code_challenge TEXT NOT NULL, redirect_uri TEXT NOT NULL, redirect_explicit INTEGER NOT NULL,
    resource TEXT, subject TEXT, expires_at REAL NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0, family_id TEXT);
CREATE TABLE IF NOT EXISTS access_tokens (
    token_hash TEXT PRIMARY KEY, family_id TEXT NOT NULL, client_id TEXT NOT NULL,
    scopes TEXT NOT NULL, resource TEXT, subject TEXT, expires_at REAL NOT NULL);
CREATE TABLE IF NOT EXISTS refresh_tokens (
    token_hash TEXT PRIMARY KEY, family_id TEXT NOT NULL, client_id TEXT NOT NULL,
    scopes TEXT NOT NULL, resource TEXT, subject TEXT, expires_at REAL NOT NULL,
    consumed INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS login_failures (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL NOT NULL, done INTEGER NOT NULL DEFAULT 0);
"""


class ClientLimitReached(Exception):
    pass


class PendingLimitReached(Exception):
    pass


@dataclass(frozen=True)
class Pending:
    client_id: str
    params: dict
    attempts: int


@dataclass(frozen=True)
class CodeRow:
    client_id: str
    scopes: list
    code_challenge: str
    redirect_uri: str
    redirect_explicit: bool
    resource: Optional[str]
    subject: Optional[str]
    expires_at: float
    consumed: bool
    family_id: Optional[str]


@dataclass(frozen=True)
class TokenRow:
    client_id: str
    scopes: list
    resource: Optional[str]
    subject: Optional[str]
    expires_at: float
    family_id: str


@dataclass(frozen=True)
class Issued:
    access_token: str
    refresh_token: str
    expires_in: int
    scopes: list


def _h(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class AuthStore:
    def __init__(self, path, clock: Callable[[], float] = time.time):
        self.path = Path(path)
        self.clock = clock
        self._create_private_file()
        self._init_schema()

    def now(self) -> float:
        return float(self.clock())

    # ------------------------------------------------------------------ plumbing
    def _create_private_file(self) -> None:
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            os.chmod(self.path, 0o600)
        except OSError:
            pass  # best effort, e.g. on file systems without POSIX permissions

    def _init_schema(self) -> None:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=10, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")
        finally:
            conn.close()

    def counts(self) -> dict:
        with self._tx() as conn:
            return {t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in _TABLES}

    # ------------------------------------------------------------------ clients
    def register_client(self, info: OAuthClientInformationFull, *, max_clients: int,
                        prune_before: float) -> None:
        with self._tx() as conn:
            # A client counts as "in use" while it has a refresh token that can still be used,
            # an open login or an unused code. Everything else older than the cutoff is removed.
            conn.execute(
                "DELETE FROM clients WHERE created_at < ? "
                "AND client_id NOT IN (SELECT client_id FROM refresh_tokens WHERE consumed = 0 AND expires_at >= ?) "
                "AND client_id NOT IN (SELECT client_id FROM pending_requests WHERE expires_at >= ?) "
                "AND client_id NOT IN (SELECT client_id FROM auth_codes WHERE consumed = 0 AND expires_at >= ?)",
                (prune_before, self.now(), self.now(), self.now()),
            )
            if conn.execute("SELECT COUNT(*) FROM clients").fetchone()[0] >= max_clients:
                raise ClientLimitReached()
            conn.execute(
                "INSERT INTO clients (client_id, info_json, created_at) VALUES (?, ?, ?)",
                (info.client_id, info.model_dump_json(), self.now()),
            )

    def get_client(self, client_id: str) -> Optional[OAuthClientInformationFull]:
        with self._tx() as conn:
            row = conn.execute("SELECT info_json FROM clients WHERE client_id = ?", (client_id,)).fetchone()
        return OAuthClientInformationFull.model_validate_json(row["info_json"]) if row else None

    # ------------------------------------------------------------------ pending logins
    def create_pending(self, client_id: str, params: dict, ttl: int, max_pending: int = 20) -> str:
        request_id = secrets.token_urlsafe(32)
        now = self.now()
        with self._tx() as conn:
            conn.execute("DELETE FROM pending_requests WHERE expires_at < ?", (now,))
            if conn.execute("SELECT COUNT(*) FROM pending_requests").fetchone()[0] >= max_pending:
                raise PendingLimitReached()
            conn.execute(
                "INSERT INTO pending_requests (request_hash, client_id, params_json, created_at, "
                "expires_at) VALUES (?, ?, ?, ?, ?)",
                (_h(request_id), client_id, json.dumps(params), now, now + ttl),
            )
        return request_id

    def _pending_row(self, conn, request_id: str):
        return conn.execute(
            "SELECT * FROM pending_requests WHERE request_hash = ? AND expires_at >= ?",
            (_h(request_id), self.now()),
        ).fetchone()

    @staticmethod
    def _to_pending(row) -> Pending:
        return Pending(row["client_id"], json.loads(row["params_json"]), row["attempts"])

    def get_pending(self, request_id: str) -> Optional[Pending]:
        if not request_id:
            return None
        with self._tx() as conn:
            row = self._pending_row(conn, request_id)
        return self._to_pending(row) if row else None

    def fail_pending(self, request_id: str, max_attempts: int) -> int:
        """Count one wrong guess. Deletes the request when the limit is reached.

        Returns how many guesses are left (0 means the request is gone)."""
        with self._tx() as conn:
            row = self._pending_row(conn, request_id)
            if row is None:
                return 0
            attempts = row["attempts"] + 1
            if attempts >= max_attempts:
                conn.execute("DELETE FROM pending_requests WHERE request_hash = ?", (_h(request_id),))
                return 0
            conn.execute("UPDATE pending_requests SET attempts = ? WHERE request_hash = ?",
                         (attempts, _h(request_id)))
            return max_attempts - attempts

    def consume_pending(self, request_id: str) -> Optional[Pending]:
        """Atomically take a pending request. A request can be consumed exactly once."""
        with self._tx() as conn:
            row = self._pending_row(conn, request_id)
            if row is None:
                return None
            conn.execute("DELETE FROM pending_requests WHERE request_hash = ?", (_h(request_id),))
        return self._to_pending(row)

    # ------------------------------------------------------------------ login throttle
    def reserve_login_attempt(self, max_failures: int, window: int):
        """Atomically claim one password attempt, *before* the password is checked.

        Returns ``(0, attempt_id)`` when the attempt may go ahead, otherwise
        ``(seconds_until_next_attempt, None)``. Checking and counting happen in one
        transaction, so parallel requests cannot exceed the limit. Call
        ``finish_login_attempt`` when the check is over."""
        now = self.now()
        with self._tx() as conn:
            conn.execute("DELETE FROM login_failures WHERE at < ?", (now - window,))
            rows = conn.execute("SELECT at FROM login_failures ORDER BY at DESC LIMIT ?",
                                (max_failures,)).fetchall()
            if len(rows) >= max_failures:
                return max(1.0, rows[-1]["at"] + window - now), None
            cur = conn.execute("INSERT INTO login_failures (at) VALUES (?)", (now,))
            return 0.0, cur.lastrowid

    def finish_login_attempt(self, attempt_id: int, success: bool) -> None:
        """A wrong password stays counted. A correct one resets the finished failures and
        its own claim, but never the claims of checks that are still running."""
        with self._tx() as conn:
            if success:
                conn.execute("DELETE FROM login_failures WHERE done = 1 OR id = ?", (attempt_id,))
            else:
                conn.execute("UPDATE login_failures SET done = 1 WHERE id = ?", (attempt_id,))

    def login_failure_count(self, window: int = 900) -> int:
        with self._tx() as conn:
            return conn.execute("SELECT COUNT(*) FROM login_failures WHERE at >= ?",
                                (self.now() - window,)).fetchone()[0]

    # ------------------------------------------------------------------ authorization codes
    def create_code(self, *, client_id: str, scopes: list, code_challenge: str, redirect_uri: str,
                    redirect_explicit: bool, resource: Optional[str], subject: Optional[str],
                    ttl: int) -> str:
        code = secrets.token_urlsafe(32)
        with self._tx() as conn:
            conn.execute(
                "INSERT INTO auth_codes (code_hash, client_id, scopes, code_challenge, redirect_uri, "
                "redirect_explicit, resource, subject, expires_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (_h(code), client_id, " ".join(scopes), code_challenge, redirect_uri,
                 int(redirect_explicit), resource, subject, self.now() + ttl),
            )
        return code

    def load_code(self, code: str, client_id: str) -> Optional[CodeRow]:
        """Look a code up for exchange. A code that was already used burns its tokens."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM auth_codes WHERE code_hash = ?", (_h(code),)).fetchone()
            if row is None or row["client_id"] != client_id:
                return None
            if row["consumed"]:
                if row["family_id"]:
                    self._revoke_family(conn, row["family_id"])
                return None
            if row["expires_at"] < self.now():
                return None
        return CodeRow(
            client_id=row["client_id"], scopes=row["scopes"].split(), code_challenge=row["code_challenge"],
            redirect_uri=row["redirect_uri"], redirect_explicit=bool(row["redirect_explicit"]),
            resource=row["resource"], subject=row["subject"], expires_at=row["expires_at"],
            consumed=False, family_id=None,
        )

    def redeem_code(self, code: str, client_id: str, *, access_ttl: int = DEFAULT_ACCESS_TTL,
                    refresh_ttl: int = DEFAULT_REFRESH_TTL) -> Optional[Issued]:
        """Use a code exactly once and issue the first token pair, all in one transaction."""
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM auth_codes WHERE code_hash = ?", (_h(code),)).fetchone()
            if row is None or row["client_id"] != client_id:
                return None
            if row["consumed"]:
                if row["family_id"]:
                    self._revoke_family(conn, row["family_id"])
                return None
            if row["expires_at"] < self.now():
                return None
            family_id = self._new_family(conn, client_id)
            changed = conn.execute(
                "UPDATE auth_codes SET consumed = 1, family_id = ? WHERE code_hash = ? AND consumed = 0",
                (family_id, _h(code)),
            ).rowcount
            if changed != 1:
                return None
            return self._insert_tokens(conn, family_id, client_id, row["scopes"].split(),
                                       row["resource"], row["subject"], access_ttl, refresh_ttl)

    # ------------------------------------------------------------------ families and tokens
    def _new_family(self, conn, client_id: str) -> str:
        family_id = "fam_" + secrets.token_urlsafe(12)
        conn.execute("INSERT INTO families (family_id, client_id, created_at) VALUES (?, ?, ?)",
                     (family_id, client_id, self.now()))
        return family_id

    def new_family(self, client_id: str) -> str:
        with self._tx() as conn:
            return self._new_family(conn, client_id)

    def _insert_tokens(self, conn, family_id, client_id, scopes, resource, subject,
                       access_ttl, refresh_ttl) -> Issued:
        access = "oat_" + secrets.token_urlsafe(32)
        refresh = "ort_" + secrets.token_urlsafe(32)
        now = self.now()
        conn.execute(
            "INSERT INTO access_tokens (token_hash, family_id, client_id, scopes, resource, subject, "
            "expires_at) VALUES (?,?,?,?,?,?,?)",
            (_h(access), family_id, client_id, " ".join(scopes), resource, subject, now + access_ttl),
        )
        conn.execute(
            "INSERT INTO refresh_tokens (token_hash, family_id, client_id, scopes, resource, subject, "
            "expires_at) VALUES (?,?,?,?,?,?,?)",
            (_h(refresh), family_id, client_id, " ".join(scopes), resource, subject, now + refresh_ttl),
        )
        return Issued(access, refresh, access_ttl, list(scopes))

    def issue_tokens(self, *, family_id: str, client_id: str, scopes: list, resource: Optional[str],
                     subject: Optional[str], access_ttl: int = DEFAULT_ACCESS_TTL,
                     refresh_ttl: int = DEFAULT_REFRESH_TTL) -> Issued:
        with self._tx() as conn:
            fam = conn.execute("SELECT revoked FROM families WHERE family_id = ?", (family_id,)).fetchone()
            if fam is None or fam["revoked"]:
                raise ValueError("Unknown or revoked token family.")
            return self._insert_tokens(conn, family_id, client_id, scopes, resource, subject,
                                       access_ttl, refresh_ttl)

    @staticmethod
    def _to_token_row(row) -> TokenRow:
        return TokenRow(row["client_id"], row["scopes"].split(), row["resource"], row["subject"],
                        row["expires_at"], row["family_id"])

    def lookup_access(self, token: str) -> Optional[TokenRow]:
        if not token:
            return None
        with self._tx() as conn:
            row = conn.execute(
                "SELECT a.*, f.revoked AS revoked FROM access_tokens a "
                "JOIN families f ON f.family_id = a.family_id WHERE a.token_hash = ?",
                (_h(token),),
            ).fetchone()
        if row is None or row["revoked"] or row["expires_at"] <= self.now():
            return None
        return self._to_token_row(row)

    def load_refresh(self, token: str, client_id: str) -> Optional[TokenRow]:
        """Look a refresh token up. Presenting a rotated one burns the whole family."""
        if not token:
            return None
        with self._tx() as conn:
            row = conn.execute(
                "SELECT r.*, f.revoked AS revoked FROM refresh_tokens r "
                "JOIN families f ON f.family_id = r.family_id WHERE r.token_hash = ?",
                (_h(token),),
            ).fetchone()
            if row is None or row["client_id"] != client_id or row["revoked"]:
                return None
            if row["consumed"]:
                self._revoke_family(conn, row["family_id"])
                return None
            if row["expires_at"] <= self.now():
                return None
        return self._to_token_row(row)

    def rotate_refresh(self, token: str, client_id: str, scopes: list, *,
                       access_ttl: int = DEFAULT_ACCESS_TTL,
                       refresh_ttl: int = DEFAULT_REFRESH_TTL) -> Optional[Issued]:
        """Swap a refresh token for a new pair. The old refresh token can never be used again."""
        with self._tx() as conn:
            row = conn.execute(
                "SELECT r.*, f.revoked AS revoked FROM refresh_tokens r "
                "JOIN families f ON f.family_id = r.family_id WHERE r.token_hash = ?",
                (_h(token),),
            ).fetchone()
            if row is None or row["client_id"] != client_id or row["revoked"]:
                return None
            if row["consumed"]:
                self._revoke_family(conn, row["family_id"])
                return None
            if row["expires_at"] <= self.now():
                return None
            granted = row["scopes"].split()
            if not set(scopes) <= set(granted):
                return None
            changed = conn.execute(
                "UPDATE refresh_tokens SET consumed = 1 WHERE token_hash = ? AND consumed = 0",
                (_h(token),),
            ).rowcount
            if changed != 1:
                return None
            return self._insert_tokens(conn, row["family_id"], client_id, scopes or granted,
                                       row["resource"], row["subject"], access_ttl, refresh_ttl)

    # ------------------------------------------------------------------ revocation
    @staticmethod
    def _revoke_family(conn, family_id: str) -> None:
        conn.execute("UPDATE families SET revoked = 1 WHERE family_id = ?", (family_id,))
        conn.execute("DELETE FROM access_tokens WHERE family_id = ?", (family_id,))
        conn.execute("DELETE FROM refresh_tokens WHERE family_id = ?", (family_id,))

    def revoke_family(self, family_id: str) -> None:
        with self._tx() as conn:
            self._revoke_family(conn, family_id)

    def revoke_token(self, token: str) -> None:
        """Revoke the family an access or refresh token belongs to. Unknown tokens: nothing happens."""
        with self._tx() as conn:
            for table in ("access_tokens", "refresh_tokens"):
                row = conn.execute(f"SELECT family_id FROM {table} WHERE token_hash = ?",
                                   (_h(token),)).fetchone()
                if row:
                    self._revoke_family(conn, row["family_id"])
                    return

    def revoke_all(self) -> None:
        """Emergency stop: forget every client, login, code and token. The lockout counter stays."""
        with self._tx() as conn:
            for table in _TABLES:
                if table != "login_failures":
                    conn.execute(f"DELETE FROM {table}")
