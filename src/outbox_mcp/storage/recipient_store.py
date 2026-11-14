"""The recipient registry: who may receive mail, who was only suggested, who is blocked.

Claude can only call `suggest` (and read its own suggestions). Everything that activates,
rejects, blocks or removes a recipient is the admin side and is reached through a separate
interface that the AI connector cannot see. Every change is written to the audit log.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from typing import Iterable, List, Optional, Sequence, Tuple, Union

from ..errors import RateLimitExceeded, RecipientNotAllowed, ValidationError
from ..security.rate_limiter import DAY
from ..security.recipients import Recipient, RecipientList
from ..security.validation import validate_address, validate_display_name
from .database import Database

NOTE_MAX_CHARS = 300

Ref = Union[int, str]


@dataclass(frozen=True)
class RecipientRecord:
    id: int
    name: str
    address: str
    aliases: Tuple[str, ...]
    status: str
    note: str
    suggested_by: Optional[str]
    created_at: float
    decided_at: Optional[float]


@dataclass(frozen=True)
class SuggestionResult:
    status: str  # suggested | already_suggested | already_allowed
    id: int
    name: str
    address: str


def _record(row: sqlite3.Row) -> RecipientRecord:
    return RecipientRecord(
        id=row["id"], name=row["name"], address=row["address"],
        aliases=tuple(json.loads(row["aliases"])), status=row["status"], note=row["note"],
        suggested_by=row["suggested_by"], created_at=row["created_at"], decided_at=row["decided_at"],
    )


def _key(address: str) -> str:
    return address.casefold()


class RecipientStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    # -- Claude's side ------------------------------------------------------------

    def suggest(
        self, owner: str, name: str, address: str, note: str, now: float,
        max_open: int = 5, max_per_day: int = 10,
    ) -> SuggestionResult:
        name = validate_display_name(name)
        if not name:
            raise ValidationError("A suggestion needs a name.")
        address = validate_address(address)
        note = validate_display_name(note or "", NOTE_MAX_CHARS)
        with self._db._tx() as conn:
            row = self._by_address(conn, address)
            if row is not None:
                if row["status"] == "active":
                    return SuggestionResult("already_allowed", row["id"], row["name"], row["address"])
                if row["status"] == "blocked":
                    raise RecipientNotAllowed("This address cannot be suggested.")
                if row["status"] == "suggested":
                    return SuggestionResult("already_suggested", row["id"], row["name"], row["address"])
            open_now = conn.execute("SELECT COUNT(*) FROM recipients WHERE status='suggested'").fetchone()[0]
            if open_now >= max_open:
                raise RateLimitExceeded(
                    f"There are already {max_open} open suggestions. The user has to decide on them first."
                )
            today = conn.execute(
                "SELECT COUNT(*) FROM audit_log WHERE event='recipient_suggested' AND ts>?", (now - DAY,)
            ).fetchone()[0]
            if today >= max_per_day:
                raise RateLimitExceeded(f"The limit of {max_per_day} suggestions per day has been reached.")
            if row is None:
                cur = conn.execute(
                    "INSERT INTO recipients(name,address,address_key,status,note,suggested_by,created_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (name, address, _key(address), "suggested", note, owner, now),
                )
                rid = cur.lastrowid
            else:
                rid = row["id"]
                conn.execute(
                    "UPDATE recipients SET name=?, address=?, status='suggested', aliases='[]', note=?,"
                    " suggested_by=?, created_at=?, decided_at=NULL WHERE id=?",
                    (name, address, note, owner, now, rid),
                )
            self._audit(conn, now, "recipient_suggested", owner, address, f"name: {name}")
            return SuggestionResult("suggested", rid, name, address)

    def suggestions_of(self, owner: str) -> List[RecipientRecord]:
        with self._db._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM recipients WHERE suggested_by=? ORDER BY id", (owner,)
            ).fetchall()
        return [_record(r) for r in rows]

    # -- what the mail service uses -----------------------------------------------

    def active(self) -> Tuple[Recipient, ...]:
        with self._db._connect() as conn:
            rows = conn.execute("SELECT * FROM recipients WHERE status='active' ORDER BY id").fetchall()
        return tuple(Recipient(r["name"], r["address"], tuple(json.loads(r["aliases"]))) for r in rows)

    def has_any(self) -> bool:
        with self._db._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM recipients").fetchone()[0] > 0

    # -- the admin side -----------------------------------------------------------

    def pending(self) -> List[RecipientRecord]:
        with self._db._connect() as conn:
            rows = conn.execute("SELECT * FROM recipients WHERE status='suggested' ORDER BY id").fetchall()
        return [_record(r) for r in rows]

    def all_records(self) -> List[RecipientRecord]:
        with self._db._connect() as conn:
            rows = conn.execute("SELECT * FROM recipients ORDER BY id").fetchall()
        return [_record(r) for r in rows]

    def find(self, ref: Ref) -> RecipientRecord:
        with self._db._connect() as conn:
            return _record(self._get(conn, ref))

    def approve(
        self, ref: Ref, now: float, source: str,
        name: Optional[str] = None, aliases: Optional[Sequence[str]] = None,
    ) -> RecipientRecord:
        with self._db._tx() as conn:
            row = self._get(conn, ref)
            if row["status"] != "suggested":
                raise ValidationError(
                    f"{row['address']} is not an open suggestion (status: {row['status']})."
                )
            return self._activate(conn, row, now, source, name, aliases, "recipient_approved")

    def add(
        self, name: str, address: str, aliases: Sequence[str], now: float, source: str
    ) -> RecipientRecord:
        name = validate_display_name(name)
        address = validate_address(address)
        with self._db._tx() as conn:
            row = self._by_address(conn, address)
            if row is None:
                cur = conn.execute(
                    "INSERT INTO recipients(name,address,address_key,status,created_at)"
                    " VALUES (?,?,?,?,?)",
                    (name or "?", address, _key(address), "removed", now),
                )
                row = conn.execute("SELECT * FROM recipients WHERE id=?", (cur.lastrowid,)).fetchone()
            elif row["status"] == "active":
                raise ValidationError(f"{row['address']} is already on the list.")
            elif row["status"] == "blocked":
                raise RecipientNotAllowed("This address is blocked. Unblock it first.")
            return self._activate(conn, row, now, source, name or row["name"], aliases, "recipient_added")

    def reject(self, ref: Ref, now: float, source: str) -> RecipientRecord:
        return self._move(ref, now, source, {"suggested"}, "rejected", "recipient_rejected")

    def remove(self, ref: Ref, now: float, source: str) -> RecipientRecord:
        return self._move(ref, now, source, {"active"}, "removed", "recipient_removed")

    def unblock(self, ref: Ref, now: float, source: str) -> RecipientRecord:
        return self._move(ref, now, source, {"blocked"}, "removed", "recipient_unblocked")

    def block(self, ref: Ref, now: float, source: str) -> RecipientRecord:
        """Block an address for good. Works for known records and for addresses never seen."""
        with self._db._tx() as conn:
            row = self._lookup(conn, ref)
            if row is None:
                if isinstance(ref, int) or str(ref).isdigit():
                    raise ValidationError(f"No recipient found for '{ref}'.")
                address = validate_address(str(ref))
                cur = conn.execute(
                    "INSERT INTO recipients(name,address,address_key,status,created_at,decided_at)"
                    " VALUES (?,?,?,?,?,?)",
                    (address, address, _key(address), "blocked", now, now),
                )
                row = conn.execute("SELECT * FROM recipients WHERE id=?", (cur.lastrowid,)).fetchone()
                self._audit(conn, now, "recipient_blocked", source, row["address"], None)
                return _record(row)
            if row["status"] == "blocked":
                return _record(row)
            return self._set_status(conn, row, now, source, "blocked", "recipient_blocked")

    def expire_stale(self, now: float, ttl_days: int) -> int:
        cutoff = now - ttl_days * DAY
        with self._db._tx() as conn:
            rows = conn.execute(
                "SELECT * FROM recipients WHERE status='suggested' AND created_at<=?", (cutoff,)
            ).fetchall()
            for row in rows:
                conn.execute(
                    "UPDATE recipients SET status='expired', decided_at=? WHERE id=?", (now, row["id"])
                )
                self._audit(conn, now, "recipient_expired", "system", row["address"], None)
            return len(rows)

    def import_list_once(self, recipients: RecipientList, now: float) -> int:
        """Take over the old recipients file the first time, when the registry is still empty."""
        with self._db._tx() as conn:
            if conn.execute("SELECT COUNT(*) FROM recipients").fetchone()[0] > 0:
                return 0
            imported = 0
            for r in recipients.all():
                conn.execute(
                    "INSERT INTO recipients(name,address,address_key,status,aliases,created_at,decided_at)"
                    " VALUES (?,?,?,?,?,?,?)",
                    (r.name, r.address, _key(r.address), "active", json.dumps(list(r.aliases)), now, now),
                )
                self._audit(conn, now, "recipient_imported", "system", r.address, "from recipients file")
                imported += 1
            return imported

    # -- internals ----------------------------------------------------------------

    @staticmethod
    def _audit(conn, now, event, owner, address, detail) -> None:
        conn.execute(
            "INSERT INTO audit_log(ts,event,owner,recipient_address,detail) VALUES (?,?,?,?,?)",
            (now, event, owner, address, detail),
        )

    @staticmethod
    def _by_address(conn, address: str):
        return conn.execute("SELECT * FROM recipients WHERE address_key=?", (_key(address),)).fetchone()

    def _lookup(self, conn, ref: Ref):
        if isinstance(ref, int) or (isinstance(ref, str) and ref.strip().isdigit()):
            return conn.execute("SELECT * FROM recipients WHERE id=?", (int(ref),)).fetchone()
        if isinstance(ref, str):
            return conn.execute(
                "SELECT * FROM recipients WHERE address_key=?", (_key(ref.strip()),)
            ).fetchone()
        return None

    def _get(self, conn, ref: Ref):
        row = self._lookup(conn, ref)
        if row is None:
            raise ValidationError(f"No recipient found for '{ref}'.")
        return row

    def _set_status(self, conn, row, now, source, status, event) -> RecipientRecord:
        conn.execute("UPDATE recipients SET status=?, decided_at=? WHERE id=?", (status, now, row["id"]))
        self._audit(conn, now, event, source, row["address"], None)
        return _record(conn.execute("SELECT * FROM recipients WHERE id=?", (row["id"],)).fetchone())

    def _move(self, ref, now, source, allowed_from, status, event) -> RecipientRecord:
        with self._db._tx() as conn:
            row = self._get(conn, ref)
            if row["status"] not in allowed_from:
                raise ValidationError(
                    f"{row['address']} cannot be changed this way (status: {row['status']})."
                )
            return self._set_status(conn, row, now, source, status, event)

    def _activate(self, conn, row, now, source, name, aliases, event) -> RecipientRecord:
        new_name = validate_display_name(name) if name else row["name"]
        if not new_name:
            raise ValidationError("A recipient needs a name.")
        new_aliases = [validate_display_name(a) for a in (aliases or [])]
        if any(not a for a in new_aliases):
            raise ValidationError("An alias must not be empty.")
        labels = [new_name, *new_aliases]
        folded = [label.casefold() for label in labels]
        if len(set(folded)) != len(folded):
            raise ValidationError("The name and aliases must all be different.")
        taken = set()
        for other in conn.execute(
            "SELECT name, aliases FROM recipients WHERE status='active' AND id!=?", (row["id"],)
        ):
            taken.add(other["name"].casefold())
            taken.update(a.casefold() for a in json.loads(other["aliases"]))
        clash = [label for label, key in zip(labels, folded) if key in taken]
        if clash:
            raise ValidationError(f"The name or alias '{clash[0]}' is already used by another recipient.")
        conn.execute(
            "UPDATE recipients SET status='active', name=?, aliases=?, decided_at=? WHERE id=?",
            (new_name, json.dumps(new_aliases), now, row["id"]),
        )
        detail = f"aliases: {', '.join(new_aliases)}" if new_aliases else None
        self._audit(conn, now, event, source, row["address"], detail)
        return _record(conn.execute("SELECT * FROM recipients WHERE id=?", (row["id"],)).fetchone())


class DbRecipientList:
    """The live list of active recipients, same interface as the file-based RecipientList.
    It reads the registry on every call, so a change takes effect without a restart."""

    def __init__(self, store: RecipientStore) -> None:
        self._store = store

    def all(self) -> Tuple[Recipient, ...]:
        return self._store.active()

    def resolve(self, key: object) -> Recipient:
        return RecipientList(self.all()).resolve(key)

    def is_allowed_address(self, address: str) -> bool:
        return RecipientList(self.all()).is_allowed_address(address)
