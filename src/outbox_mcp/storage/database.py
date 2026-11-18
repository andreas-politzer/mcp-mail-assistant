"""SQLite storage for drafts and the audit log.

Database triggers enforce the important rules (immutable draft content, allowed status
changes, append-only audit log), so a bug in the calling code cannot silently break them.
This protects against programming errors, not against someone who can write to the
database file: that person can drop the triggers or delete the file.
"""

from __future__ import annotations

import hashlib
import os
import random
import secrets
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator, List, Optional, Sequence, Tuple

from .. import sources as src_rules
from ..errors import (
    ConfigError, DraftNotFound, DraftNotSendable, RateLimitExceeded, SourceRejected, ValidationError,
)
from ..security.attachments import Attachment, key_parts, path_key, text_problem
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
    decided_at REAL,
    scope TEXT NOT NULL DEFAULT 'external' CHECK (scope IN ('own','external'))
);

CREATE TRIGGER IF NOT EXISTS recipients_no_delete
BEFORE DELETE ON recipients
BEGIN
    SELECT RAISE(ABORT, 'recipient history is kept');
END;

-- Version 7: approval outside the chat. The passkey proves that the person who holds the device
-- approved exactly this draft; nothing here can be created through the AI connector.
CREATE TABLE IF NOT EXISTS passkey_credentials (
    credential_id TEXT PRIMARY KEY,
    public_key BLOB NOT NULL,
    sign_count INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    last_used_at REAL
);

CREATE TABLE IF NOT EXISTS passkey_enrollments (
    token_hash TEXT PRIMARY KEY,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL
);

CREATE TABLE IF NOT EXISTS passkey_challenges (
    challenge_id TEXT PRIMARY KEY,
    purpose TEXT NOT NULL CHECK (purpose IN ('enroll','approve')),
    draft_id TEXT,
    body_sha256 TEXT,
    enrollment_hash TEXT,
    created_at REAL NOT NULL,
    expires_at REAL NOT NULL,
    consumed_at REAL
);

CREATE TABLE IF NOT EXISTS draft_approvals (
    draft_id TEXT PRIMARY KEY,
    body_sha256 TEXT NOT NULL,
    credential_id TEXT NOT NULL,
    approved_at REAL NOT NULL
);

CREATE TRIGGER IF NOT EXISTS draft_approvals_no_update
BEFORE UPDATE ON draft_approvals
BEGIN
    SELECT RAISE(ABORT, 'an approval cannot be changed');
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

CREATE TABLE IF NOT EXISTS file_sources (
    id TEXT PRIMARY KEY,
    kind TEXT NOT NULL CHECK (kind IN ('server_folder','upload')),
    scope TEXT NOT NULL CHECK (scope IN ('folder','file')),
    label TEXT NOT NULL,
    label_key TEXT NOT NULL,
    origin_display TEXT NOT NULL DEFAULT '',
    real_path TEXT,
    rule_root TEXT NOT NULL,
    file_key TEXT,
    file_display TEXT,
    status TEXT NOT NULL CHECK (status IN ('active','removed')),
    sync_epoch INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL,
    created_by TEXT NOT NULL,
    removed_at REAL,
    removed_by TEXT,
    CHECK (kind = 'upload' OR (real_path IS NOT NULL AND scope = 'folder')),
    CHECK (kind = 'server_folder' OR real_path IS NULL),
    CHECK (scope = 'folder' OR file_key IS NOT NULL)
);

CREATE UNIQUE INDEX IF NOT EXISTS file_sources_active_label
ON file_sources(label_key) WHERE status = 'active';

CREATE UNIQUE INDEX IF NOT EXISTS file_sources_active_path
ON file_sources(real_path) WHERE status = 'active' AND real_path IS NOT NULL;

CREATE TRIGGER IF NOT EXISTS file_sources_no_delete
BEFORE DELETE ON file_sources
BEGIN
    SELECT RAISE(ABORT, 'source history is kept');
END;

CREATE TRIGGER IF NOT EXISTS file_sources_stay_removed
BEFORE UPDATE ON file_sources
WHEN OLD.status = 'removed' AND NEW.status IS NOT 'removed'
BEGIN
    SELECT RAISE(ABORT, 'a removed source stays removed');
END;

CREATE TRIGGER IF NOT EXISTS file_sources_identity_fixed
BEFORE UPDATE ON file_sources
WHEN NEW.id IS NOT OLD.id OR NEW.kind IS NOT OLD.kind OR NEW.scope IS NOT OLD.scope
  OR NEW.real_path IS NOT OLD.real_path OR NEW.rule_root IS NOT OLD.rule_root
  OR NEW.file_key IS NOT OLD.file_key
BEGIN
    SELECT RAISE(ABORT, 'the identity of a source cannot change');
END;

CREATE TABLE IF NOT EXISTS stored_files (
    source_id TEXT NOT NULL,
    path_key TEXT NOT NULL,
    path_display TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('current','purged','removed')),
    purged_reason TEXT,
    version INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    content BLOB,
    uploaded_at REAL NOT NULL,
    changed_at REAL NOT NULL,
    PRIMARY KEY (source_id, path_key),
    CHECK (
        (state = 'current' AND content IS NOT NULL AND purged_reason IS NULL)
        OR (state = 'purged' AND content IS NULL AND purged_reason IN ('blocked','source_removed'))
        OR (state = 'removed' AND content IS NULL AND purged_reason = 'removed_local')
    )
);

