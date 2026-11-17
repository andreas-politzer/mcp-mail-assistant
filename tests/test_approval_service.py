"""Passkey approval with real ES256 signatures (no mocked crypto). A software authenticator
builds registration and assertion answers the way a device does."""

import base64
import hashlib
import json
import os
import sqlite3
import threading

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec

from approval_helpers import Device, ORIGIN, RP_ID, b64
from outbox_mcp.approval.service import ApprovalConfig, ApprovalService, MAX_PASSKEYS
from outbox_mcp.errors import ConfigError
from outbox_mcp.security.rate_limiter import RateLimits

NOW = 1_000_000.0


class Env:
    def __init__(self, db):
        self.db = db
        self.t = NOW
        self.svc = ApprovalService(db, ApprovalConfig(rp_id=RP_ID), lambda: self.t)

    def enroll(self, device):
        token = self.svc.create_enrollment()
        ok, opts = self.svc.enroll_options(token)
        assert ok, opts
        cid = opts["challenge_id"]
        return self.svc.enroll_verify(token, {"challenge_id": cid, "credential": device.register(cid)})

    def draft(self, subject="Hallo", body="Text", address="x@example.org"):
        return self.db.create_draft(
            "o", "X", address, subject, body, self.t, 600, RateLimits(100, 100, 100),
        )

    def options(self, draft):
        ok, opts = self.svc.approve_options(draft.id)
        assert ok, opts
        return opts["challenge_id"]

    def approved(self, draft):
        with sqlite3.connect(self.db.path) as c:
            return c.execute("SELECT COUNT(*) FROM draft_approvals WHERE draft_id=?", (draft.id,)).fetchone()[0]

    def challenge_used(self, cid):
        with sqlite3.connect(self.db.path) as c:
            return c.execute("SELECT consumed_at FROM passkey_challenges WHERE challenge_id=?", (cid,)).fetchone()[0] is not None


@pytest.fixture
def env(db):
    return Env(db)


@pytest.fixture
def device(env):
    d = Device()
    ok, msg = env.enroll(d)
    assert ok, msg
    return d


# -- config ---------------------------------------------------------------------

@pytest.mark.parametrize("host", ["", "relay", "https://relay.example.org", "relay.example.org:8443",
                                  "relay.example.org/x", "*.example.org", "Relay.Example.org", ".example.org",
                                  "a..b.org", "relay.example.org.", "re lay.example.org", "relay.exämple.org"])
def test_bad_hosts_refused(host):
    with pytest.raises(ConfigError):
        ApprovalConfig(rp_id=host)


def test_config_from_env():
    assert ApprovalConfig.from_env({}) is None
    cfg = ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": RP_ID})
    assert cfg.origin == ORIGIN and cfg.challenge_seconds == 300
    with pytest.raises(ConfigError):
        ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": RP_ID, "OUTBOX_APPROVAL_CHALLENGE_SECONDS": "5"})
    with pytest.raises(ConfigError):
        ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": RP_ID, "OUTBOX_APPROVAL_CHALLENGE_SECONDS": "x"})


# -- enrollment -------------------------------------------------------------------

def test_enrollment_works_once(env):
    token = env.svc.create_enrollment()
    d = Device()
    ok, opts = env.svc.enroll_options(token)
    cid = opts["challenge_id"]
    assert opts["publicKey"]["authenticatorSelection"]["userVerification"] == "required"
    ok, msg = env.svc.enroll_verify(token, {"challenge_id": cid, "credential": d.register(cid)})
    assert ok, msg
    assert env.svc.passkey_count() == 1
    assert env.svc.enroll_options(token)[0] is False  # the link is used up


def test_enrollment_token_expires(env):
    token = env.svc.create_enrollment()
    env.t += 601
    assert env.svc.enroll_options(token)[0] is False


def test_unknown_token_refused(env):
    assert env.svc.enroll_options("nope")[0] is False
    assert env.svc.enroll_options(None)[0] is False


def test_third_passkey_refused(env):
    for _ in range(MAX_PASSKEYS):
        ok, msg = env.enroll(Device())
        assert ok, msg
    assert env.svc.create_enrollment() is None


def test_third_passkey_refused_even_with_older_token(env):
    token = env.svc.create_enrollment()
    ok, opts = env.svc.enroll_options(token)
    for _ in range(MAX_PASSKEYS):
        assert env.enroll(Device())[0]
    d = Device()
    cid = opts["challenge_id"]
    ok, _ = env.svc.enroll_verify(token, {"challenge_id": cid, "credential": d.register(cid)})
    assert not ok and env.svc.passkey_count() == MAX_PASSKEYS


def test_existing_credentials_are_excluded_and_handle_is_random(env, device):
    token = env.svc.create_enrollment()
    _, a = env.svc.enroll_options(token)
    _, b = env.svc.enroll_options(token)
    assert a["publicKey"]["excludeCredentials"][0]["id"] == b64(device.cred_id)
    assert a["publicKey"]["user"]["id"] != b["publicKey"]["user"]["id"]


