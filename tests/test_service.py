import dataclasses
import sqlite3
import threading
from email import message_from_bytes, policy

import pytest

from outbox_mcp.errors import (
    DraftNotFound, DraftNotSendable, RateLimitExceeded, RecipientNotAllowed, ValidationError,
)
from outbox_mcp.mail.smtp_client import SmtpSendFailed, SmtpSendUnknown
from outbox_mcp.security.recipients import Recipient, RecipientList
from outbox_mcp.service import OutboxService


@pytest.fixture
def svc(config, recipients, db, sender, clock):
    return OutboxService(config, recipients, db, sender, clock)


def prep(svc, to="Ferdinand", subject="Hallo", text="Text"):
    return svc.prepare_email("o", to, subject, text)


def test_happy_path(svc, sender):
    d = prep(svc, text="Grüße aus dem Park")
    assert d["to_address"] == "ferdinand@example.org" and "send_email" in d["instructions"]
    assert sender.sent == []
    r = svc.send_email("o", d["draft_id"])
    assert r["status"] == "sent"
    msg, frm, to = sender.sent[0]
    assert (frm, to) == ("me@example.com", "ferdinand@example.org")
    assert msg["Bcc"] is None and msg["Cc"] is None
    assert "Grüße aus dem Park" in msg.get_content()


def test_double_send_refused(svc, sender):
    d = prep(svc)
    svc.send_email("o", d["draft_id"])
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", d["draft_id"])
    assert len(sender.sent) == 1


def test_failed_is_final(svc, sender):
    sender.error = SmtpSendFailed("refused")
    d = prep(svc)
    assert svc.send_email("o", d["draft_id"])["status"] == "failed"
    sender.error = None
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", d["draft_id"])
    assert sender.sent == []


def test_unknown_no_retry(svc, sender):
    sender.error = SmtpSendUnknown("dropped")
    d = prep(svc)
    r = svc.send_email("o", d["draft_id"])
    assert r["status"] == "unknown" and "Do NOT retry" in r["note"]
    sender.error = None
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", d["draft_id"])


def test_unexpected_exception_is_unknown(svc, sender):
    sender.error = RuntimeError("boom")
    d = prep(svc)
    assert svc.send_email("o", d["draft_id"])["status"] == "unknown"


def test_unknown_recipient_refused(svc):
    with pytest.raises(RecipientNotAllowed):
        prep(svc, to="evil@example.com")


def test_validation_applies(svc):
    with pytest.raises(ValidationError):
        prep(svc, subject="a\nBcc: x@y.de")


def test_expiry(svc, clock, sender):
    d = prep(svc)
    clock.t += 601
    with pytest.raises(DraftNotSendable) as e:
        svc.send_email("o", d["draft_id"])
    assert e.value.status == "expired" and "expired" in str(e.value)
    assert sender.sent == []


def test_cancel(svc, sender):
    d = prep(svc)
    assert svc.cancel_draft("o", d["draft_id"])["status"] == "cancelled"
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", d["draft_id"])


def test_owner_isolation(svc, sender):
    d = prep(svc)
    with pytest.raises(DraftNotFound):
        svc.send_email("someone-else", d["draft_id"])
    with pytest.raises(DraftNotFound):
        svc.cancel_draft("someone-else", d["draft_id"])
    assert sender.sent == []
    assert svc.send_email("o", d["draft_id"])["status"] == "sent"


def test_allowlist_change_between_prepare_and_send(svc, sender):
    d = prep(svc)
    svc.recipients = RecipientList([Recipient("Me", "me@example.com")])
    with pytest.raises(RecipientNotAllowed):
        svc.send_email("o", d["draft_id"])
    assert sender.sent == []


def test_send_limit(svc, config, sender):
    svc.config = dataclasses.replace(config, max_sends_per_hour=1)
    a, b = prep(svc), prep(svc)
    svc.send_email("o", a["draft_id"])
    with pytest.raises(RateLimitExceeded):
        svc.send_email("o", b["draft_id"])
    assert len(sender.sent) == 1


def test_list_recipients(svc):
    names = [r["name"] for r in svc.list_recipients()["recipients"]]
    assert names == ["Me", "Ferdinand"]


