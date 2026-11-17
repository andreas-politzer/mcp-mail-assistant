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
import sqlite3
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
    AuthenticatorAttachment,
    AuthenticatorSelectionCriteria,
    PublicKeyCredentialDescriptor,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)

from ..errors import ConfigError
from ..storage.database import Database, body_hash

MAX_PASSKEYS = 2
ENROLLMENT_SECONDS = 600
DEFAULT_CHALLENGE_SECONDS = 300


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
        return cls(rp_id=host, challenge_seconds=seconds, port=port)


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
                "SELECT credential_id, created_at, last_used_at FROM passkey_credentials ORDER BY created_at"
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

    def enroll_options(self, token: str) -> Tuple[bool, Any]:
        now = self.clock()
        if not isinstance(token, str):
            return False, "This link is not valid."
        with self.db._tx() as conn:
            row = self._open_enrollment(conn, token, now)
            if row is None:
                return False, "This link is not valid or has expired."
            if conn.execute("SELECT COUNT(*) FROM passkey_credentials").fetchone()[0] >= MAX_PASSKEYS:
                return False, "The maximum number of passkeys is reached."
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
            user_name="owner",
            user_display_name="Mail Assistant owner",
            challenge=challenge,
            exclude_credentials=existing or None,
            authenticator_selection=AuthenticatorSelectionCriteria(
                authenticator_attachment=AuthenticatorAttachment.PLATFORM,
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
                if conn.execute("SELECT COUNT(*) FROM passkey_credentials").fetchone()[0] >= MAX_PASSKEYS:
                    raise _Rejected("limit")
                verified = verify_registration_response(
                    credential=credential,
                    expected_challenge=base64url_to_bytes(challenge_id),
                    expected_rp_id=self.config.rp_id,
                    expected_origin=self.config.origin,
                    require_user_verification=True,
                )
                conn.execute(
                    "INSERT INTO passkey_credentials(credential_id, public_key, sign_count, created_at)"
                    " VALUES (?,?,?,?)",
                    (b64url(verified.credential_id), verified.credential_public_key, verified.sign_count, now),
                )
                conn.execute("UPDATE passkey_enrollments SET used_at=? WHERE token_hash=?", (now, row["token_hash"]))
        except Exception as exc:  # the transaction is rolled back as a whole
            return False, f"Enrollment rejected: {type(exc).__name__}"
        return True, "Passkey registered."

    # -- approving a draft --------------------------------------------------------

    def approve_options(self, draft_id: str) -> Tuple[bool, Any]:
        now = self.clock()
        if not isinstance(draft_id, str):
            return False, "Draft not found."
        with self.db._tx() as conn:
            draft = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                return False, "Draft not found."
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
        self._audit("approval_issued", draft_id)
        options = generate_authentication_options(
            rp_id=self.config.rp_id,
            challenge=challenge,
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
            self._audit("approval_rejected", draft_id, type(exc).__name__ + (f": {exc}" if isinstance(exc, _Rejected) else ""))
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
            draft = conn.execute("SELECT * FROM drafts WHERE id=?", (draft_id,)).fetchone()
            if draft is None:
                return None
            files = conn.execute(
                "SELECT name, length(data) AS size FROM draft_attachments WHERE draft_id=? ORDER BY position",
                (draft_id,),
            ).fetchall()
            approved = conn.execute("SELECT 1 FROM draft_approvals WHERE draft_id=?", (draft_id,)).fetchone()
            keys = conn.execute("SELECT COUNT(*) FROM passkey_credentials").fetchone()[0]
        if approved:
            state = "approved"
        elif draft["status"] != "pending":
            state = "closed"
        elif now >= draft["expires_at"]:
            state = "expired"
        elif keys == 0:
            state = "no_passkey"
        else:
            state = "open"
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
