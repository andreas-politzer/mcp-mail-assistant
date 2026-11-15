"""SQLite storage for drafts and the audit log.

Database triggers enforce the important rules (immutable draft content, allowed status
changes, append-only audit log), so a bug in the calling code cannot silently break them.
This protects against programming errors, not against someone who can write to the
database file: that person can drop the triggers or delete the file.
"""

from __future__ import annotations

import hashlib
import os
import secrets
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

from ..errors import ConfigError, DraftNotFound, DraftNotSendable, RateLimitExceeded, ValidationError
from ..security.attachments import Attachment, key_parts, path_key
from ..security.rate_limiter import (
    DAY, HARD_MAX_SENDS_PER_DAY, HARD_MAX_SENDS_PER_HOUR, HOUR, RateLimits, clamp,
    prepare_limit_reached, send_limit_reason,
)

_FINAL = ("sent", "failed", "unknown", "expired", "cancelled")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    owner TEXT NOT NULL,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    status TEXT NOT NULL CHECK (status IN
        ('pending','sending','sent','failed','unknown','expired','cancelled')),
    recipient_name TEXT NOT NULL,
    recipient_address TEXT NOT NULL,
    subject TEXT NOT NULL,
    body TEXT NOT NULL,
    body_sha256 TEXT NOT NULL,
    sending_at REAL,
    finished_at REAL,
    detail TEXT
);

CREATE TABLE IF NOT EXISTS audit_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    event TEXT NOT NULL,
    draft_id TEXT,
    owner TEXT,
    recipient_address TEXT,
    subject TEXT,
    body_sha256 TEXT,
    detail TEXT
);
CREATE INDEX IF NOT EXISTS audit_event_ts ON audit_log(event, ts);

CREATE TRIGGER IF NOT EXISTS drafts_content_immutable
BEFORE UPDATE ON drafts
WHEN NEW.id IS NOT OLD.id OR NEW.owner IS NOT OLD.owner
  OR NEW.created_at IS NOT OLD.created_at OR NEW.expires_at IS NOT OLD.expires_at
  OR NEW.recipient_name IS NOT OLD.recipient_name
  OR NEW.recipient_address IS NOT OLD.recipient_address
  OR NEW.subject IS NOT OLD.subject OR NEW.body IS NOT OLD.body
  OR NEW.body_sha256 IS NOT OLD.body_sha256
BEGIN
    SELECT RAISE(ABORT, 'draft content is immutable');
END;

CREATE TRIGGER IF NOT EXISTS drafts_final_is_final
BEFORE UPDATE ON drafts
WHEN OLD.status IN ('sent','failed','unknown','expired','cancelled')
BEGIN
    SELECT RAISE(ABORT, 'draft is in a final state');
END;

CREATE TRIGGER IF NOT EXISTS drafts_valid_transition
BEFORE UPDATE ON drafts
WHEN NEW.status IS NOT OLD.status AND NOT (
       (OLD.status = 'pending' AND NEW.status IN ('sending','expired','cancelled'))
    OR (OLD.status = 'sending' AND NEW.status IN ('sent','failed','unknown')))
BEGIN
    SELECT RAISE(ABORT, 'invalid status transition');
END;

CREATE TRIGGER IF NOT EXISTS drafts_no_delete_active
BEFORE DELETE ON drafts
WHEN OLD.status IN ('pending','sending')
BEGIN
    SELECT RAISE(ABORT, 'active drafts cannot be deleted');
END;

CREATE TABLE IF NOT EXISTS draft_attachments (
    draft_id TEXT NOT NULL,
    position INTEGER NOT NULL,
    name TEXT NOT NULL,
    data BLOB NOT NULL,
    size_bytes INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    PRIMARY KEY (draft_id, position)
);

CREATE TRIGGER IF NOT EXISTS attachments_immutable
BEFORE UPDATE ON draft_attachments
BEGIN
    SELECT RAISE(ABORT, 'attachments are immutable');
END;