@pytest.mark.parametrize("kwargs", [{"origin": "https://evil.example.org"}, {"flags": 0x41}])
def test_enrollment_wrong_origin_or_no_uv_refused(env, kwargs):
    token = env.svc.create_enrollment()
    _, opts = env.svc.enroll_options(token)
    cid = opts["challenge_id"]
    ok, _ = env.svc.enroll_verify(token, {"challenge_id": cid, "credential": Device().register(cid, **kwargs)})
    assert not ok and env.svc.passkey_count() == 0
    # the challenge is gone, a retry with it fails
    ok, _ = env.svc.enroll_verify(token, {"challenge_id": cid, "credential": Device().register(cid)})
    assert not ok and env.svc.passkey_count() == 0


def test_enrollment_challenge_of_other_token_refused(env):
    t1, t2 = env.svc.create_enrollment(), env.svc.create_enrollment()
    _, opts = env.svc.enroll_options(t1)
    cid = opts["challenge_id"]
    ok, _ = env.svc.enroll_verify(t2, {"challenge_id": cid, "credential": Device().register(cid)})
    assert not ok and env.svc.passkey_count() == 0


def test_enrollment_malformed_payload(env):
    token = env.svc.create_enrollment()
    for bad in (None, [], {}, {"challenge_id": 1, "credential": {}}, {"challenge_id": "x", "credential": "y"}):
        assert env.svc.enroll_verify(token, bad)[0] is False


def test_remove_passkey(env, device):
    cid = b64(device.cred_id)
    with pytest.raises(ConfigError):
        env.svc.remove_passkey("abc")
    assert env.svc.remove_passkey(cid[:8]) is True
    assert env.svc.passkey_count() == 0


# -- approval: the good path ---------------------------------------------------------

def test_valid_approval(env, device):
    d = env.draft()
    cid = env.options(d)
    ok, msg = env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": device.assert_(cid)})
    assert ok, msg
    assert env.approved(d) == 1
    with env.db._connect() as conn:
        assert env.svc.is_approved(conn, d.id, d.body_sha256)
        assert not env.svc.is_approved(conn, d.id, "0" * 64)
        assert not env.svc.is_approved(conn, "other", d.body_sha256)


def test_no_passkey_no_options(env):
    assert env.svc.approve_options(env.draft().id)[0] is False


def test_options_for_unknown_or_bad_draft(env, device):
    assert env.svc.approve_options("drf_nope")[0] is False
    assert env.svc.approve_options(None)[0] is False


# -- approval: every failure leaves nothing behind ---------------------------------

def reject(env, device, d, make, *, expect_used=True):
    cid = env.options(d)
    cred = make(cid)
    ok, _ = env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": cred})
    assert not ok
    assert env.approved(d) == 0
    assert env.challenge_used(cid) is expect_used
    # the same challenge can never be replayed, not even with a good answer
    ok, _ = env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": device.assert_(cid)})
    assert not ok and env.approved(d) == 0


def test_wrong_origin(env, device):
    reject(env, device, env.draft(), lambda c: device.assert_(c, origin="https://evil.example.org"))


def test_wrong_rp_id_hash(env, device):
    reject(env, device, env.draft(), lambda c: device.assert_(c, rp_id="evil.example.org"))


def test_wrong_ceremony_type(env, device):
    reject(env, device, env.draft(), lambda c: device.assert_(c, ctype="webauthn.create"))


def test_cross_origin(env, device):
    reject(env, device, env.draft(), lambda c: device.assert_(c, cross_origin=True))


def test_user_not_present(env, device):
    reject(env, device, env.draft(), lambda c: device.assert_(c, flags=0x04))


def test_user_not_verified(env, device):
    reject(env, device, env.draft(), lambda c: device.assert_(c, flags=0x01))


def test_tampered_signature(env, device):
    reject(env, device, env.draft(), lambda c: device.assert_(c, tamper=True))


def test_wrong_challenge_in_answer(env, device):
    reject(env, device, env.draft(), lambda c: device.assert_(b64(b"other-challenge-value-0123456789ab")))


def test_unknown_credential(env, device):
    stranger = Device()
    reject(env, device, env.draft(), lambda c: stranger.assert_(c))


def test_wrong_credential_type(env, device):
    def make(c):
        cred = device.assert_(c)
        cred["type"] = "password"
        return cred
    reject(env, device, env.draft(), make)


def test_malformed_credential_does_not_crash(env, device):
    d = env.draft()
    cid = env.options(d)
    for bad in ({}, {"id": 1}, {"rawId": "x", "type": "public-key", "response": "no"},
                {"rawId": b64(device.cred_id), "type": "public-key", "response": {"clientDataJSON": "!!"}}):
        cid = env.options(d) if env.challenge_used(cid) else cid
        assert env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": bad})[0] is False
    assert env.approved(d) == 0
    assert env.svc.approve_verify(d.id, None)[0] is False
    assert env.svc.approve_verify(d.id, {"credential": {}})[0] is False
    assert env.svc.approve_verify(None, {})[0] is False


