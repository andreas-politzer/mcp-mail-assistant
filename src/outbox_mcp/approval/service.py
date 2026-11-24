"""Passkey approval of one draft, outside the chat.

What this proves: the person who holds the registered passkey, and could unlock it on the
device, approved exactly this draft (recipient, subject, text, attachments). What it does not
prove: that the person read the text. The approval card has to show the content.

Rules that hold everywhere in this module:
* A challenge is used up by the first attempt that names it, whatever the outcome.
* The checks and the approval happen in one write transaction that is rolled back as a whole on
  any failure. Using the challenge up is a separate, earlier transaction that is always kept.
* A draft is approved only for the exact fingerprint (`body_sha256`) it has when the challenge is
  issued, and the fingerprint is calculated again from the stored fields at both ends.
* Nothing here can be reached through the AI connector.
"""

from __future__ import annotations

import base64
import hashlib
import json
import secrets
import os
import sqlite3
import traceback
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any, Callable, Dict, Mapping, Optional, Tuple

from webauthn import (
    base64url_to_bytes,
    generate_authentication_options,
    generate_registration_options,
    verify_authentication_response,
    verify_registration_response,
)
from webauthn.helpers import options_to_json
from webauthn.helpers.structs import (
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..errors import ConfigError
from ..storage.database import Database, body_hash

MAX_PASSKEYS = 2
MAX_LABEL = 40


def clean_label(value: Any) -> str:
    """The name of a passkey as a person typed it: one line, no control characters, at most MAX_LABEL
    characters. Anything else gives an empty string, and the caller picks a default."""
    if not isinstance(value, str):
        return ""
    text = "".join(ch if ch.isprintable() else " " for ch in value)
    return " ".join(text.split())[:MAX_LABEL].strip()
ENROLLMENT_SECONDS = 600
DEFAULT_CHALLENGE_SECONDS = 300
# Upper limit of the waiting time. Clients end a tool call that takes too long (often after about a
# minute), so a long wait would only turn a missing approval into a client error.
MAX_WAIT_SECONDS = 50


def b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


LOCAL_HOST = "localhost"


@dataclass(frozen=True)
class ApprovalConfig:
    """Two forms, and nothing in between.

    Public: `rp_id` is the host name of the public address of this server (no scheme, port, path
    or wildcard), `port` is None, the origin is https. A passkey is bound to it.

    Local: `rp_id` is exactly "localhost" and `port` is the port of the local approval page; the
    origin is `http://localhost:<port>`. Browsers treat localhost as a secure context, so passkeys
    work there without a domain or a certificate. Only the loopback name is accepted for http."""

    rp_id: str
    rp_name: str = "Mail Assistant"
    challenge_seconds: int = DEFAULT_CHALLENGE_SECONDS
    port: Optional[int] = None
    # How long send_email waits for the approval before it answers "approval required". 0 = it does
    # not wait (the person tells the assistant that they approved, and it calls send_email again).
    wait_seconds: int = 0
    # True: the approval page sends the mail itself right after the passkey check. The person can
    # take any time to read and approve (until the draft expires), and nobody has to tell the
    # assistant afterwards. False: the old way (the assistant calls send_email after the approval).
    sends: bool = True

    def __post_init__(self) -> None:
        host = self.rp_id
        if host == LOCAL_HOST:
            if self.port is None or isinstance(self.port, bool) or not 1024 <= self.port <= 65535:
                raise ConfigError(
                    "The local approval page needs OUTBOX_APPROVAL_PORT, a port between 1024 and 65535."
                )
        else:
            if self.port is not None:
                raise ConfigError("OUTBOX_APPROVAL_PORT is only allowed together with OUTBOX_APPROVAL_HOST=localhost.")
            bad = (
                not host or host != host.strip().lower() or "." not in host
                or any(c in host for c in ":/\\@*? #%") or host.startswith(".") or host.endswith(".")
                or ".." in host or not all(c.isalnum() or c in ".-" for c in host) or not host.isascii()
            )
            if bad:
                raise ConfigError(
                    "The approval host must be a plain lower-case host name such as relay.example.org "
                    "(no scheme, port, path or wildcard), or exactly 'localhost' for the local page."
                )
        if not 30 <= self.challenge_seconds <= 900:
            raise ConfigError("The approval challenge time must be between 30 and 900 seconds.")
        if isinstance(self.wait_seconds, bool) or not 0 <= self.wait_seconds <= MAX_WAIT_SECONDS:
            raise ConfigError(f"OUTBOX_APPROVAL_WAIT_SECONDS must be between 0 and {MAX_WAIT_SECONDS}.")

    @property
    def is_local(self) -> bool:
        return self.rp_id == LOCAL_HOST

    @property
    def origin(self) -> str:
        return f"http://{LOCAL_HOST}:{self.port}" if self.is_local else f"https://{self.rp_id}"

    @classmethod
    def from_env(cls, env: Mapping[str, str]) -> Optional["ApprovalConfig"]:
        """None when no host is set: approval is then switched off, and mail that needs it cannot be sent."""
        host = (env.get("OUTBOX_APPROVAL_HOST") or "").strip()
        if not host:
            return None
        raw = (env.get("OUTBOX_APPROVAL_CHALLENGE_SECONDS") or "").strip()
        try:
            seconds = int(raw) if raw else DEFAULT_CHALLENGE_SECONDS
        except ValueError:
            raise ConfigError("OUTBOX_APPROVAL_CHALLENGE_SECONDS must be a whole number.") from None
        raw_port = (env.get("OUTBOX_APPROVAL_PORT") or "").strip()
        try:
            port = int(raw_port) if raw_port else None
        except ValueError:
            raise ConfigError("OUTBOX_APPROVAL_PORT must be a whole number.") from None
        raw_wait = (env.get("OUTBOX_APPROVAL_WAIT_SECONDS") or "").strip()
        try:
            wait = int(raw_wait) if raw_wait else 0
        except ValueError:
            raise ConfigError("OUTBOX_APPROVAL_WAIT_SECONDS must be a whole number.") from None
        raw_sends = (env.get("OUTBOX_APPROVAL_SENDS") or "").strip().lower()
        if raw_sends not in ("", "0", "1", "true", "false", "yes", "no", "on", "off"):
            raise ConfigError("OUTBOX_APPROVAL_SENDS must be 1 or 0.")
        sends = raw_sends not in ("0", "false", "no", "off")
        return cls(rp_id=host, challenge_seconds=seconds, port=port, wait_seconds=wait, sends=sends)


def _audit_detail(exc: BaseException) -> str:
    """Audit text for a rejected approval. Our own rejections keep their short text; an unexpected
    exception also gets the place it was raised (file:line) and the start of its message, so the cause
    can be found without changing what the approval page shows."""
    if isinstance(exc, _Rejected):
        return f"{type(exc).__name__}: {exc}"
    where = ""
    frames = traceback.extract_tb(exc.__traceback__)
    if frames:
        last = frames[-1]
        where = f" at {os.path.basename(last.filename)}:{last.lineno} in {last.name}"
    return f"{type(exc).__name__}{where}: {str(exc)[:200]}"


class _Rejected(Exception):
    """A check failed. Only the class of the reason leaves this module, never details."""


class ApprovalService:
    def __init__(self, db: Database, config: ApprovalConfig, clock: Callable[[], float]) -> None:
        self.db = db
        self.config = config
        self.clock = clock

    # -- passkeys -----------------------------------------------------------------

    def passkey_count(self) -> int:
        with self.db._connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM passkey_credentials").fetchone()[0]

    def list_passkeys(self) -> list:
        with self.db._connect() as conn:
            rows = conn.execute(
                "SELECT credential_id, created_at, last_used_at, label FROM passkey_credentials ORDER BY created_at"
            ).fetchall()
        return [dict(r) for r in rows]

    def remove_passkey(self, credential_id_prefix: str) -> bool:
        prefix = credential_id_prefix.strip()
        if len(prefix) < 6:
            raise ConfigError("Give at least six characters of the passkey id.")
        with self.db._tx() as conn:
            rows = conn.execute(
                "SELECT credential_id FROM passkey_credentials WHERE substr(credential_id,1,?)=?",
                (len(prefix), prefix),
            ).fetchall()
            if len(rows) != 1:
                return False
            conn.execute("DELETE FROM passkey_credentials WHERE credential_id=?", (rows[0][0],))
            return True

    def rename_passkey(self, credential_id_prefix: str, label: Any) -> bool:
        """Give a passkey a new name. This changes only the name kept here, never the entry that the
        device itself holds."""
        prefix = credential_id_prefix.strip()
        name = clean_label(label)
        if len(prefix) < 6:
            raise ConfigError("Give at least six characters of the passkey id.")
        if not name:
            raise ConfigError("Give the passkey a name.")
        with self.db._tx() as conn:
            rows = conn.execute(
                "SELECT credential_id FROM passkey_credentials WHERE substr(credential_id,1,?)=?",
                (len(prefix), prefix),
            ).fetchall()
            if len(rows) != 1:
                return False
            conn.execute("UPDATE passkey_credentials SET label=? WHERE credential_id=?", (name, rows[0][0]))
            return True

    # -- enrolling a passkey ------------------------------------------------------

    def create_enrollment(self) -> Optional[str]:
        """A single-use secret for one enrollment. Created on the admin side only. Returns None
        when the maximum number of passkeys is reached."""
        now = self.clock()
        token = secrets.token_urlsafe(24)
        with self.db._tx() as conn:
            if conn.execute("SELECT COUNT(*) FROM passkey_credentials").fetchone()[0] >= MAX_PASSKEYS:
                return None
            conn.execute(
                "INSERT INTO passkey_enrollments(token_hash, created_at, expires_at) VALUES (?,?,?)",
                (self._hash(token), now, now + ENROLLMENT_SECONDS),
            )
        return token

    def enroll_options(self, token: str, label: Any = None) -> Tuple[bool, Any]:
        now = self.clock()
        if not isinstance(token, str):
            return False, "This link is not valid."
        with self.db._tx() as conn:
            row = self._open_enrollment(conn, token, now)
            if row is None:
                return False, "This link is not valid or has expired."
            count = conn.execute("SELECT COUNT(*) FROM passkey_credentials").fetchone()[0]
            if count >= MAX_PASSKEYS:
                return False, "The maximum number of passkeys is reached."
            name = clean_label(label) or f"Passkey {count + 1}"
            challenge = secrets.token_bytes(32)
            conn.execute(
                "INSERT INTO passkey_challenges(challenge_id, purpose, enrollment_hash, created_at, expires_at)"
                " VALUES (?, 'enroll', ?, ?, ?)",
                (b64url(challenge), row["token_hash"], now, min(now + self.config.challenge_seconds, row["expires_at"])),
            )
            existing = [
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(r[0]))
                for r in conn.execute("SELECT credential_id FROM passkey_credentials")
            ]
        options = generate_registration_options(
            rp_id=self.config.rp_id,
            rp_name=self.config.rp_name,
            user_id=secrets.token_bytes(16),  # a new handle per passkey: a second one never replaces the first
            user_name=name,  # the device shows this name next to the passkey
            user_display_name=name,
            challenge=challenge,
            exclude_credentials=existing or None,
            authenticator_selection=AuthenticatorSelectionCriteria(
                resident_key=ResidentKeyRequirement.REQUIRED,
                user_verification=UserVerificationRequirement.REQUIRED,
            ),
        )
        return True, {"challenge_id": b64url(challenge), "publicKey": json.loads(options_to_json(options))}

    def enroll_verify(self, token: str, payload: Any) -> Tuple[bool, str]:
        if not isinstance(token, str) or not isinstance(payload, dict):
            return False, "Malformed request."
        challenge_id = payload.get("challenge_id")
        credential = payload.get("credential")
        if not isinstance(challenge_id, str) or not isinstance(credential, dict):
            return False, "Malformed request."
        now = self.clock()
        if not self._use_up_challenge(challenge_id, "enroll", now):
            return False, "Unknown or used challenge."
        try:
            with self.db._tx() as conn:
                row = self._open_enrollment(conn, token, now)
                if row is None:
                    raise _Rejected("enrollment")
                challenge = conn.execute(
                    "SELECT * FROM passkey_challenges WHERE challenge_id=?", (challenge_id,)
                ).fetchone()
                if challenge["enrollment_hash"] != row["token_hash"]:
                    raise _Rejected("challenge belongs to another enrollment")
                if now > challenge["expires_at"]:
                    raise _Rejected("expired")
                count = conn.execute("SELECT COUNT(*) FROM passkey_credentials").fetchone()[0]
                if count >= MAX_PASSKEYS:
                    raise _Rejected("limit")
                verified = verify_registration_response(
                    credential=credential,
                    expected_challenge=base64url_to_bytes(challenge_id),
                    expected_rp_id=self.config.rp_id,
                    expected_origin=self.config.origin,
                    require_user_verification=True,
                )
                conn.execute(
                    "INSERT INTO passkey_credentials(credential_id, public_key, sign_count, created_at, label)"
                    " VALUES (?,?,?,?,?)",
                    (b64url(verified.credential_id), verified.credential_public_key, verified.sign_count, now,
                     clean_label(payload.get("label")) or f"Passkey {count + 1}"),
                )
                conn.execute("UPDATE passkey_enrollments SET used_at=? WHERE token_hash=?", (now, row["token_hash"]))
        except Exception as exc:  # the transaction is rolled back as a whole
            return False, f"Enrollment rejected: {type(exc).__name__}"
        return True, "Passkey registered."

    # -- approving a draft --------------------------------------------------------

    def approve_options(self, draft_id: str) -> Tuple[bool, Any]:
        now = self.clock()
        if not isinstance(draft_id, str):
            return False, "Draft not found. If several Mail Assistant servers are active, it was probably created on the other one."
        with self.db._tx() as conn:
            draft = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                return False, "Draft not found. If several Mail Assistant servers are active, it was probably created on the other one."
            if draft["status"] != "pending":
                return False, "This draft can no longer be approved."
            if now >= draft["expires_at"]:
                return False, "This draft has expired."
            if conn.execute("SELECT COUNT(*) FROM passkey_credentials").fetchone()[0] == 0:
                return False, "No passkey is registered."
            if conn.execute("SELECT 1 FROM draft_approvals WHERE draft_id=?", (draft_id,)).fetchone():
                return False, "This draft is already approved."
            try:
                fingerprint = self._fingerprint(conn, draft)
            except _Rejected:
                return False, "The stored draft does not match its fingerprint."
            challenge = secrets.token_bytes(32)
            conn.execute(
                "INSERT INTO passkey_challenges(challenge_id, purpose, draft_id, body_sha256, created_at, expires_at)"
                " VALUES (?, 'approve', ?, ?, ?, ?)",
                (b64url(challenge), draft_id, fingerprint, now,
                 min(now + self.config.challenge_seconds, draft["expires_at"])),
            )
            known = [
                PublicKeyCredentialDescriptor(id=base64url_to_bytes(r[0]))
                for r in conn.execute("SELECT credential_id FROM passkey_credentials")
            ]
        self._audit("approval_issued", draft_id)
        options = generate_authentication_options(
            rp_id=self.config.rp_id,
            challenge=challenge,
            allow_credentials=known,  # the device offers only passkeys this program knows
            user_verification=UserVerificationRequirement.REQUIRED,
        )
        return True, {"challenge_id": b64url(challenge), "publicKey": json.loads(options_to_json(options))}

    def approve_verify(self, draft_id: str, payload: Any) -> Tuple[bool, str]:
        """All checks of an approval live here. Returns (approved, message)."""
        if not isinstance(draft_id, str) or not isinstance(payload, dict):
            return False, "Malformed request."
        challenge_id = payload.get("challenge_id")
        credential = payload.get("credential")
        if not isinstance(challenge_id, str):
            return False, "Malformed request."
        if not isinstance(credential, dict):
            credential = {}  # a known challenge is still used up by a broken answer
        now = self.clock()
        # 1. A known challenge is used up by the first attempt, in a transaction that is always kept.
        if not self._use_up_challenge(challenge_id, "approve", now):
            return False, "Unknown or used challenge."
        # 2. Everything else happens in one transaction, rolled back as a whole on any failure.
        try:
            with self.db._tx() as conn:
                challenge = conn.execute(
                    "SELECT * FROM passkey_challenges WHERE challenge_id=?", (challenge_id,)
                ).fetchone()
                draft = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
                if draft is None:
                    raise _Rejected("draft")
                if challenge["draft_id"] != draft_id:
                    raise _Rejected("challenge belongs to another draft")
                fingerprint = self._fingerprint(conn, draft)  # calculated again, from the stored fields
                if fingerprint != challenge["body_sha256"] or fingerprint != draft["body_sha256"]:
                    raise _Rejected("draft changed after the challenge")
                if now > challenge["expires_at"] or now >= draft["expires_at"]:
                    raise _Rejected("expired")
                if draft["status"] != "pending":
                    raise _Rejected("draft not pending")
                raw_id = credential.get("rawId") or credential.get("id")
                if not isinstance(raw_id, str) or credential.get("type") != "public-key":
                    raise _Rejected("credential")
                key = conn.execute(
                    "SELECT * FROM passkey_credentials WHERE credential_id=?", (raw_id,)
                ).fetchone()
                if key is None:
                    raise _Rejected("unknown credential")
                response = credential.get("response")
                if not isinstance(response, dict):
                    raise _Rejected("response")
                client_data = json.loads(base64url_to_bytes(response["clientDataJSON"]).decode("utf-8"))
                if not isinstance(client_data, dict):
                    raise _Rejected("client data")
                if client_data.get("crossOrigin") not in (None, False):
                    raise _Rejected("cross-origin")
                if client_data.get("origin") != self.config.origin:
                    raise _Rejected("origin")
                if client_data.get("type") != "webauthn.get":
                    raise _Rejected("type")
                if client_data.get("challenge") != challenge_id:
                    raise _Rejected("challenge")
                auth_data = base64url_to_bytes(response["authenticatorData"])
                if len(auth_data) < 37:
                    raise _Rejected("authenticator data")
                if auth_data[:32] != hashlib.sha256(self.config.rp_id.encode("utf-8")).digest():
                    raise _Rejected("rp id")
                flags = auth_data[32]
                if not flags & 0x01:
                    raise _Rejected("user not present")
                if not flags & 0x04:
                    raise _Rejected("user not verified")
                received = int.from_bytes(auth_data[33:37], "big")
                stored = int(key["sign_count"])
                if received > 0 and stored > 0 and received < stored:
                    raise _Rejected("sign count went backwards")
                verification = verify_authentication_response(
                    credential=credential,
                    expected_challenge=base64url_to_bytes(challenge_id),
                    expected_rp_id=self.config.rp_id,
                    expected_origin=self.config.origin,
                    credential_public_key=bytes(key["public_key"]),
                    credential_current_sign_count=stored,
                    require_user_verification=True,
                )
                if b64url(verification.credential_id) != raw_id or not verification.user_verified:
                    raise _Rejected("verification")
                conn.execute(
                    "INSERT INTO draft_approvals(draft_id, body_sha256, credential_id, approved_at) VALUES (?,?,?,?)",
                    (draft_id, fingerprint, raw_id, now),  # a second approval of the same draft cannot be stored
                )
                conn.execute(
                    "UPDATE passkey_credentials SET sign_count=?, last_used_at=? WHERE credential_id=?",
                    (max(stored, verification.new_sign_count), now, raw_id),
                )
        except Exception as exc:
            self._audit("approval_rejected", draft_id, _audit_detail(exc))
            return False, f"Approval rejected: {type(exc).__name__}"
        self._audit("approval_done", draft_id)
        return True, "Draft approved."

    # -- links and what the approval page shows ---------------------------------------

    def approval_url(self, draft_id: str) -> str:
        return f"{self.config.origin}/approve/{draft_id}"

    def enrollment_url(self, token: str) -> str:
        return f"{self.config.origin}/passkey/enroll/{token}"

    def describe_draft(self, draft_id: str) -> Optional[Dict[str, Any]]:
        """Everything the approval page shows: recipient, subject, the FULL text, attachments with
        name and size, and where the draft stands. None if there is no such draft."""
        if not isinstance(draft_id, str) or len(draft_id) > 100:
            return None
        now = self.clock()
        with self.db._connect() as conn:
            draft = conn.execute("SELECT rowid AS rid, * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                return None
            files = conn.execute(
                "SELECT name, length(data) AS size FROM draft_attachments WHERE draft_id=? ORDER BY position",
                (draft_id,),
            ).fetchall()
            approved = conn.execute("SELECT 1 FROM draft_approvals WHERE draft_id=?", (draft_id,)).fetchone()
            keys = conn.execute("SELECT COUNT(*) FROM passkey_credentials").fetchone()[0]
            related = conn.execute(
                "SELECT rowid AS rid, id, created_at, status, subject, finished_at, sending_at, expires_at FROM drafts "
                "WHERE owner=? AND recipient_address=? AND id<>? "
                "AND status IN ('pending','sending','sent','unknown')",
                (draft["owner"], draft["recipient_address"], draft_id),
            ).fetchall()
        status = draft["status"]
        if status in ("sent", "unknown", "failed", "sending"):
            state = status
        elif approved:
            state = "approved"
        elif status != "pending":
            state = "closed"
        elif now >= draft["expires_at"]:
            state = "expired"
        elif keys == 0:
            state = "no_passkey"
        else:
            state = "open"

        def end(row) -> float:
            return row["finished_at"] if row["finished_at"] is not None else row["expires_at"]

        # Versions of one mail: drafts to the same address that were alive at the same time as this
        # one. A later, separate mail to the same person is not one of them, because the earlier
        # mail was already finished when the later draft was made.
        mine_end = draft["finished_at"] if draft["finished_at"] is not None else draft["expires_at"]
        siblings = [
            r for r in related
            if r["created_at"] <= mine_end and draft["created_at"] <= end(r)
            and (r["status"] != "pending" or r["expires_at"] > now)
        ]
        sent_siblings = [r for r in siblings if r["status"] in ("sent", "sending", "unknown")]
        other_sent = None
        if sent_siblings:
            last = max(sent_siblings, key=lambda r: r["finished_at"] or r["sending_at"] or 0)
            other_sent = {"subject": last["subject"], "at": last["finished_at"] or last["sending_at"]}
        return {
            "state": state,
            "to_name": draft["recipient_name"],
            "to_address": draft["recipient_address"],
            "subject": draft["subject"],
            "text": draft["body"],
            "fingerprint": draft["body_sha256"][:12],
            "expires_at": draft["expires_at"],
            "seconds_left": max(0, int(draft["expires_at"] - now)),
            "attachments": [{"name": f["name"], "size": f["size"]} for f in files],
            "version": 1 + sum(1 for r in siblings if (r["created_at"], r["rid"]) < (draft["created_at"], draft["rid"])),
            "versions": len(siblings) + 1,
            "other_sent": other_sent,
            "finished_at": draft["finished_at"],
        }

    def enrollment_open(self, token: str) -> bool:
        if not isinstance(token, str) or len(token) > 200:
            return False
        with self.db._connect() as conn:
            return self._open_enrollment(conn, token, self.clock()) is not None

    # -- what the send step asks ---------------------------------------------------

    def is_approved_now(self, draft_id: str, body_sha256: str) -> bool:
        with self.db._connect() as conn:
            return self.is_approved(conn, draft_id, body_sha256)

    def is_approved(self, conn: sqlite3.Connection, draft_id: str, body_sha256: str) -> bool:
        """True only if this exact draft content has been approved. Meant to run inside the send
        transaction (the connection is that transaction's)."""
        row = conn.execute(
            "SELECT body_sha256 FROM draft_approvals WHERE draft_id=?", (draft_id,)
        ).fetchone()
        return row is not None and secrets.compare_digest(row["body_sha256"], body_sha256)

    # -- internals ------------------------------------------------------------------

    def _audit(self, event: str, draft_id: str, detail: Optional[str] = None) -> None:
        """Best effort: a failing audit entry never changes the result of an approval."""
        try:
            with self.db._tx() as conn:
                row = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
                if row is not None:
                    self.db._audit(conn, self.clock(), event, self.db._draft(row), detail=detail)
        except Exception:
            pass

    @staticmethod
    def _hash(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def _open_enrollment(self, conn, token: str, now: float):
        row = conn.execute(
            "SELECT * FROM passkey_enrollments WHERE token_hash=?", (self._hash(token),)
        ).fetchone()
        if row is None or row["used_at"] is not None or now >= row["expires_at"]:
            return None
        return row

    def _use_up_challenge(self, challenge_id: str, purpose: str, now: float) -> bool:
        with self.db._tx() as conn:
            changed = conn.execute(
                "UPDATE passkey_challenges SET consumed_at=? WHERE challenge_id=? AND purpose=? AND consumed_at IS NULL",
                (now, challenge_id, purpose),
            ).rowcount
        return changed == 1

    def _fingerprint(self, conn, draft: sqlite3.Row) -> str:
        """The fingerprint of the stored draft, calculated again. It must equal the stored one."""
        attachments = [
            SimpleNamespace(name=r["name"], sha256=r["sha256"])
            for r in conn.execute(
                "SELECT name, sha256 FROM draft_attachments WHERE draft_id=? ORDER BY position", (draft["id"],)
            )
        ]
        value = body_hash(draft["subject"], draft["body"], draft["recipient_address"], attachments)
        if value != draft["body_sha256"]:
            raise _Rejected("stored fingerprint")
        return value
