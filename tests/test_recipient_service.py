"""Claude's side of the recipient registry: suggest and look, never approve."""

import asyncio
import dataclasses
import json

import pytest

from outbox_mcp.config import Config
from outbox_mcp.errors import RateLimitExceeded, RecipientNotAllowed, ValidationError
from outbox_mcp.server import build_server, build_service
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

DAY = 86400.0
BASE = {
    "OUTBOX_SMTP_HOST": "smtp.example.com", "OUTBOX_SMTP_USER": "me@example.com",
    "OUTBOX_SMTP_PASSWORD": "pw", "OUTBOX_FROM_ADDRESS": "me@example.com",
}


@pytest.fixture
def store(db):
    s = RecipientStore(db)
    s.add("Me", "me@example.com", ["ich"], 1.0, "admin:test")
    return s


@pytest.fixture
def svc(config, db, sender, clock, store):
    return OutboxService(config, DbRecipientList(store), db, sender, clock, recipient_store=store)


# -- suggesting through the service --------------------------------------------------

def test_suggestion_waits_and_cannot_be_mailed(svc, store):
    r = svc.suggest_recipient("o", "Ferdinand", "ferdinand@example.org", "Neffe")
    assert r["status"] == "suggested" and r["address"] == "ferdinand@example.org"
    assert "outside this chat" in r["instructions"] and "approved" in r["instructions"]
    with pytest.raises(RecipientNotAllowed):
        svc.prepare_email("o", "Ferdinand", "Hallo", "Text")
    with pytest.raises(RecipientNotAllowed):
        svc.prepare_email("o", "ferdinand@example.org", "Hallo", "Text")


def test_after_approval_the_mail_flows_as_before(svc, store, sender):
    svc.suggest_recipient("o", "Ferdinand", "ferdinand@example.org")
    store.approve("ferdinand@example.org", 2000.0, "admin:test", aliases=["Ferdi"], scope="own")
    draft = svc.prepare_email("o", "Ferdi", "Hallo", "Text")
    assert draft["to_address"] == "ferdinand@example.org"
    assert svc.send_email("o", draft["draft_id"])["status"] == "sent"
    assert sender.sent[0][2] == "ferdinand@example.org"


def test_blocking_stops_an_already_prepared_draft(svc, store, sender):
    store.add("Anna", "anna@example.org", [], 2.0, "admin:test")
    draft = svc.prepare_email("o", "Anna", "Hallo", "Text")
    store.block("anna@example.org", 3.0, "admin:test")
    with pytest.raises(RecipientNotAllowed):
        svc.send_email("o", draft["draft_id"])
    assert sender.sent == []


def test_known_address_is_reported_not_suggested(svc):
    r = svc.suggest_recipient("o", "Me again", "me@example.com")
    assert r["status"] == "already_allowed"
    assert svc.list_suggestions("o")["suggestions"] == []


def test_invalid_suggestions_are_refused(svc):
    with pytest.raises(ValidationError):
        svc.suggest_recipient("o", "X", "not an address")


def test_list_suggestions_shows_only_own_and_current_status(svc, store):
    svc.suggest_recipient("o", "Ferdinand", "ferdinand@example.org", "Neffe")
    svc.suggest_recipient("other", "Anna", "anna@example.org")
    mine = svc.list_suggestions("o")["suggestions"]
    assert [(s["name"], s["address"], s["status"]) for s in mine] == [
        ("Ferdinand", "ferdinand@example.org", "suggested")]
    store.approve("ferdinand@example.org", 2000.0, "admin:test")
    assert svc.list_suggestions("o")["suggestions"][0]["status"] == "active"


def test_suggestions_expire_through_housekeeping(svc, clock):
    svc.suggest_recipient("o", "Ferdinand", "ferdinand@example.org")
    clock.t += 31 * DAY
    assert svc.list_suggestions("o")["suggestions"][0]["status"] == "expired"