CREATE TRIGGER IF NOT EXISTS attachments_keep_while_active
BEFORE DELETE ON draft_attachments
WHEN EXISTS (SELECT 1 FROM drafts WHERE id = OLD.draft_id AND status IN ('pending','sending'))
BEGIN
    SELECT RAISE(ABORT, 'attachments of active drafts cannot be deleted');
END;

CREATE TRIGGER IF NOT EXISTS attachments_only_for_pending
BEFORE INSERT ON draft_attachments
WHEN NOT EXISTS (SELECT 1 FROM drafts WHERE id = NEW.draft_id AND status = 'pending')
BEGIN
    SELECT RAISE(ABORT, 'attachments can only be added to a pending draft');
END;

CREATE TABLE IF NOT EXISTS recipients (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    address TEXT NOT NULL,
    address_key TEXT NOT NULL UNIQUE,
    status TEXT NOT NULL CHECK (status IN
        ('suggested','active','rejected','removed','expired','blocked')),
    aliases TEXT NOT NULL DEFAULT '[]',
    note TEXT NOT NULL DEFAULT '',
    suggested_by TEXT,
    created_at REAL NOT NULL,
    decided_at REAL
);

CREATE TRIGGER IF NOT EXISTS recipients_no_delete
BEFORE DELETE ON recipients
BEGIN
    SELECT RAISE(ABORT, 'recipient history is kept');
END;

CREATE TABLE IF NOT EXISTS file_access_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    scope TEXT NOT NULL CHECK (scope IN ('attach','read')),
    root_path TEXT NOT NULL,
    path_key TEXT NOT NULL,
    path_display TEXT NOT NULL,
    effect TEXT NOT NULL CHECK (effect IN ('allow','deny','cleared')),
    changed_at REAL NOT NULL,
    changed_by TEXT NOT NULL,
    UNIQUE (scope, root_path, path_key)
);

CREATE TRIGGER IF NOT EXISTS file_access_rules_no_delete
BEFORE DELETE ON file_access_rules
BEGIN
    SELECT RAISE(ABORT, 'rule history is kept');
END;

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    changed_at REAL NOT NULL,
    changed_by TEXT NOT NULL
);

CREATE TRIGGER IF NOT EXISTS audit_no_update
BEFORE UPDATE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;

CREATE TRIGGER IF NOT EXISTS audit_no_delete
BEFORE DELETE ON audit_log
BEGIN
    SELECT RAISE(ABORT, 'audit log is append-only');