def test_challenge_of_other_draft(env, device):
    a, b = env.draft(), env.draft(subject="B")
    cid = env.options(a)
    ok, _ = env.svc.approve_verify(b.id, {"challenge_id": cid, "credential": device.assert_(cid)})
    assert not ok and env.approved(a) == 0 and env.approved(b) == 0


def test_expired_challenge(env, device):
    d = env.draft()
    cid = env.options(d)
    env.t += 301
    ok, _ = env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": device.assert_(cid)})
    assert not ok and env.approved(d) == 0


def test_challenge_never_outlives_draft(env, device):
    d = env.draft()
    env.t += 500
    cid = env.options(d)  # the draft has 100 s left
    env.t += 101
    ok, _ = env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": device.assert_(cid)})
    assert not ok and env.approved(d) == 0


def test_expired_draft_gets_no_options(env, device):
    d = env.draft()
    env.t += 601
    assert env.svc.approve_options(d.id)[0] is False


def test_draft_changed_after_challenge(env, device):
    d = env.draft()
    cid = env.options(d)
    with sqlite3.connect(env.db.path) as c:
        c.execute("DROP TRIGGER drafts_content_immutable")  # an attacker with raw database access
        c.execute("UPDATE drafts SET body='Anderer Text' WHERE id=?", (d.id,))
    ok, _ = env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": device.assert_(cid)})
    assert not ok and env.approved(d) == 0
    assert env.svc.approve_options(d.id)[0] is False  # stored fingerprint no longer matches


def test_attachment_change_after_challenge(env, device):
    from outbox_mcp.security.attachments import Attachment
    att = Attachment(name="a.txt", data=b"abc", sha256=hashlib.sha256(b"abc").hexdigest(), source="")
    d = env.db.create_draft("o", "X", "x@example.org", "S", "B", env.t, 600, RateLimits(100, 100, 100), attachments=[att])
    cid = env.options(d)
    with sqlite3.connect(env.db.path) as c:
        for (name,) in c.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='draft_attachments'").fetchall():
            c.execute(f"DROP TRIGGER {name}")  # an attacker with raw database access
        c.execute("UPDATE draft_attachments SET sha256=? WHERE draft_id=?", ("f" * 64, d.id))
    ok, _ = env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": device.assert_(cid)})
    assert not ok and env.approved(d) == 0


def test_cancelled_draft_cannot_be_approved(env, device):
    d = env.draft()
    cid = env.options(d)
    with sqlite3.connect(env.db.path) as c:
        c.execute("UPDATE drafts SET status='cancelled' WHERE id=?", (d.id,))
    ok, _ = env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": device.assert_(cid)})
    assert not ok and env.approved(d) == 0


def test_double_approval_refused(env, device):
    d = env.draft()
    c1 = env.options(d)
    assert env.svc.approve_verify(d.id, {"challenge_id": c1, "credential": device.assert_(c1)})[0]
    assert env.svc.approve_options(d.id)[0] is False
    with sqlite3.connect(env.db.path) as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE draft_approvals SET body_sha256='x' WHERE draft_id=?", (d.id,))


def test_failed_verification_rolls_back_sign_count(env, device):
    d = env.draft()
    cid = env.options(d)
    env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": device.assert_(cid, counter=9, tamper=True)})
    with sqlite3.connect(env.db.path) as c:
        assert c.execute("SELECT sign_count, last_used_at FROM passkey_credentials").fetchone() == (0, None)


def test_sign_count_rules(env, device):
    d1, d2, d3 = env.draft(subject="1"), env.draft(subject="2"), env.draft(subject="3")
    c = env.options(d1)
    assert env.svc.approve_verify(d1.id, {"challenge_id": c, "credential": device.assert_(c, counter=5)})[0]
    c = env.options(d2)
    ok, _ = env.svc.approve_verify(d2.id, {"challenge_id": c, "credential": device.assert_(c, counter=3)})
    assert not ok  # moved backwards
    c = env.options(d3)
    ok, _ = env.svc.approve_verify(d3.id, {"challenge_id": c, "credential": device.assert_(c, counter=0)})
    assert not ok  # once a device counts, a reset to zero looks like a clone
    c = env.options(d3)
    assert env.svc.approve_verify(d3.id, {"challenge_id": c, "credential": device.assert_(c, counter=6)})[0]


def test_parallel_verify_exactly_one_wins(env, device):
    d = env.draft()
    cid = env.options(d)
    cred = device.assert_(cid)
    results = []
    barrier = threading.Barrier(6)

    def worker():
        barrier.wait()
        results.append(env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": cred})[0])

    threads = [threading.Thread(target=worker) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count(True) == 1 and env.approved(d) == 1


def test_purge_removes_approval_data(env, device):
    d = env.draft()
    cid = env.options(d)
    assert env.svc.approve_verify(d.id, {"challenge_id": cid, "credential": device.assert_(cid)})[0]
    with sqlite3.connect(env.db.path) as c:
        c.execute("UPDATE drafts SET status='expired', finished_at=? WHERE id=?", (env.t, d.id))
    env.t += 10 * 86400
    env.db.purge(env.t, 1)
    assert env.approved(d) == 0