def test_open_suggestion_limit(config, db, sender, clock, store):
    tight = dataclasses.replace(config, max_open_suggestions=1)
    svc = OutboxService(tight, DbRecipientList(store), db, sender, clock, recipient_store=store)
    svc.suggest_recipient("o", "A", "a@example.org")
    with pytest.raises(RateLimitExceeded):
        svc.suggest_recipient("o", "B", "b@example.org")


def test_service_without_a_store_refuses_suggestions(config, recipients, db, sender, clock):
    plain = OutboxService(config, recipients, db, sender, clock)
    with pytest.raises(RecipientNotAllowed, match="not enabled"):
        plain.suggest_recipient("o", "A", "a@example.org")


def test_suggestion_settings_from_env():
    c = Config.from_env(BASE)
    assert (c.max_open_suggestions, c.max_suggestions_per_day, c.suggestion_ttl_days) == (5, 10, 30)
    c = Config.from_env({**BASE, "OUTBOX_MAX_OPEN_SUGGESTIONS": "2", "OUTBOX_SUGGESTION_TTL_DAYS": "7"})
    assert (c.max_open_suggestions, c.suggestion_ttl_days) == (2, 7)


# -- the tools Claude can call --------------------------------------------------------

def call(mcp, name, arguments):
    return json.loads(asyncio.run(mcp.call_tool(name, arguments))[0].text)


def test_claude_can_suggest_and_look_but_has_no_admin_tool(svc):
    tools = {t.name: t for t in asyncio.run(build_server(svc).list_tools())}
    assert set(tools) == {"list_recipients", "prepare_email", "send_email", "cancel_draft",
                          "suggest_recipient", "list_suggestions", "list_attachments", "draft_status"}
    assert tools["suggest_recipient"].annotations.destructiveHint is False
    assert tools["list_suggestions"].annotations.readOnlyHint is True
    assert tools["list_attachments"].annotations.readOnlyHint is True
    assert tools["list_attachments"].annotations.destructiveHint in (None, False)
    text = tools["suggest_recipient"].description
    assert "told you" in text and "letter by letter" in text


def test_suggest_tool_end_to_end(svc):
    mcp = build_server(svc)
    r = call(mcp, "suggest_recipient", {"name": "Ferdinand", "address": "ferdinand@example.org", "note": "Neffe"})
    assert r["status"] == "suggested"
    listed = call(mcp, "list_suggestions", {})
    assert listed["suggestions"][0]["address"] == "ferdinand@example.org"


# -- starting up: the old file is imported once ---------------------------------------

def test_build_service_imports_the_file_once_and_then_uses_the_database(tmp_path):
    rec_file = tmp_path / "r.json"
    rec_file.write_text(json.dumps({"recipients": [{"name": "Me", "address": "me@example.com", "aliases": ["ich"]}]}))
    cfg = Config.from_env({**BASE, "OUTBOX_RECIPIENTS_FILE": str(rec_file), "OUTBOX_DB_PATH": str(tmp_path / "m.db")})
    first = build_service(cfg)
    assert [r["name"] for r in first.list_recipients()["recipients"]] == ["Me"]
    rec_file.write_text(json.dumps({"recipients": [{"name": "Other", "address": "o@example.com"}]}))
    second = build_service(cfg)
    assert [r["name"] for r in second.list_recipients()["recipients"]] == ["Me"]
    rec_file.unlink()
    third = build_service(cfg)
    assert [r["name"] for r in third.list_recipients()["recipients"]] == ["Me"]


def test_build_service_starts_with_an_empty_list_if_there_is_no_file(tmp_path):
    cfg = Config.from_env({**BASE, "OUTBOX_RECIPIENTS_FILE": str(tmp_path / "none.json"),
                           "OUTBOX_DB_PATH": str(tmp_path / "m.db")})
    assert build_service(cfg).list_recipients()["recipients"] == []