END;
"""


@dataclass(frozen=True)
class Draft:
    id: str
    owner: str
    created_at: float
    expires_at: float
    status: str
    recipient_name: str
    recipient_address: str
    subject: str
    body: str
    body_sha256: str
    sending_at: Optional[float]
    finished_at: Optional[float]
    detail: Optional[str]

    @property
    def fingerprint(self) -> str:
        return self.body_sha256[:12]


def body_hash(subject: str, body: str, address: str, attachments: Sequence[Attachment] = ()) -> str:
    """Fingerprint of everything the user confirms. Without attachments the value is the same
    as before attachments existed."""
    h = hashlib.sha256()
    for part in (address, subject, body):
        h.update(part.encode("utf-8"))
        h.update(b"\x00")
    for attachment in attachments:
        h.update(b"attachment\x00" + attachment.name.encode("utf-8") + b"\x00")
        h.update(attachment.sha256.encode("ascii") + b"\x00")
    return h.hexdigest()


class Database:
    # Version 1: drafts, audit log and draft_attachments. Version 2: the recipient registry. Version 3: the origin path of
    # an attachment (checked again when sending). Version 4: file_access_rules and settings. The schema script only
    # creates what is missing, so opening an older database upgrades it without touching existing rows.
    SCHEMA_VERSION = 4

    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self._restrict_permissions()
        with self._connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            version = conn.execute("PRAGMA user_version").fetchone()[0]
            if version > self.SCHEMA_VERSION:
                raise ConfigError(
                    "The mail database was written by a newer version of outbox-mcp. "
                    "Update the program instead of opening it with this one."
                )
            conn.executescript(_SCHEMA)
            columns = [r[1] for r in conn.execute("PRAGMA table_info(draft_attachments)")]
            if "source_path" not in columns:  # version 3: remember where an attachment came from
                conn.execute("ALTER TABLE draft_attachments ADD COLUMN source_path TEXT")
            if version < self.SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    # -- plumbing -----------------------------------------------------------------

    def _restrict_permissions(self) -> None:
        """Create the file readable by the owner only (0600). SQLite gives its -wal and -shm
        files the same permissions as the main file. Best effort on platforms without chmod."""
        try:
            os.close(os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600))
            os.chmod(self.path, 0o600)
        except OSError:
            pass

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                yield conn
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            else:
                conn.execute("COMMIT")

    @staticmethod
    def _draft(row: sqlite3.Row) -> Draft:
        return Draft(**{k: row[k] for k in row.keys()})

    @staticmethod
    def _audit(conn, now, event, draft: Optional[Draft] = None, draft_id=None, owner=None, detail=None):
        conn.execute(
            "INSERT INTO audit_log(ts,event,draft_id,owner,recipient_address,subject,body_sha256,detail)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (
                now,
                event,
                draft.id if draft else draft_id,
                draft.owner if draft else owner,
                draft.recipient_address if draft else None,
                draft.subject if draft else None,
                draft.body_sha256 if draft else None,
                detail,
            ),
        )

    @staticmethod
    def _count(conn, event: str, since: float) -> int:
        return conn.execute(
            "SELECT COUNT(*) FROM audit_log WHERE event=? AND ts>?", (event, since)
        ).fetchone()[0]

    @staticmethod
    def _load(conn, draft_id: str, owner: str) -> Draft:
        row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
        if row is None or row["owner"] != owner:
            raise DraftNotFound("No such draft. Prepare a new draft with prepare_email.")
        return Database._draft(row)

    # -- operations ---------------------------------------------------------------

    def create_draft(
        self,
        owner: str,
        recipient_name: str,
        recipient_address: str,
        subject: str,
        body: str,
        now: float,
        ttl_seconds: int,
        limits: RateLimits,
        attachments: Sequence[Attachment] = (),
    ) -> Draft:
        with self._tx() as conn:
            if prepare_limit_reached(self._count(conn, "prepared", now - HOUR), limits):
                raise RateLimitExceeded(
                    f"The limit of {limits.max_prepares_per_hour} prepared drafts per hour has been reached."
                )
            draft = Draft(
                id="drf_" + secrets.token_urlsafe(16),
                owner=owner,
                created_at=now,
                expires_at=now + ttl_seconds,
                status="pending",
                recipient_name=recipient_name,
                recipient_address=recipient_address,
                subject=subject,
                body=body,
                body_sha256=body_hash(subject, body, recipient_address, attachments),
                sending_at=None,
                finished_at=None,
                detail=None,
            )
            conn.execute(
                "INSERT INTO drafts(id,owner,created_at,expires_at,status,recipient_name,"
                "recipient_address,subject,body,body_sha256) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    draft.id, owner, now, draft.expires_at, "pending", recipient_name,
                    recipient_address, subject, body, draft.body_sha256,
                ),
            )
            for position, attachment in enumerate(attachments):
                conn.execute(
                    "INSERT INTO draft_attachments(draft_id,position,name,data,size_bytes,sha256,source_path)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (
                        draft.id, position, attachment.name, attachment.data, attachment.size,
                        attachment.sha256, attachment.source,
                    ),
                )
            detail = None
            if attachments:
                detail = "attachments: " + "; ".join(
                    f"{a.name} ({a.size} bytes, sha256 {a.sha256})" for a in attachments
                )
            self._audit(conn, now, "prepared", draft, detail=detail)
            return draft

    def attachments_of(self, draft_id: str) -> List[Attachment]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT name,data,sha256,source_path FROM draft_attachments WHERE draft_id=? ORDER BY position",
                (draft_id,),
            ).fetchall()
        return [
            Attachment(
                name=r["name"], data=bytes(r["data"]), sha256=r["sha256"], source=r["source_path"] or ""
            )
            for r in rows
        ]

    def get_draft(self, draft_id: str, owner: str) -> Draft:
        with self._connect() as conn:
            return self._load(conn, draft_id, owner)

    def expire_stale(self, now: float) -> int:
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM drafts WHERE status='pending' AND expires_at<=?", (now,)
            ).fetchall()
            for row in rows:
                conn.execute("UPDATE drafts SET status='expired', finished_at=? WHERE id=?", (now, row["id"]))
                self._audit(conn, now, "expired", self._draft(row))
            return len(rows)

    def cancel(self, draft_id: str, owner: str, now: float) -> Draft:
        expired = False
        with self._tx() as conn:
            draft = self._load(conn, draft_id, owner)
            if draft.status != "pending":
                raise DraftNotSendable(draft.status)
            if draft.expires_at <= now:
                conn.execute("UPDATE drafts SET status='expired', finished_at=? WHERE id=?", (now, draft.id))
                self._audit(conn, now, "expired", draft)
                expired = True
            else:
                conn.execute("UPDATE drafts SET status='cancelled', finished_at=? WHERE id=?", (now, draft.id))
                self._audit(conn, now, "cancelled", draft)
        if expired:
            raise DraftNotSendable("expired")
        return self.get_draft(draft_id, owner)

    def claim_for_sending(
        self, draft_id: str, owner: str, now: float, limits: RateLimits,
        authorize: Optional[Callable[[Draft], None]] = None,
    ) -> Draft:
        """Atomically move pending -> sending. Only one caller can ever succeed per draft.

        `authorize` (recipient and attachment rules) runs inside the same write transaction, after
        the draft is known to be pending and before it is claimed. While it runs, no other writer
        (for example a rule change by the admin side) can commit, so the rules it sees are the rules
        under which the draft is claimed. If it raises, nothing is claimed."""
        expired = False
        limited: Optional[str] = None
        with self._tx() as conn:
            draft = self._load(conn, draft_id, owner)
            if draft.status != "pending":
                raise DraftNotSendable(draft.status)
            if draft.expires_at <= now:
                conn.execute("UPDATE drafts SET status='expired', finished_at=? WHERE id=?", (now, draft.id))
                self._audit(conn, now, "expired", draft)
                expired = True
            else:
                if authorize is not None:
                    authorize(draft)
                reason = send_limit_reason(
                    self._count(conn, "claimed", now - HOUR),
                    self._count(conn, "claimed", now - DAY),
                    self._effective_limits(conn, limits),
                )
                if reason:
                    self._audit(conn, now, "rate_limited", draft, detail=reason)
                    limited = reason
                else:
                    conn.execute("UPDATE drafts SET status='sending', sending_at=? WHERE id=?", (now, draft.id))
                    self._audit(conn, now, "claimed", draft)
        if expired:
            raise DraftNotSendable("expired")
        if limited:
            raise RateLimitExceeded(limited)
        return self.get_draft(draft_id, owner)

    def finish(self, draft_id: str, status: str, now: float, detail: Optional[str] = None) -> None:
        if status not in ("sent", "failed", "unknown"):
            raise ValueError("invalid final status")
        with self._tx() as conn:
            row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if row is None:
                raise DraftNotFound("No such draft.")
            conn.execute(
                "UPDATE drafts SET status=?, finished_at=?, detail=? WHERE id=?",
                (status, now, detail, draft_id),
            )
            self._audit(conn, now, status, self._draft(row), detail=detail)

    def recover_interrupted(self, now: float) -> int:
        """Drafts stuck in 'sending' (crash, restart) have an unknown result."""
        with self._tx() as conn:
            rows = conn.execute("SELECT * FROM drafts WHERE status='sending'").fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE drafts SET status='unknown', finished_at=?, detail=? WHERE id=?",
                    (now, "service stopped while sending", row["id"]),
                )
                self._audit(conn, now, "unknown", self._draft(row), detail="service stopped while sending")
            return len(rows)

    def purge(self, now: float, retention_days: int) -> int:
        cutoff = now - retention_days * DAY
        old = (
            "status IN ('sent','failed','unknown','expired','cancelled')"
            " AND COALESCE(finished_at, created_at) < ?"
        )
        with self._tx() as conn:
            conn.execute(
                f"DELETE FROM draft_attachments WHERE draft_id IN (SELECT id FROM drafts WHERE {old})",
                (cutoff,),
            )
            cur = conn.execute(f"DELETE FROM drafts WHERE {old}", (cutoff,))
            return cur.rowcount

    # -- send limits ----------------------------------------------------------------

    _LIMIT_KEYS = {"hour": "send_limit_per_hour", "day": "send_limit_per_day"}

    def _effective_limits(self, conn, base: RateLimits) -> RateLimits:
        """The limits that apply right now: the stored values where there are any, otherwise the
        starting values from the settings file, always cut down to the hard ceilings. A stored value
        that cannot be read falls back to the starting value. Read inside the claim transaction, so
        a change made by the admin side is either fully in or fully out for a given send."""
        values = {}
        for name, key in self._LIMIT_KEYS.items():
            row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            try:
                number = int(row["value"]) if row is not None else None
            except ValueError:
                number = None
            values[name] = number
        return clamp(RateLimits(
            max_prepares_per_hour=base.max_prepares_per_hour,
            max_sends_per_hour=base.max_sends_per_hour if values["hour"] is None else values["hour"],
            max_sends_per_day=base.max_sends_per_day if values["day"] is None else values["day"],
        ))

    def send_limits(self, base: RateLimits) -> RateLimits:
        """What the mail service applies: `base` is its own starting value from its settings file."""
        with self._connect() as conn:
            return self._effective_limits(conn, base)

    def limits_are_stored(self) -> bool:
        """True once limits have been set through the admin side."""
        with self._connect() as conn:
            return any(
                conn.execute("SELECT 1 FROM settings WHERE key=?", (k,)).fetchone() for k in self._LIMIT_KEYS.values()
            )

    _REPORTED = "send_limits_start"

    def report_start_limits(self, base: RateLimits, now: float) -> None:
        """The mail service writes down the starting values it runs with (after the ceilings), so the
        admin side can show what really applies instead of guessing from its own settings file. This is
        information only: it is never used to decide a send."""
        eff = clamp(base)
        value = f"{eff.max_sends_per_hour}:{eff.max_sends_per_day}"
        with self._tx() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (self._REPORTED,)).fetchone()
            if row is not None and row["value"] == value:
                return
            conn.execute(
                "INSERT INTO settings(key,value,changed_at,changed_by) VALUES (?,?,?,'mail-service')"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, changed_at=excluded.changed_at,"
                " changed_by=excluded.changed_by",
                (self._REPORTED, value, now),
            )

    def _admin_effective(self, conn) -> Tuple[Optional[Tuple[int, int]], str, Optional[Tuple[int, int]]]:
        """(effective (hour, day) or None, source, reported starting values or None).
        source: 'stored' (set through the admin side), 'mail_service_settings' (the mail service's own
        settings file, as it reported it) or 'unknown' (nothing stored and no report yet)."""
        reported = None
        row = conn.execute("SELECT value FROM settings WHERE key=?", (self._REPORTED,)).fetchone()
        if row is not None:
            try:
                h, _, d = row["value"].partition(":")
                reported = (int(h), int(d))
            except ValueError:
                reported = None
        found = []
        for key in self._LIMIT_KEYS.values():
            r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            try:
                found.append(int(r["value"]) if r is not None else None)
            except ValueError:
                found.append(None)
        any_stored = any(v is not None for v in found)
        hour = found[0] if found[0] is not None else (reported[0] if reported else None)
        day = found[1] if found[1] is not None else (reported[1] if reported else None)
        if hour is None or day is None:
            return None, "unknown", reported
        eff = clamp(RateLimits(0, hour, day))
        return (eff.max_sends_per_hour, eff.max_sends_per_day), ("stored" if any_stored else "mail_service_settings"), reported

    def admin_limits(self) -> Tuple[Optional[Tuple[int, int]], str, Optional[Tuple[int, int]]]:
        with self._connect() as conn:
            return self._admin_effective(conn)

    def send_counts(self, now: float) -> Tuple[int, int]:
        """(claims in the last hour, claims in the last day). Every claim counts, whatever became of
        it afterwards: sent, failed, unknown or still sending. Refused attempts do not count."""
        with self._connect() as conn:
            return self._count(conn, "claimed", now - HOUR), self._count(conn, "claimed", now - DAY)

    def set_send_limits(self, per_hour: int, per_day: int, now: float, changed_by: str) -> Tuple[int, int]:
        """Store new send limits (0 stops sending). Both must be whole numbers from 0 up to the hard
        ceilings. The change and its audit entry are one transaction."""
        for value, ceiling, name in (
            (per_hour, HARD_MAX_SENDS_PER_HOUR, "per hour"), (per_day, HARD_MAX_SENDS_PER_DAY, "per day"),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= ceiling:
                raise ValidationError(f"The limit {name} must be a whole number from 0 to {ceiling}.")
        with self._tx() as conn:
            before, _, _ = self._admin_effective(conn)
            was_hour, was_day = (str(before[0]), str(before[1])) if before else ("unknown", "unknown")
            for key, value in (("send_limit_per_hour", per_hour), ("send_limit_per_day", per_day)):
                conn.execute(
                    "INSERT INTO settings(key,value,changed_at,changed_by) VALUES (?,?,?,?)"
                    " ON CONFLICT(key) DO UPDATE SET value=excluded.value, changed_at=excluded.changed_at,"
                    " changed_by=excluded.changed_by",
                    (key, str(value), now, changed_by),
                )
            self._audit(
                conn, now, "send_limits_set", owner=changed_by,
                detail=f"hour {was_hour} -> {per_hour}, day {was_day} -> {per_day}",
            )
            return self._admin_effective(conn)[0] or (per_hour, per_day)

    def log_event(self, event: str, owner: str, detail: Optional[str], now: float) -> None:
        """Write one entry to the audit log (used by the admin side for sign-ins and similar)."""
        with self._tx() as conn:
            self._audit(conn, now, event, owner=owner, detail=detail)

    def audit_page(self, limit: int, before_seq: Optional[int] = None) -> List[sqlite3.Row]:
        """Newest entries first, without subjects and body hashes (no mail content)."""
        with self._connect() as conn:
            return conn.execute(
                "SELECT seq,ts,event,draft_id,owner,recipient_address,detail FROM audit_log"
                " WHERE (? IS NULL OR seq < ?) ORDER BY seq DESC LIMIT ?",
                (before_seq, before_seq, limit),
            ).fetchall()

    def schema_version(self) -> int:
        with self._connect() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0]

    # -- file access rules ----------------------------------------------------------

    @staticmethod
    def _root_key(root) -> str:
        return os.path.normpath(str(root))

    @staticmethod
    def _rule_path(path: str) -> Tuple[str, str]:
        """Check a relative path for a rule. Returns (display, key)."""
        text = path.strip() if isinstance(path, str) else ""
        parts = text.split("/") if text else []
        if (
            not text or text.startswith(("/", "~")) or text.endswith("/") or "\\" in text
            or any(ord(c) < 32 or ord(c) == 127 for c in text)
            or any(p in ("", ".", "..") for p in parts)
        ):
            raise ValueError("A rule needs a clean relative path below the root folder.")
        return text, path_key(text)

    def set_file_rule(
        self, scope: str, root, path: str, effect: str, now: float, changed_by: str
    ) -> None:
        """Set, change or clear (effect 'cleared') the rule for one path below a root folder.
        The rule and its audit entry are stored in one transaction. Rows are never deleted.
        Only a folder can be allowed: a single .md file can only be blocked."""
        if scope not in ("attach", "read"):
            raise ValueError("unknown scope")
        if effect not in ("allow", "deny", "cleared"):
            raise ValueError("unknown effect")
        display, key = self._rule_path(path)
        if effect == "allow" and key.endswith(".md"):
            raise ValueError("A single file cannot be allowed, only blocked. Allow its folder instead.")
        root_key = self._root_key(root)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT effect FROM file_access_rules WHERE scope=? AND root_path=? AND path_key=?",
                (scope, root_key, key),
            ).fetchone()
            before = row["effect"] if row else "none"
            if row is None:
                conn.execute(
                    "INSERT INTO file_access_rules(scope,root_path,path_key,path_display,effect,changed_at,changed_by)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (scope, root_key, key, display, effect, now, changed_by),
                )
            else:
                conn.execute(
                    "UPDATE file_access_rules SET effect=?, path_display=?, changed_at=?, changed_by=?"
                    " WHERE scope=? AND root_path=? AND path_key=?",
                    (effect, display, now, changed_by, scope, root_key, key),
                )
            self._audit(
                conn, now, "file_rule_set", owner=changed_by,
                detail=f"scope={scope} path={display} {before} -> {effect}",
            )

    def file_rules(self, scope: str, root) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM file_access_rules WHERE scope=? AND root_path=? ORDER BY path_key",
                (scope, self._root_key(root)),
            ).fetchall()

    def rule_lists(self, scope: str, root) -> Tuple[List[Tuple[str, ...]], List[Tuple[str, ...]]]:
        """(allowed, denied) path parts for one root folder. Rules of other root folders are
        never used, and cleared rules do not count."""
        allowed: List[Tuple[str, ...]] = []
        denied: List[Tuple[str, ...]] = []
        for row in self.file_rules(scope, root):
            if row["effect"] == "allow":
                allowed.append(key_parts(row["path_key"]))
            elif row["effect"] == "deny":
                denied.append(key_parts(row["path_key"]))
        return allowed, denied

    def import_env_rules_once(
        self, root, allowed: Sequence[str], denied: Sequence[str], now: float
    ) -> int:
        """Take over the folder lists from the configuration the first time this root folder is
        seen. After that the database decides and the configuration lines are ignored, so a rule
        cleared later does not come back. Returns the number of rules taken over."""
        root_key = self._root_key(root)
        marker = f"file_rules_imported:attach:{root_key}"
        count = 0
        with self._tx() as conn:
            if conn.execute("SELECT 1 FROM settings WHERE key=?", (marker,)).fetchone():
                return 0
            for effect, folders in (("deny", denied), ("allow", allowed)):
                for folder in folders:
                    try:
                        display, key = self._rule_path(folder)
                    except ValueError:
                        continue
                    if effect == "allow" and key.endswith(".md"):
                        continue
                    cur = conn.execute(
                        "INSERT OR IGNORE INTO file_access_rules"
                        "(scope,root_path,path_key,path_display,effect,changed_at,changed_by)"
                        " VALUES ('attach',?,?,?,?,?,'system')",
                        (root_key, key, display, effect, now),
                    )
                    if cur.rowcount:
                        count += 1
                        self._audit(
                            conn, now, "file_rule_set", owner="system",
                            detail=f"scope=attach path={display} none -> {effect} (from configuration)",
                        )
            conn.execute(
                "INSERT INTO settings(key,value,changed_at,changed_by) VALUES (?,?,?,'system')",
                (marker, "1", now),
            )
        return count

    # -- reading helpers (also used by tests) -------------------------------------

    def count_events(self, event: str, since: float = 0.0) -> int:
        with self._connect() as conn:
            return self._count(conn, event, since)

    def audit_events(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM audit_log ORDER BY seq").fetchall()