CREATE TRIGGER IF NOT EXISTS stored_files_no_delete
BEFORE DELETE ON stored_files
BEGIN
    SELECT RAISE(ABORT, 'stored file history is kept');
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
    # an attachment (checked again when sending). Version 4: file_access_rules and settings. Version 5: file sources
    # (file_sources, stored_files) and the source id on rules and on the origin of an attachment. Version 6: the scope of
    # a recipient (own or external); active recipients that exist at the upgrade become own. Version 7: the approval
    # outside the chat (passkeys, one-time enrollment links, challenges, approvals). The schema script only
    # creates what is missing, so opening an older database upgrades it without touching existing rows.
    SCHEMA_VERSION = 7

    def __init__(self, path: Path) -> None:
        self.path = str(path)
        self._restrict_permissions()
        # Several processes may open a brand-new database at the same moment (Claude Desktop starts
        # the server twice). For a few steps, such as switching to WAL, SQLite then answers
        # "database is locked" at once instead of waiting, and two processes can race on adding a
        # column. Every step is safe to repeat, so the whole start-up is simply tried again.
        deadline = time.monotonic() + 15
        while True:
            try:
                self._initialise()
                return
            except sqlite3.OperationalError as exc:
                message = str(exc).lower()
                # "duplicate column": another process added the column between our check and our ALTER
                if ("locked" not in message and "duplicate column" not in message) or time.monotonic() > deadline:
                    raise
                time.sleep(0.05 + random.random() * 0.1)

    def _initialise(self) -> None:
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
            # version 5: which source a rule or an attachment origin belongs to (empty for older rows)
            for table, column in (
                ("file_access_rules", "source_id"),
                ("draft_attachments", "source_id"),
                ("draft_attachments", "origin_key"),
            ):
                if column not in [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} TEXT")
            self._migrate_recipient_scope(conn)
            if version < self.SCHEMA_VERSION:
                conn.execute(f"PRAGMA user_version = {self.SCHEMA_VERSION}")

    @staticmethod
    def _migrate_recipient_scope(conn: sqlite3.Connection) -> None:
        """Version 6: every recipient is either 'own' (an address of the operator) or 'external'.

        Recipients that exist when the column is added were put on the list by the operator, so
        the active ones become 'own'. Everything else, and everything added later, is 'external'.
        The check and the change happen under one write lock, so two processes that open the
        database at the same time cannot both run the migration."""
        conn.execute("BEGIN IMMEDIATE")
        try:
            columns = [r[1] for r in conn.execute("PRAGMA table_info(recipients)")]
            if "scope" not in columns:
                conn.execute(
                    "ALTER TABLE recipients ADD COLUMN scope TEXT NOT NULL DEFAULT 'external' "
                    "CHECK (scope IN ('own','external'))"
                )
                conn.execute("UPDATE recipients SET scope='own' WHERE status='active'")
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise

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
                    "INSERT INTO draft_attachments(draft_id,position,name,data,size_bytes,sha256,source_path,"
                    "source_id,origin_key) VALUES (?,?,?,?,?,?,?,?,?)",
                    (
                        draft.id, position, attachment.name, attachment.data, attachment.size,
                        attachment.sha256, attachment.source, attachment.source_id or None,
                        attachment.origin_key or None,
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
                "SELECT name,data,sha256,source_path,source_id,origin_key FROM draft_attachments"
                " WHERE draft_id=? ORDER BY position",
                (draft_id,),
            ).fetchall()
        return [
            Attachment(
                name=r["name"], data=bytes(r["data"]), sha256=r["sha256"], source=r["source_path"] or "",
                source_id=r["source_id"] or "", origin_key=r["origin_key"] or "",
            )
            for r in rows
        ]

    def get_draft(self, draft_id: str, owner: str) -> Draft:
        with self._connect() as conn:
            return self._load(conn, draft_id, owner)

    def owner_of(self, draft_id: str) -> Optional[str]:
        with self._connect() as conn:
            row = conn.execute("SELECT owner FROM drafts WHERE id=?", (draft_id,)).fetchone()
        return row["owner"] if row else None

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

    def recover_interrupted(self, now: float, older_than: float = 0.0) -> int:
        """Drafts stuck in 'sending' (crash, restart) have an unknown result.

        `older_than` leaves alone sends that began less than that many seconds ago: another
        server process on the same database may be in the middle of one (Claude Desktop can run
        more than one instance), and a send that is still running must not be marked unknown."""
        with self._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM drafts WHERE status='sending' AND COALESCE(sending_at, 0) <= ?",
                (now - older_than,),
            ).fetchall()
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
            for table in ("draft_approvals", "passkey_challenges"):
                conn.execute(
                    f"DELETE FROM {table} WHERE draft_id IN (SELECT id FROM drafts WHERE {old})", (cutoff,)
                )
            # challenges and enrollment links that ran out long ago are of no use to anyone
            conn.execute("DELETE FROM passkey_challenges WHERE expires_at < ?", (now - DAY,))
            conn.execute("DELETE FROM passkey_enrollments WHERE expires_at < ?", (now - DAY,))
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

    # Which audit events belong to which activity type (the admin app filters by these).
    AUDIT_TYPES = {
        "recipients": ("recipient_%",),
        "files": ("file_rule_set", "source_rule_set", "stored_file_%", "stored_files_purged"),
        "limits": ("send_limits_%",),
        "sources": ("source_created", "source_renamed", "source_removed", "source_synced"),
        "sends": ("prepared", "claimed", "sent", "failed", "unknown", "expired", "cancelled", "rate_limited",
                  "draft_%"),
        "settings": ("setting_changed",),
        "access": ("admin_%",),
    }

    def audit_page(
        self, limit: int, before_seq: Optional[int] = None, types: Optional[Sequence[str]] = None,
        since: Optional[float] = None,
    ) -> List[sqlite3.Row]:
        """Newest entries first, without subjects and body hashes (no mail content). `types` are names
        from AUDIT_TYPES (several allowed), `since` a time stamp; all filters combine."""
        where, args = ["(? IS NULL OR seq < ?)"], [before_seq, before_seq]
        if since is not None:
            where.append("ts >= ?")
            args.append(since)
        if types:
            unknown = [t for t in types if t not in self.AUDIT_TYPES]
            if unknown:
                raise ValidationError(f"Unknown activity type: {unknown[0][:40]}.")
            clauses = []
            for t in types:
                for pat in self.AUDIT_TYPES[t]:
                    if pat.endswith("%"):
                        clauses.append("substr(event,1,?)=?")
                        args.extend([len(pat) - 1, pat[:-1]])
                    else:
                        clauses.append("event=?")
                        args.append(pat)
            where.append("(" + " OR ".join(clauses) + ")")
        with self._connect() as conn:
            return conn.execute(
                "SELECT seq,ts,event,draft_id,owner,recipient_address,detail FROM audit_log WHERE "
                + " AND ".join(where) + " ORDER BY seq DESC LIMIT ?",
                (*args, limit),
            ).fetchall()

    # -- candidate expiry (setting) ---------------------------------------------------

    EXPIRY_KEY = "candidate_expiry_days"
    _EXPIRY_REPORTED = "candidate_expiry_start"
    EXPIRY_MIN, EXPIRY_MAX = 1, 365

    @staticmethod
    def _int_setting(conn, key: str) -> Optional[int]:
        row = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        try:
            return int(row["value"]) if row is not None else None
        except ValueError:
            return None

    def candidate_expiry_days(self, default: int) -> int:
        """What the mail service applies: the stored value, else its own starting value; always inside
        the allowed range."""
        with self._connect() as conn:
            value = self._int_setting(conn, self.EXPIRY_KEY)
        value = default if value is None else value
        return min(max(value, self.EXPIRY_MIN), self.EXPIRY_MAX)

    def report_candidate_expiry(self, days: int, now: float) -> None:
        """Information for the admin side only: the starting value the mail service runs with."""
        value = str(min(max(days, self.EXPIRY_MIN), self.EXPIRY_MAX))
        with self._tx() as conn:
            row = conn.execute("SELECT value FROM settings WHERE key=?", (self._EXPIRY_REPORTED,)).fetchone()
            if row is not None and row["value"] == value:
                return
            conn.execute(
                "INSERT INTO settings(key,value,changed_at,changed_by) VALUES (?,?,?,'mail-service')"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, changed_at=excluded.changed_at,"
                " changed_by=excluded.changed_by",
                (self._EXPIRY_REPORTED, value, now),
            )

    def admin_candidate_expiry(self) -> Tuple[Optional[int], str, Optional[int]]:
        """(effective days or None, source 'stored' | 'mail_service_settings' | 'unknown', reported start)."""
        with self._connect() as conn:
            stored = self._int_setting(conn, self.EXPIRY_KEY)
            reported = self._int_setting(conn, self._EXPIRY_REPORTED)
        if stored is not None:
            return min(max(stored, self.EXPIRY_MIN), self.EXPIRY_MAX), "stored", reported
        if reported is not None:
            return reported, "mail_service_settings", reported
        return None, "unknown", None

    def set_candidate_expiry(self, days: int, now: float, changed_by: str) -> int:
        if isinstance(days, bool) or not isinstance(days, int) or not self.EXPIRY_MIN <= days <= self.EXPIRY_MAX:
            raise ValidationError(f"The expiry must be a whole number of days from {self.EXPIRY_MIN} to {self.EXPIRY_MAX}.")
        with self._tx() as conn:
            before = self._int_setting(conn, self.EXPIRY_KEY)
            reported = self._int_setting(conn, self._EXPIRY_REPORTED)
            was = before if before is not None else reported
            conn.execute(
                "INSERT INTO settings(key,value,changed_at,changed_by) VALUES (?,?,?,?)"
                " ON CONFLICT(key) DO UPDATE SET value=excluded.value, changed_at=excluded.changed_at,"
                " changed_by=excluded.changed_by",
                (self.EXPIRY_KEY, str(days), now, changed_by),
            )
            self._audit(
                conn, now, "setting_changed", owner=changed_by,
                detail=f"candidate_expiry_days {was if was is not None else 'unknown'} -> {days}",
            )
        return days

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
            if scope == "attach":
                src = conn.execute(
                    "SELECT id FROM file_sources WHERE rule_root=? AND status='active'", (root_key,)
                ).fetchone()
                if src is not None:
                    self._after_rules_changed(conn, src["id"], now, changed_by)

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

    # -- file sources (schema 5) ----------------------------------------------------
    # A source is a place Claude may attach files from (see outbox_mcp/sources.py). Rules, stored
    # copies and the audit entry of every change are written in one transaction. Rules of one source
    # never apply to another; removing a source keeps its rules as history that is never effective.

    @staticmethod
    def _active_source(conn, source_id: str) -> sqlite3.Row:
        row = conn.execute("SELECT * FROM file_sources WHERE id=?", (source_id,)).fetchone()
        if row is None:
            raise SourceRejected("state", "This source does not exist.")
        if row["status"] != "active":
            raise SourceRejected("state", "This source has been removed.")
        return row

    @staticmethod
    def _norm_real_path(real_path) -> str:
        text = os.path.normpath(str(real_path))
        if not os.path.isabs(text) or text == os.path.sep:
            raise SourceRejected("input", "A folder source needs an absolute folder path (not '/').")
        return text

    def _insert_source(
        self, conn, kind, scope, label, now, changed_by, origin_display, real_path, file_name, rule_root=None
    ) -> str:
        if kind not in src_rules.SOURCE_KINDS or scope not in src_rules.SOURCE_SCOPES:
            raise SourceRejected("input", "Unknown kind or scope of a source.")
        label_display, label_key = src_rules.clean_label(label)
        origin = src_rules.clean_display(origin_display)
        file_display = file_key = None
        if kind == "server_folder":
            if scope != "folder":
                raise SourceRejected("input", "A server folder source is a folder.")
            if real_path is None:
                raise SourceRejected("input", "A server folder source needs its folder path.")
            real = self._norm_real_path(real_path)
            for other in conn.execute(
                "SELECT real_path FROM file_sources WHERE status='active' AND real_path IS NOT NULL"
            ):
                o = other["real_path"]
                if real == o or real.startswith(o + os.sep) or o.startswith(real + os.sep):
                    raise SourceRejected("input", "This folder overlaps with a folder that is already a source.")
        else:
            if real_path is not None:
                raise SourceRejected("input", "An upload source has no folder path on the server.")
            real = None
        if scope == "file":
            if file_name is None:
                raise SourceRejected("input", "A single-file source needs the file name.")
            file_display, file_key = src_rules.clean_relative_path(file_name)
            if "/" in file_display:
                raise SourceRejected("input", "A single-file source is registered by its file name only.")
            src_rules.check_stored_name(kind, file_display)
        elif file_name is not None:
            raise SourceRejected("input", "Only a single-file source has a file name.")
        active = conn.execute("SELECT COUNT(*) FROM file_sources WHERE status='active'").fetchone()[0]
        if active >= src_rules.MAX_ACTIVE_SOURCES:
            raise SourceRejected("quota", f"At most {src_rules.MAX_ACTIVE_SOURCES} sources can be active.")
        source_id = src_rules.new_source_id(secrets.token_hex(8))
        try:
            conn.execute(
                "INSERT INTO file_sources(id,kind,scope,label,label_key,origin_display,real_path,rule_root,"
                "file_key,file_display,status,sync_epoch,created_at,created_by)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,'active',0,?,?)",
                (
                    source_id, kind, scope, label_display, label_key, origin, real,
                    rule_root or f"source:{source_id}", file_key, file_display, now, changed_by,
                ),
            )
        except sqlite3.IntegrityError:
            raise SourceRejected("input", "A source with this name or folder already exists.") from None
        self._audit(
            conn, now, "source_created", owner=changed_by,
            detail=f"source={source_id} label={label_display} kind={kind} scope={scope}",
        )
        return source_id

    def create_source(
        self, kind: str, scope: str, label: str, now: float, changed_by: str, *,
        origin_display: str = "", real_path=None, file_name: Optional[str] = None,
    ) -> str:
        """Register a new source. It allows nothing at first: a folder source needs a rule that
        allows a folder, a single-file source allows only its one file."""
        with self._tx() as conn:
            return self._insert_source(
                conn, kind, scope, label, now, changed_by, origin_display, real_path, file_name
            )

    def ensure_server_folder_source(self, root, now: float, label: str = "notes") -> str:
        """The notes folder from the configuration as a source (the migration from schema 4).
        Existing rules of that folder become the rules of this source; nothing is copied or moved.
        Calling it again returns the same source. A source that was removed once is never
        revived: registering the same folder again gives a new source without the old rules."""
        root_key = self._root_key(root)
        with self._tx() as conn:
            row = conn.execute(
                "SELECT id FROM file_sources WHERE status='active' AND real_path=?", (root_key,)
            ).fetchone()
            if row:
                return row["id"]
            legacy_free = conn.execute(
                "SELECT 1 FROM file_sources WHERE rule_root=?", (root_key,)
            ).fetchone() is None
            name, n = label, 1
            while conn.execute(
                "SELECT 1 FROM file_sources WHERE status='active' AND label_key=?",
                (src_rules.clean_label(name)[1],),
            ).fetchone():
                n += 1
                name = f"{label}-{n}"
            source_id = self._insert_source(
                conn, "server_folder", "folder", name, now, "system", "", root_key, None,
                rule_root=root_key if legacy_free else None,
            )
            if legacy_free:
                conn.execute(
                    "UPDATE file_access_rules SET source_id=? WHERE scope='attach' AND root_path=?"
                    " AND source_id IS NULL",
                    (source_id, root_key),
                )
            return source_id

    def get_source(self, source_id: str) -> Optional[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM file_sources WHERE id=?", (source_id,)).fetchone()

    def list_sources(self, include_removed: bool = False) -> List[sqlite3.Row]:
        with self._connect() as conn:
            where = "" if include_removed else " WHERE status='active'"
            return conn.execute(f"SELECT * FROM file_sources{where} ORDER BY created_at, id").fetchall()

    def source_epoch(self, source_id: str) -> Optional[int]:
        row = self.get_source(source_id)
        return None if row is None else row["sync_epoch"]

    @staticmethod
    def _source_rule_lists(conn, src: sqlite3.Row):
        """(allowed, denied) path parts of one source. Nothing is effective for a removed source."""
        if src["status"] != "active":
            return [], []
        allowed, denied = [], []
        for row in conn.execute(
            "SELECT path_key, effect FROM file_access_rules WHERE scope='attach' AND root_path=?"
            " AND (source_id=? OR source_id IS NULL)",
            (src["rule_root"], src["id"]),
        ):
            if row["effect"] == "allow":
                allowed.append(key_parts(row["path_key"]))
            elif row["effect"] == "deny":
                denied.append(key_parts(row["path_key"]))
        return allowed, denied

    def source_rule_lists(self, source_id: str):
        with self._connect() as conn:
            src = conn.execute("SELECT * FROM file_sources WHERE id=?", (source_id,)).fetchone()
            return ([], []) if src is None else self._source_rule_lists(conn, src)

    def _path_state(self, conn, src: sqlite3.Row, key: str) -> str:
        if src["status"] != "active":
            return "none"
        allowed, denied = self._source_rule_lists(conn, src)
        return src_rules.evaluate(src["scope"], src["file_key"], allowed, denied, key)

    def source_path_state(self, source_id: str, path: str) -> str:
        """'allowed', 'denied' or 'none' for a relative path inside one source, as the rules are now."""
        _, key = src_rules.clean_relative_path(path)
        with self._connect() as conn:
            src = conn.execute("SELECT * FROM file_sources WHERE id=?", (source_id,)).fetchone()
            return "none" if src is None else self._path_state(conn, src, key)

    @staticmethod
    def _purge(conn, source_id: str, keys: Sequence[str], reason: str, now: float) -> int:
        count = 0
        for key in keys:
            cur = conn.execute(
                "UPDATE stored_files SET state='purged', purged_reason=?, content=NULL, changed_at=?"
                " WHERE source_id=? AND path_key=? AND state='current'",
                (reason, now, source_id, key),
            )
            count += cur.rowcount
        return count

    def _purge_unallowed(self, conn, src: sqlite3.Row, now: float) -> int:
        keys = [
            r["path_key"]
            for r in conn.execute(
                "SELECT path_key FROM stored_files WHERE source_id=? AND state='current'", (src["id"],)
            )
            if self._path_state(conn, src, r["path_key"]) != "allowed"
        ]
        return self._purge(conn, src["id"], keys, "blocked", now)

    def _attachment_ok(self, conn, source_id, source_path, sha256) -> bool:
        """The one check of a draft attachment against the rules as they are now: the source is
        active, the path text is clean and of an allowed type, the rules allow it and, for an upload
        source, the stored copy is still current and is exactly the version the draft holds.
        Used to cancel open drafts when something is blocked and again when sending."""
        if not source_id or not source_path:
            return False
        src = conn.execute("SELECT * FROM file_sources WHERE id=?", (source_id,)).fetchone()
        if src is None or src["status"] != "active":
            return False
        try:
            display, key = src_rules.clean_relative_path(source_path)
            src_rules.check_stored_name(src["kind"], display)
        except SourceRejected:
            return False
        if self._path_state(conn, src, key) != "allowed":
            return False
        if src["kind"] == "upload":
            row = conn.execute(
                "SELECT sha256 FROM stored_files WHERE source_id=? AND path_key=? AND state='current'",
                (source_id, key),
            ).fetchone()
            return row is not None and row["sha256"] == sha256
        return True

    def attachment_still_allowed(self, attachment: Attachment) -> bool:
        with self._connect() as conn:
            return self._attachment_ok(conn, attachment.source_id, attachment.source, attachment.sha256)

    def find_source_by_label(self, label: str) -> Optional[sqlite3.Row]:
        try:
            _, key = src_rules.clean_label(label)
        except SourceRejected:
            return None
        with self._connect() as conn:
            return conn.execute(
                "SELECT * FROM file_sources WHERE status='active' AND label_key=?", (key,)
            ).fetchone()

    def _after_rules_changed(self, conn, source_id: str, now: float, changed_by: str) -> dict:
        """Everything that must follow a rule change or a removal, inside the same transaction:
        stored copies that are not allowed any more are deleted; open drafts that hold such a file
        are cancelled (and their copies deleted); copies in ended drafts that were never sent
        (failed, expired, cancelled) are deleted too. Sent and unknown drafts keep their copies until
        the normal retention ends: they are the record of what went out."""
        src = conn.execute("SELECT * FROM file_sources WHERE id=?", (source_id,)).fetchone()
        purged = self._purge_unallowed(conn, src, now)
        rows = conn.execute(
            "SELECT da.draft_id, da.position, da.sha256, da.source_path, d.status FROM draft_attachments da"
            " JOIN drafts d ON d.id=da.draft_id WHERE da.source_id=?",
            (source_id,),
        ).fetchall()
        bad = [r for r in rows if not self._attachment_ok(conn, source_id, r["source_path"], r["sha256"])]
        cancelled = sorted({r["draft_id"] for r in bad if r["status"] == "pending"})
        deleted = 0
        for draft_id in cancelled:
            row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            conn.execute(
                "UPDATE drafts SET status='cancelled', finished_at=?, detail=? WHERE id=? AND status='pending'",
                (now, "an attachment was blocked or removed", draft_id),
            )
            self._audit(
                conn, now, "draft_cancelled_by_block", self._draft(row),
                detail=f"source={source_id} label={src['label']}",
            )
            deleted += conn.execute("DELETE FROM draft_attachments WHERE draft_id=?", (draft_id,)).rowcount
        for r in bad:
            if r["status"] in ("failed", "expired", "cancelled") and r["draft_id"] not in cancelled:
                deleted += conn.execute(
                    "DELETE FROM draft_attachments WHERE draft_id=? AND position=?",
                    (r["draft_id"], r["position"]),
                ).rowcount
        if deleted:
            self._audit(
                conn, now, "draft_copies_deleted", owner=changed_by,
                detail=f"source={source_id} label={src['label']} count={deleted}",
            )
        return {"purged_files": purged, "cancelled_drafts": len(cancelled), "deleted_copies": deleted}

    def set_source_rule(
        self, source_id: str, path: str, effect: str, now: float, changed_by: str, whole: bool = False
    ) -> dict:
        """Set, change or clear (effect 'cleared') a rule inside one source. Returns what the change
        removed (see _after_rules_changed). The rule, everything it removes and the audit entries are
        written in one transaction. Rows are never deleted. Only a folder can be
        allowed: a single file can only be blocked (its registration is the allowance)."""
        if effect not in ("allow", "deny", "cleared"):
            raise SourceRejected("input", "Unknown effect of a rule.")
        # whole=True rules the whole source (its main folder) and is never taken from an empty path
        try:
            display, key = ("", "") if whole else self._rule_path(path)
        except ValueError as exc:
            raise SourceRejected("input", str(exc)) from None
        with self._tx() as conn:
            src = self._active_source(conn, source_id)
            if whole and (src["kind"] != "upload" or src["scope"] != "folder"):
                raise SourceRejected("permission", "Only a folder from the Mac can be ruled as a whole.")
            if src["scope"] == "file":
                if key != src["file_key"]:
                    raise SourceRejected("permission", "This source holds one file only; nothing else can be ruled.")
                if effect == "allow":
                    raise SourceRejected(
                        "permission", "A single file is allowed by registering it; it can only be blocked."
                    )
            elif effect == "allow" and src_rules.looks_like_file(key):
                raise SourceRejected(
                    "permission", "A single file cannot be allowed, only blocked. Allow its folder instead."
                )
            row = conn.execute(
                "SELECT effect FROM file_access_rules WHERE scope='attach' AND root_path=? AND path_key=?",
                (src["rule_root"], key),
            ).fetchone()
            before = row["effect"] if row else "none"
            if row is None:
                conn.execute(
                    "INSERT INTO file_access_rules(scope,root_path,path_key,path_display,effect,changed_at,"
                    "changed_by,source_id) VALUES ('attach',?,?,?,?,?,?,?)",
                    (src["rule_root"], key, display, effect, now, changed_by, source_id),
                )
            else:
                conn.execute(
                    "UPDATE file_access_rules SET effect=?, path_display=?, changed_at=?, changed_by=?, source_id=?"
                    " WHERE scope='attach' AND root_path=? AND path_key=?",
                    (effect, display, now, changed_by, source_id, src["rule_root"], key),
                )
            conn.execute("UPDATE file_sources SET sync_epoch=sync_epoch+1 WHERE id=?", (source_id,))
            self._audit(
                conn, now, "source_rule_set", owner=changed_by,
                detail=f"source={source_id} label={src['label']} path={display or '(whole source)'} {before} -> {effect}",
            )
            effects = self._after_rules_changed(conn, source_id, now, changed_by)
            if effects["purged_files"]:
                self._audit(
                    conn, now, "stored_files_purged", owner=changed_by,
                    detail=f"source={source_id} label={src['label']} count={effects['purged_files']} reason=blocked",
                )
            return effects

    def remove_source(self, source_id: str, now: float, changed_by: str) -> dict:
        """Remove a source for good. Its stored copies are deleted, its rules stay as history and are
        never effective again (registering the same thing again makes a new source with no rules).
        Open drafts with a file from it are cancelled. Returns what was removed."""
        with self._tx() as conn:
            src = self._active_source(conn, source_id)
            conn.execute(
                "UPDATE file_sources SET status='removed', removed_at=?, removed_by=?, sync_epoch=sync_epoch+1"
                " WHERE id=?",
                (now, changed_by, source_id),
            )
            keys = [
                r["path_key"]
                for r in conn.execute(
                    "SELECT path_key FROM stored_files WHERE source_id=? AND state='current'", (source_id,)
                )
            ]
            purged = self._purge(conn, source_id, keys, "source_removed", now)
            effects = self._after_rules_changed(conn, source_id, now, changed_by)
            effects["purged_files"] += purged
            self._audit(
                conn, now, "source_removed", owner=changed_by,
                detail=(
                    f"source={source_id} label={src['label']} purged_files={effects['purged_files']}"
                    f" cancelled_drafts={effects['cancelled_drafts']}"
                ),
            )
            return effects

    # -- stored copies ----------------------------------------------------------------

    def _put_stored(self, conn, src: sqlite3.Row, path: str, data, claimed_sha256, now: float):
        """Store one uploaded file inside the transaction `conn` is in. The server checks everything again:
        hash, path, name and type, rules, size, text and secret scan, quotas. Returns
        (row dict, changed). Raises SourceRejected with a category."""
        if not isinstance(data, (bytes, bytearray)):
            raise SourceRejected("input", "The file content must be bytes.")
        data = bytes(data)
        sha = src_rules.sha256_hex(data)
        if not isinstance(claimed_sha256, str) or claimed_sha256.lower() != sha:
            raise SourceRejected("integrity", "The hash of the file does not match its content.")
        if src["kind"] != "upload":
            raise SourceRejected("state", "Files can only be stored in an upload source.")
        display, key = src_rules.clean_relative_path(path)
        src_rules.check_stored_name(src["kind"], display)
        state = self._path_state(conn, src, key)
        if state == "denied":
            raise SourceRejected("permission", f"'{display}' is blocked.")
        if state != "allowed":
            raise SourceRejected("permission", f"'{display}' is not in an allowed folder of this source.")
        if len(data) > src_rules.MAX_FILE_BYTES:
            raise SourceRejected("content", f"'{display}' is too large (limit {src_rules.MAX_FILE_BYTES} bytes).")
        problem = text_problem(data)
        if problem:
            raise SourceRejected("content", f"'{display}' is not allowed: {problem}.")
        old = conn.execute(
            "SELECT state, version, sha256, size_bytes FROM stored_files WHERE source_id=? AND path_key=?",
            (src["id"], key),
        ).fetchone()
        if old is not None and old["state"] == "current" and old["sha256"] == sha:
            return {"path_key": key, "path_display": display, "version": old["version"], "sha256": sha,
                    "size": len(data)}, False
        replaced = old["size_bytes"] if old is not None and old["state"] == "current" else 0
        files, used = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(size_bytes),0) FROM stored_files WHERE source_id=? AND state='current'",
            (src["id"],),
        ).fetchone()
        if old is None or old["state"] != "current":
            if files + 1 > src_rules.MAX_FILES_PER_SOURCE:
                raise SourceRejected(
                    "quota", f"A source can hold at most {src_rules.MAX_FILES_PER_SOURCE} files."
                )
        total = conn.execute(
            "SELECT COALESCE(SUM(size_bytes),0) FROM stored_files WHERE state='current'"
        ).fetchone()[0]
        if total - replaced + len(data) > src_rules.MAX_TOTAL_BYTES:
            raise SourceRejected("quota", f"The stored files may not exceed {src_rules.MAX_TOTAL_BYTES} bytes in total.")
        version = (old["version"] if old is not None else 0) + 1
        conn.execute(
            "INSERT INTO stored_files(source_id,path_key,path_display,state,purged_reason,version,sha256,"
            "size_bytes,content,uploaded_at,changed_at) VALUES (?,?,?,'current',NULL,?,?,?,?,?,?)"
            " ON CONFLICT(source_id,path_key) DO UPDATE SET path_display=excluded.path_display,"
            " state='current', purged_reason=NULL, version=excluded.version, sha256=excluded.sha256,"
            " size_bytes=excluded.size_bytes, content=excluded.content, uploaded_at=excluded.uploaded_at,"
            " changed_at=excluded.changed_at",
            (src["id"], key, display, version, sha, len(data), data, now, now),
        )
        return {"path_key": key, "path_display": display, "version": version, "sha256": sha, "size": len(data)}, True

    def put_stored_file(
        self, source_id: str, path: str, data: bytes, claimed_sha256: str, now: float, changed_by: str
    ) -> dict:
        """Store (or replace) one uploaded file. Same content again changes nothing (no new version).
        The result has path_key, path_display, version, sha256, size and `changed`."""
        with self._tx() as conn:
            src = self._active_source(conn, source_id)
            result, changed = self._put_stored(conn, src, path, data, claimed_sha256, now)
            if changed:
                conn.execute("UPDATE file_sources SET sync_epoch=sync_epoch+1 WHERE id=?", (source_id,))
                self._audit(
                    conn, now, "stored_file_stored", owner=changed_by,
                    detail=(
                        f"source={source_id} label={src['label']} path={result['path_display']}"
                        f" version={result['version']} id={src_rules.short_version(result['sha256'])}"
                    ),
                )
            return {**result, "changed": changed}

    def remove_stored_file(self, source_id: str, path: str, now: float, changed_by: str) -> bool:
        """Mark a stored copy as removed (the file is gone on the uploading side). Deletes the content."""
        display, key = src_rules.clean_relative_path(path)
        with self._tx() as conn:
            src = self._active_source(conn, source_id)
            cur = conn.execute(
                "UPDATE stored_files SET state='removed', purged_reason='removed_local', content=NULL,"
                " changed_at=? WHERE source_id=? AND path_key=? AND state='current'",
                (now, source_id, key),
            )
            if not cur.rowcount:
                return False
            conn.execute("UPDATE file_sources SET sync_epoch=sync_epoch+1 WHERE id=?", (source_id,))
            self._audit(
                conn, now, "stored_file_removed", owner=changed_by,
                detail=f"source={source_id} label={src['label']} path={display}",
            )
            return True

    def stored_content(self, source_id: str, path: str) -> Optional[dict]:
        """The stored copy of a file: only if its source is active, the copy is current and the rules
        allow the path right now. None otherwise. Returns data, sha256, version, size, uploaded_at."""
        try:
            _, key = src_rules.clean_relative_path(path)
        except SourceRejected:
            return None
        with self._connect() as conn:
            src = conn.execute("SELECT * FROM file_sources WHERE id=?", (source_id,)).fetchone()
            if src is None or src["status"] != "active" or self._path_state(conn, src, key) != "allowed":
                return None
            row = conn.execute(
                "SELECT content, sha256, version, size_bytes, uploaded_at FROM stored_files"
                " WHERE source_id=? AND path_key=? AND state='current'",
                (source_id, key),
            ).fetchone()
            if row is None:
                return None
            return {
                "data": bytes(row["content"]), "sha256": row["sha256"], "version": row["version"],
                "size": row["size_bytes"], "uploaded_at": row["uploaded_at"],
            }

    def list_stored_files(self, source_id: str, state: Optional[str] = None) -> List[sqlite3.Row]:
        """Metadata of the stored files of a source (never the content), ordered by path."""
        with self._connect() as conn:
            query = (
                "SELECT path_key, path_display, state, purged_reason, version, sha256, size_bytes,"
                " uploaded_at, changed_at FROM stored_files WHERE source_id=?"
            )
            args: list = [source_id]
            if state is not None:
                query += " AND state=?"
                args.append(state)
            return conn.execute(query + " ORDER BY path_key", args).fetchall()

    def list_allowed_files(self, source_id: str) -> List[dict]:
        """The current stored files of an upload source that the rules allow right now (what Claude may
        see). Metadata only, never the content, ordered by path."""
        with self._connect() as conn:
            src = conn.execute("SELECT * FROM file_sources WHERE id=?", (source_id,)).fetchone()
            if src is None or src["status"] != "active" or src["kind"] != "upload":
                return []
            out = []
            for r in conn.execute(
                "SELECT path_key, path_display, version, sha256, size_bytes, uploaded_at FROM stored_files"
                " WHERE source_id=? AND state='current' ORDER BY path_key",
                (source_id,),
            ).fetchall():
                try:
                    src_rules.check_stored_name("upload", r["path_display"])
                except SourceRejected:
                    continue
                if self._path_state(conn, src, r["path_key"]) == "allowed":
                    out.append(dict(r))
            return out

    def rename_source(self, source_id: str, label: str, now: float, changed_by: str) -> str:
        """Change the name of an active source. The name is only what people and Claude see; nothing else
        about the source changes. Returns the new name as shown."""
        display, key = src_rules.clean_label(label)
        with self._tx() as conn:
            src = self._active_source(conn, source_id)
            if src["label_key"] == key and src["label"] == display:
                return display
            try:
                conn.execute("UPDATE file_sources SET label=?, label_key=? WHERE id=?", (display, key, source_id))
            except sqlite3.IntegrityError:
                raise SourceRejected("conflict", "A source with this name already exists.") from None
            self._audit(
                conn, now, "source_renamed", owner=changed_by,
                detail=f"source={source_id} from={src['label']} to={display}",
            )
            return display

    def source_rules(self, source_id: str) -> List[tuple]:
        """The allow and deny rules of one source as (path_key, effect, path_display)."""
        with self._connect() as conn:
            src = conn.execute("SELECT * FROM file_sources WHERE id=?", (source_id,)).fetchone()
            if src is None or src["status"] != "active":
                return []
            return [
                (r["path_key"], r["effect"], r["path_display"])
                for r in conn.execute(
                    "SELECT path_key, effect, path_display FROM file_access_rules WHERE scope='attach'"
                    " AND root_path=? AND (source_id=? OR source_id IS NULL) AND effect IN ('allow','deny')"
                    " ORDER BY path_key",
                    (src["rule_root"], source_id),
                )
            ]

    def apply_sync(
        self, source_id: str, files: Sequence[tuple], removals: Sequence[str], now: float, changed_by: str,
        expected_epoch: int,
    ) -> dict:
        """One pass of a synchronisation, all or nothing. `files` are (path, bytes, claimed sha256);
        `removals` are path keys whose stored copy is to be marked removed. Integrity, permission,
        quota and state violations undo the whole pass (SourceRejected); a file whose content is
        not acceptable (too large, not text, secret) is skipped and reported. One audit entry covers
        the pass. A changed epoch (a rule, a removal or another upload happened meanwhile) refuses it."""
        with self._tx() as conn:
            src = self._active_source(conn, source_id)
            if src["sync_epoch"] != expected_epoch:
                raise SourceRejected("conflict", "The source changed while the synchronisation was prepared. Plan again.")
            stored, unchanged, skipped = 0, 0, []
            for path, data, sha in files:
                try:
                    _, changed = self._put_stored(conn, src, path, data, sha, now)
                except SourceRejected as exc:
                    if exc.category != "content":
                        raise
                    skipped.append({"path": str(path)[:200], "reason": str(exc)})
                    continue
                stored += 1 if changed else 0
                unchanged += 0 if changed else 1
            removed = 0
            for key in removals:
                removed += conn.execute(
                    "UPDATE stored_files SET state='removed', purged_reason='removed_local', content=NULL,"
                    " changed_at=? WHERE source_id=? AND path_key=? AND state='current'",
                    (now, source_id, key),
                ).rowcount
            epoch = src["sync_epoch"]
            if stored or removed:
                epoch += 1
                conn.execute("UPDATE file_sources SET sync_epoch=? WHERE id=?", (epoch, source_id))
            self._audit(
                conn, now, "source_synced", owner=changed_by,
                detail=(
                    f"source={source_id} label={src['label']} stored={stored} removed={removed}"
                    f" unchanged={unchanged} skipped={len(skipped)}"
                ),
            )
            return {"stored": stored, "removed": removed, "unchanged": unchanged, "skipped": skipped, "epoch": epoch}

    def source_usage(self) -> dict:
        """Counts for the quota display: active sources, current files and bytes (total and per source)."""
        with self._connect() as conn:
            active = conn.execute("SELECT COUNT(*) FROM file_sources WHERE status='active'").fetchone()[0]
            per = {}
            for r in conn.execute(
                "SELECT source_id, state, COUNT(*) n, COALESCE(SUM(size_bytes),0) b FROM stored_files"
                " GROUP BY source_id, state"
            ):
                entry = per.setdefault(r["source_id"], {"current": 0, "purged": 0, "removed": 0, "bytes": 0})
                entry[r["state"]] = r["n"]
                if r["state"] == "current":
                    entry["bytes"] = r["b"]
            return {"sources": active, "bytes": sum(e["bytes"] for e in per.values()), "per_source": per}

    # -- reading helpers (also used by tests) -------------------------------------

    def count_events(self, event: str, since: float = 0.0) -> int:
        with self._connect() as conn:
            return self._count(conn, event, since)

    def audit_events(self) -> List[sqlite3.Row]:
        with self._connect() as conn:
            return conn.execute("SELECT * FROM audit_log ORDER BY seq").fetchall()

