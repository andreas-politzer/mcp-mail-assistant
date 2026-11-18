"""The approval page sends the mail itself right after the passkey check.

What the person needs: read in peace, approve whenever it suits them (until the draft expires), and
have the mail leave at that moment, without telling the assistant anything. What must stay true: the
passkey is still checked, the draft is still claimed once inside the existing transaction, and
several versions of one mail cannot go out by accident."""

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from approval_helpers import Device
from outbox_mcp.approval.service import ApprovalConfig, ApprovalService
from outbox_mcp.approval.web import AttemptLimiter, build_approval_routes
from outbox_mcp.errors import ConfigError
from outbox_mcp.mail.smtp_client import SmtpSendFailed, SmtpSendUnknown
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

OWN = "me@example.com"
EXT = "ferdinand@example.org"
HOST = "relay.example.org"


class World:
    def __init__(self, config, sender, clock, sends=True, wired=True):
        self.clock, self.sender = clock, sender
        self.db = Database(config.db_path)
        self.store = RecipientStore(self.db)
        self.store.add("Me", OWN, ["myself"], 1.0, "admin:test", scope="own")
        self.store.add("Ferdinand", EXT, [], 1.0, "admin:test")
        self.store.add("Erna", "erna@example.org", [], 1.0, "admin:test")
        cfg = ApprovalConfig(rp_id=HOST, sends=sends)
        self.approval = ApprovalService(self.db, cfg, clock)
        self.svc = OutboxService(
            config, DbRecipientList(self.store), self.db, sender, clock,
            recipient_store=self.store, approval=self.approval,
        )
        self.device = Device(rp_id=cfg.rp_id, origin=cfg.origin)
        token = self.approval.create_enrollment()
        _, opts = self.approval.enroll_options(token)
        cid = opts["challenge_id"]
        ok, msg = self.approval.enroll_verify(token, {"challenge_id": cid, "credential": self.device.register(cid)})
        assert ok, msg
        send = self.svc.send_after_approval if wired else None
        app = Starlette(routes=build_approval_routes(self.approval, AttemptLimiter(clock), send=send))
        self.client = TestClient(app, base_url=cfg.origin)
        self.hdr = {"Origin": cfg.origin, "Content-Type": "application/json"}

    def draft(self, who="Ferdinand", subject="S", body="T"):
        return self.svc.prepare_email("o", who, subject, body)["draft_id"]

    def click(self, draft_id):
        """What the page does: options, passkey, verify. Returns the JSON answer."""
        cid = self.client.post(f"/approve/{draft_id}/options", headers=self.hdr, json={}).json()["challenge_id"]
        return self.client.post(
            f"/approve/{draft_id}/verify", headers=self.hdr,
            json={"challenge_id": cid, "credential": self.device.assert_(cid)},
        ).json()

    def page(self, draft_id):
        return self.client.get(f"/approve/{draft_id}").text


@pytest.fixture
def world(config, sender, clock):
    return World(config, sender, clock)


# -- the click sends ---------------------------------------------------------------------------

def test_the_click_sends_the_mail_and_the_page_says_so(world, sender):
    d = world.draft()
    answer = world.click(d)
    assert answer["ok"] and answer["outcome"] == "sent" and answer["title"] == "Mail sent"
    assert EXT in answer["detail"]
    assert len(sender.sent) == 1 and sender.sent[0][2] == EXT


def test_the_person_can_take_hours_to_read(world, sender, clock):
    d = world.draft()
    assert "Approve and send with passkey" in world.page(d)
    clock.t += 3 * 3600
    assert "Approve and send with passkey" in world.page(d)
    assert world.click(d)["outcome"] == "sent" and len(sender.sent) == 1


def test_a_draft_still_expires_after_a_day(world, sender, clock):
    d = world.draft()
    clock.t += 24 * 3600 + 1
    assert "expired" in world.page(d)
    assert world.client.post(f"/approve/{d}/options", headers=world.hdr, json={}).status_code == 400
    assert sender.sent == []


def test_the_page_names_the_remaining_time_in_hours(world):
    d = world.draft()
    assert "more hours" in world.page(d)