def test_text_that_looks_like_headers_stays_in_the_body(svc, sender):
    d = prep(svc, subject="Hi", text="x\r\nBcc: evil@example.com\r\nTo: evil@example.com")
    svc.send_email("o", d["draft_id"])
    parsed = message_from_bytes(sender.sent[0][0].as_bytes(policy=policy.SMTP), policy=policy.default)
    assert parsed.get_all("Bcc") is None
    assert parsed.get_all("To") == ["Ferdinand <ferdinand@example.org>"]
    assert "Bcc: evil@example.com" in parsed.get_content()  # only as plain text in the body
    assert sorted(parsed.keys()) == sorted(
        ["From", "To", "Subject", "Date", "Message-ID", "Content-Type",
         "Content-Transfer-Encoding", "MIME-Version"]
    )


def test_builder_refuses_header_injection():
    from outbox_mcp.mail.message_builder import build_message
    with pytest.raises(ValueError):
        build_message("me@example.com", "Me", "you@example.org", "Evil\r\nBcc: x@y.de", "S", "b")
    with pytest.raises(ValueError):
        build_message("me@example.com", "Me", "you@example.org", "You", "S\r\nBcc: x@y.de", "b")


# ---- B2: the database fails after the mail server already answered -------------------


class FinishFails:
    """Replaces db.finish; counts calls so we can prove there is no retry."""

    def __init__(self):
        self.calls = 0

    def __call__(self, *args, **kwargs):
        self.calls += 1
        raise sqlite3.OperationalError("database is locked")


def test_sent_but_status_not_stored(svc, sender, db, monkeypatch, capsys):
    d = prep(svc)
    boom = FinishFails()
    monkeypatch.setattr(svc.db, "finish", boom)
    r = svc.send_email("o", d["draft_id"])
    assert r["status"] == "sent_but_not_recorded" and r["status"] != "sent"
    assert r["status_recorded"] is False
    assert "ACCEPTED" in r["note"] and "Do NOT send it again" in r["note"]
    assert boom.calls == 1  # no automatic retry
    assert len(sender.sent) == 1
    assert "could not store status 'sent'" in capsys.readouterr().err
    # the draft stays locked: no second send is possible
    with pytest.raises(DraftNotSendable) as e:
        svc.send_email("o", d["draft_id"])
    assert e.value.status == "sending"
    assert len(sender.sent) == 1
    # after a restart the draft is 'unknown', still not sendable
    monkeypatch.undo()
    assert db.recover_interrupted(2_000_000.0) == 1
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", d["draft_id"])
    assert len(sender.sent) == 1


@pytest.mark.parametrize("error, expected", [
    (SmtpSendFailed("refused"), "failed"),
    (SmtpSendUnknown("dropped"), "unknown"),
    (RuntimeError("boom"), "unknown"),
])
def test_other_branches_when_status_not_stored(svc, sender, monkeypatch, error, expected):
    sender.error = error
    d = prep(svc)
    boom = FinishFails()
    monkeypatch.setattr(svc.db, "finish", boom)
    r = svc.send_email("o", d["draft_id"])
    assert r["status"] == expected
    assert r["status_recorded"] is False
    assert "could not be stored" in r["note"]
    assert boom.calls == 1
    sender.error = None
    with pytest.raises(DraftNotSendable):
        svc.send_email("o", d["draft_id"])
    assert sender.sent == []


def test_recorded_flag_true_in_normal_cases(svc, sender):
    assert svc.send_email("o", prep(svc)["draft_id"])["status_recorded"] is True
    sender.error = SmtpSendFailed("x")
    assert svc.send_email("o", prep(svc)["draft_id"])["status_recorded"] is True


def test_concurrent_send_sends_one_mail(svc, sender):
    d = prep(svc)
    results = []

    def go():
        try:
            results.append(svc.send_email("o", d["draft_id"])["status"])
        except DraftNotSendable:
            results.append("refused")

    threads = [threading.Thread(target=go) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("sent") == 1 and results.count("refused") == 7
    assert len(sender.sent) == 1