def test_a_second_click_sends_nothing_more(world, sender):
    d = world.draft()
    world.click(d)
    page = world.page(d)
    assert "This mail was sent to" in page and f"{EXT}" in page
    assert "Approve and send" not in page
    assert world.client.post(f"/approve/{d}/options", headers=world.hdr, json={}).status_code == 400
    assert len(sender.sent) == 1


def test_no_send_without_the_passkey_check(world, sender):
    d = world.draft()
    cid = world.client.post(f"/approve/{d}/options", headers=world.hdr, json={}).json()["challenge_id"]
    bad = world.client.post(f"/approve/{d}/verify", headers=world.hdr,
                            json={"challenge_id": cid, "credential": {"id": "x"}})
    assert bad.status_code == 400 and sender.sent == []


def test_a_refused_server_is_shown_as_not_sent(world, sender):
    sender.error = SmtpSendFailed("no")
    d = world.draft()
    answer = world.click(d)
    assert answer["ok"] and answer["outcome"] == "failed" and answer["title"] == "Not sent"
    assert "Nothing was sent" in answer["detail"]


def test_an_unclear_result_says_to_check_the_sent_folder(world, sender):
    sender.error = SmtpSendUnknown("cut")
    d = world.draft()
    answer = world.click(d)
    assert answer["outcome"] == "unknown" and "sent folder" in answer["detail"]
    page = world.page(d)
    assert "unclear" in page and "do not send it again" in page.lower()
    assert world.svc.draft_status("o", d)["status"] == "unknown"


def test_the_hourly_limit_leaves_the_draft_approved_and_says_so(world, sender):
    for _ in range(3):
        world.click(world.draft())
    d = world.draft()
    answer = world.click(d)
    assert answer["outcome"] == "not_sent" and answer["title"] == "Approved, but not sent"
    assert len(sender.sent) == 3
    assert "approved but was not sent" in world.page(d)


def test_the_done_box_has_a_place_for_the_result(world):
    page = world.page(world.draft())
    assert 'id="done-title"' in page and 'id="done-text"' in page


# -- the old way stays -------------------------------------------------------------------------------

def test_without_the_send_step_the_old_text_stays(config, sender, clock):
    w = World(config, sender, clock, wired=False)
    d = w.draft()
    assert "Approve with passkey" in w.page(d) and "Approve and send" not in w.page(d)
    assert "tell it that you approved" in w.page(d)
    assert w.click(d)["message"] == "Approved. Go back to Claude and tell it to send the mail."
    assert sender.sent == []
    assert w.svc.send_email("o", d)["status"] == "sent"


def test_the_switch_turns_the_page_send_off(config, sender, clock):
    w = World(config, sender, clock, sends=False)
    d = w.draft()
    assert "Approve and send" not in w.page(d)
    w.click(d)
    assert sender.sent == []


def test_the_env_switch():
    base = {"OUTBOX_APPROVAL_HOST": HOST}
    assert ApprovalConfig.from_env(base).sends is True
    for off in ("0", "false", "no", "off"):
        assert ApprovalConfig.from_env({**base, "OUTBOX_APPROVAL_SENDS": off}).sends is False
    assert ApprovalConfig.from_env({**base, "OUTBOX_APPROVAL_SENDS": "1"}).sends is True
    with pytest.raises(ConfigError):
        ApprovalConfig.from_env({**base, "OUTBOX_APPROVAL_SENDS": "maybe"})


def test_send_after_approval_for_an_unknown_draft_is_refused(world, sender):
    assert world.svc.send_after_approval("drf_nothing")["status"] == "refused"
    assert sender.sent == []


def test_send_after_approval_without_an_approval_sends_nothing(world, sender):
    d = world.draft()
    assert world.svc.send_after_approval(d)["status"] == "approval_required"
    assert sender.sent == []


# -- several versions of one mail ------------------------------------------------------------------------

def test_versions_are_numbered_and_stay_open(world, sender):
    d1, d2, d3 = (world.draft(subject=f"Fassung {i}") for i in (1, 2, 3))
    assert "Version 1 of 3" in world.page(d1)
    assert "Version 2 of 3" in world.page(d2)
    assert "Version 3 of 3" in world.page(d3)
    assert "Approve and send with passkey" in world.page(d1)        # no version is closed by a newer one


def test_a_single_draft_has_no_version_line(world):
    assert "Version" not in world.page(world.draft())


def test_once_a_version_is_sent_the_others_warn_but_can_still_be_sent(world, sender):
    d1, d2 = world.draft(subject="Erste"), world.draft(subject="Zweite")
    world.click(d2)
    page = world.page(d1)
    assert "Another version of this mail was already sent" in page
    assert "Zweite" in page and EXT in page
    assert "Send this one anyway with passkey" in page and "Approve and send" not in page
    answer = world.click(d1)                                       # a new passkey check, then it goes
    assert answer["outcome"] == "sent" and len(sender.sent) == 2


def test_the_warning_names_the_sent_version_with_a_local_time_element(world):
    d1, d2 = world.draft(subject="Erste"), world.draft(subject="Zweite")
    world.click(d2)
    assert "<time data-ts=" in world.page(d1)


def test_a_later_separate_mail_to_the_same_person_is_not_a_version(world, sender, clock):
    d1 = world.draft(subject="Käsekuchen")
    world.click(d1)
    clock.t += 60
    d2 = world.draft(subject="Einladung")
    page = world.page(d2)
    assert "Another version" not in page and "Version" not in page
    assert "Approve and send with passkey" in page


def test_mail_to_another_person_is_never_a_version(world):
    d1 = world.draft("Ferdinand")
    d2 = world.draft("Erna")
    world.click(d2)
    assert "Another version" not in world.page(d1) and "Version" not in world.page(d1)


def test_each_version_is_bound_to_its_own_text(world, sender):
    d1, d2 = world.draft(body="eins"), world.draft(body="zwei")
    world.click(d1)
    sent_text = sender.sent[0][0].get_body().get_content()
    assert "eins" in sent_text and "zwei" not in sent_text
    assert world.svc.draft_status("o", d2)["status"] == "pending"


# -- what the assistant is told and can ask -------------------------------------------------------------------

def test_the_instructions_say_the_page_sends_and_do_not_claim_nothing_was_sent(world):
    text = world.svc.prepare_email("o", "Ferdinand", "S", "T")["instructions"]
    assert "sends the mail itself" in text and "do NOT call send_email" in text
    assert "Do not write that nothing has been sent yet" in text
    assert "draft_status" in text and "do not cancel the old one" in text


def test_the_old_instructions_stay_without_the_page_send(config, sender, clock):
    w = World(config, sender, clock, sends=False)
    text = w.svc.prepare_email("o", "Ferdinand", "S", "T")["instructions"]
    assert "Do not call send_email before the user tells you they approved it" in text


def test_draft_status_follows_the_draft(world):
    d = world.draft()
    s = world.svc.draft_status("o", d)
    assert s["status"] == "pending" and s["approved"] is False and "Not sent yet" in s["note"]
    world.click(d)
    s = world.svc.draft_status("o", d)
    assert s["status"] == "sent" and "finished_at" in s and s["to_address"] == EXT
    assert s["approved"] is True


def test_draft_status_says_approved_for_a_sent_draft_only_if_a_passkey_approved_it(world):
    own = world.draft(who="Me")
    world.svc.send_email("o", own)
    s = world.svc.draft_status("o", own)
    assert s["status"] == "sent" and s["approved"] is False
    ext = world.draft()
    world.click(ext)
    assert world.svc.draft_status("o", ext)["approved"] is True


def test_draft_status_belongs_to_the_owner(world):
    from outbox_mcp.errors import DraftNotFound

    d = world.draft()
    with pytest.raises(DraftNotFound):
        world.svc.draft_status("someone-else", d)


def test_an_early_send_email_call_points_to_the_page_and_draft_status(world):
    d = world.draft()
    answer = world.svc.send_email("o", d)
    assert answer["status"] == "approval_required"
    assert "Do not call send_email again" in answer["note"] and "draft_status" in answer["note"]
