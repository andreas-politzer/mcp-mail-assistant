"""send_email that waits for the passkey approval (comfort function, off by default).

The wait only decides when send_email is called again. The approval check and the one-time claim
of the draft are the existing ones, so these tests also pin down that nothing is sent twice,
nothing is sent without approval, and the old way (tell the assistant, call again) still works."""

import threading
import time

import anyio
import pytest

from approval_helpers import Device
from outbox_mcp.approval.service import MAX_WAIT_SECONDS, ApprovalConfig, ApprovalService
from outbox_mcp.approval.waiting import ApprovalWaiter
from outbox_mcp.errors import ConfigError, DraftNotSendable
from outbox_mcp.server import LOCAL_OWNER, build_server
from outbox_mcp.service import OutboxService
from outbox_mcp.storage.database import Database
from outbox_mcp.storage.recipient_store import DbRecipientList, RecipientStore

OWN = "me@example.com"
EXT = "ferdinand@example.org"
HOST = "relay.example.org"


class World:
    def __init__(self, config, sender, clock, wait_seconds=25, local=False):
        self.clock, self.sender = clock, sender
        self.db = Database(config.db_path)
        self.store = RecipientStore(self.db)
        self.store.add("Me", OWN, ["myself"], 1.0, "admin:test", scope="own")
        self.store.add("Ferdinand", EXT, [], 1.0, "admin:test")
        if local:
            cfg = ApprovalConfig(rp_id="localhost", port=8433, wait_seconds=wait_seconds)
        else:
            cfg = ApprovalConfig(rp_id=HOST, wait_seconds=wait_seconds)
        self.approval = ApprovalService(self.db, cfg, clock)
        self.svc = OutboxService(
            config, DbRecipientList(self.store), self.db, sender, clock,
            recipient_store=self.store, approval=self.approval,
        )
        self.device = Device(rp_id=cfg.rp_id, origin=cfg.origin)

    def enroll(self):
        token = self.approval.create_enrollment()
        _, opts = self.approval.enroll_options(token)
        cid = opts["challenge_id"]
        ok, msg = self.approval.enroll_verify(token, {"challenge_id": cid, "credential": self.device.register(cid)})
        assert ok, msg

    def approve(self, draft_id):
        ok, opts = self.approval.approve_options(draft_id)
        assert ok, opts
        cid = opts["challenge_id"]
        ok, msg = self.approval.approve_verify(draft_id, {"challenge_id": cid, "credential": self.device.assert_(cid)})
        assert ok, msg

    def draft(self, who="Ferdinand", owner="o"):
        return self.svc.prepare_email(owner, who, "S", "T")["draft_id"]


@pytest.fixture
def world(config, sender, clock):
    w = World(config, sender, clock)
    w.enroll()
    return w


class FakeTime:
    """Time that only moves when the waiter sleeps; `on_sleep` can act while it 'waits'."""

    def __init__(self, on_sleep=None):
        self.t = 0.0
        self.sleeps = []
        self.on_sleep = on_sleep

    def monotonic(self):
        return self.t

    async def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.t += seconds
        if self.on_sleep:
            self.on_sleep(len(self.sleeps), self.t)
        await anyio.sleep(0)


async def inline(func, *args):
    return func(*args)


def waiter_with(fake, **kw):
    return ApprovalWaiter(sleep=fake.sleep, monotonic=fake.monotonic, **kw)


def go(world, waiter, draft_id, owner="o", announced=True):
    """One send_email call. By default the draft was already announced (the link was shown)."""
    async def main():
        if announced:
            await waiter.wait(world.svc, inline, owner, draft_id, world.svc.send_email(owner, draft_id))
            waiter._monotonic.__self__.t += 10                   # the reply was written meanwhile
        first = world.svc.send_email(owner, draft_id)
        return await waiter.wait(world.svc, inline, owner, draft_id, first)
    return anyio.run(main)


# -- configuration ------------------------------------------------------------------------

def test_waiting_is_off_by_default():
    assert ApprovalConfig(rp_id=HOST).wait_seconds == 0
    assert ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": HOST}).wait_seconds == 0
    assert ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": HOST, "OUTBOX_APPROVAL_WAIT_SECONDS": ""}).wait_seconds == 0


def test_env_reads_the_wait_time():
    c = ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": HOST, "OUTBOX_APPROVAL_WAIT_SECONDS": "25"})
    assert c.wait_seconds == 25
    c = ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": "localhost", "OUTBOX_APPROVAL_PORT": "8433",
                                 "OUTBOX_APPROVAL_WAIT_SECONDS": str(MAX_WAIT_SECONDS)})
    assert c.wait_seconds == MAX_WAIT_SECONDS


@pytest.mark.parametrize("raw", ["-1", str(MAX_WAIT_SECONDS + 1), "600", "abc", "2.5"])
def test_bad_wait_times_are_refused(raw):
    with pytest.raises(ConfigError):
        ApprovalConfig.from_env({"OUTBOX_APPROVAL_HOST": HOST, "OUTBOX_APPROVAL_WAIT_SECONDS": raw})


def test_wait_time_must_be_a_real_number_in_range():
    for bad in (-1, MAX_WAIT_SECONDS + 1, True):
        with pytest.raises(ConfigError):
            ApprovalConfig(rp_id=HOST, wait_seconds=bad)


def test_a_moved_local_page_keeps_the_wait_time():
    from dataclasses import replace

    c = ApprovalConfig(rp_id="localhost", port=8433, wait_seconds=25)
    assert replace(c, port=8434).wait_seconds == 25


# -- the wait itself ------------------------------------------------------------------------

def test_the_mail_goes_out_by_itself_when_the_approval_arrives(world, sender):
    draft = world.draft()
    fake = FakeTime(on_sleep=lambda n, t: world.approve(draft) if n == 3 else None)
    result = go(world, waiter_with(fake), draft)
    assert result["status"] == "sent" and len(sender.sent) == 1
    assert len(fake.sleeps) == 3                       # it polled, it did not wait for the whole time


def test_without_approval_it_gives_the_ordinary_answer_after_the_time(world, sender):
    draft = world.draft()
    fake = FakeTime()
    result = go(world, waiter_with(fake), draft)
    assert result["status"] == "approval_required" and result["approval_url"]
    assert result["waited_seconds"] == 25 and "did not arrive within 25 seconds" in result["note"]
    assert sum(fake.sleeps) == pytest.approx(25) and sender.sent == []


def test_the_old_way_still_works_after_a_timeout(world, sender):
    draft = world.draft()
    result = go(world, waiter_with(FakeTime()), draft)
    assert result["status"] == "approval_required"
    world.approve(draft)                                # the person approves late, tells the assistant
    assert world.svc.send_email("o", draft)["status"] == "sent" and len(sender.sent) == 1


def test_an_approval_that_was_already_there_is_used_at_once(world, sender):
    draft = world.draft()
    world.approve(draft)

    async def main():
        # the first call already sends; a waiter never needs to start
        first = world.svc.send_email("o", draft)
        return first, await waiter_with(FakeTime()).wait(world.svc, inline, "o", draft, first)

    first, result = anyio.run(main)
    assert first["status"] == "sent" and result is first and len(sender.sent) == 1


def test_approval_that_arrives_before_the_first_poll_is_found(world, sender):
    draft = world.draft()

    async def main():
        fake = FakeTime()
        waiter = waiter_with(fake)
        await waiter.wait(world.svc, inline, "o", draft, world.svc.send_email("o", draft))  # announced
        fake.t += 10
        first = world.svc.send_email("o", draft)        # approval_required
        world.approve(draft)                            # approved right after
        return await waiter.wait(world.svc, inline, "o", draft, first)

    assert anyio.run(main)["status"] == "sent" and len(sender.sent) == 1


def test_it_does_not_wait_when_off(config, sender, clock):
    w = World(config, sender, clock, wait_seconds=0)
    w.enroll()
    draft = w.draft()
    fake = FakeTime()
    result = go(w, waiter_with(fake), draft)
    assert result["status"] == "approval_required" and "waited_seconds" not in result
    assert fake.sleeps == []


def test_it_does_not_wait_without_a_passkey(config, sender, clock):
    w = World(config, sender, clock)                    # nobody enrolled
    draft = w.draft()
    fake = FakeTime()
    result = go(w, waiter_with(fake), draft)
    assert result["status"] == "approval_required" and result["approval_url"] is None
    assert fake.sleeps == []


def test_it_does_not_wait_for_an_own_address(world, sender):
    draft = world.draft("me")
    fake = FakeTime()
    result = go(world, waiter_with(fake), draft, announced=False)
    assert result["status"] == "sent" and fake.sleeps == [] and len(sender.sent) == 1


def test_a_cancelled_draft_ends_the_wait_with_the_ordinary_error(world, sender):
    draft = world.draft()
    fake = FakeTime(on_sleep=lambda n, t: world.svc.cancel_draft("o", draft) if n == 2 else None)
    with pytest.raises(DraftNotSendable):
        go(world, waiter_with(fake), draft)
    assert sender.sent == []


def test_an_expired_draft_ends_the_wait_and_nothing_is_sent(world, sender, clock):
    draft = world.draft()
    fake = FakeTime(on_sleep=lambda n, t: setattr(clock, "t", clock.t + 100000) if n == 2 else None)
    with pytest.raises(DraftNotSendable):
        go(world, waiter_with(fake), draft)
    assert sender.sent == []


def test_a_foreign_owner_never_gets_an_approved_state(world):
    draft = world.draft()
    world.approve(draft)
    assert world.svc.approval_state("o", draft) == "approved"
    assert world.svc.approval_state("someone-else", draft) == "closed"
    assert world.svc.approval_state("o", "drf_unknown") == "closed"


def test_approval_for_other_content_does_not_end_the_wait(world, sender, clock):
    draft = world.draft()
    other = world.draft()
    world.approve(other)                                # another draft is approved, not this one
    result = go(world, waiter_with(FakeTime()), draft)
    assert result["status"] == "approval_required" and sender.sent == []


# -- limits and cancellation ---------------------------------------------------------------------

def test_at_most_two_calls_wait_and_the_third_answers_at_once(world, sender):
    drafts = [world.draft() for _ in range(3)]
    release = anyio.Event()
    started = []

    async def blocking_sleep(seconds):
        started.append(seconds)
        await release.wait()

    now = [0.0]
    waiter = ApprovalWaiter(sleep=blocking_sleep, monotonic=lambda: now[0])
    results = {}

    async def one(i):
        first = world.svc.send_email("o", drafts[i])
        await waiter.wait(world.svc, inline, "o", drafts[i], first)       # announces, does not wait
        now[0] += 10                                                      # the reply was written
        results[i] = await waiter.wait(world.svc, inline, "o", drafts[i], first)

    async def main():
        async with anyio.create_task_group() as tg:
            tg.start_soon(one, 0)
            tg.start_soon(one, 1)
            await anyio.sleep(0.05)
            assert waiter.waiting == 2
            await one(2)                                # the third does not wait
            assert results[2]["status"] == "approval_required" and "waited_seconds" not in results[2]
            assert waiter.waiting == 2
            tg.cancel_scope.cancel()

    anyio.run(main)
    assert waiter.waiting == 0 and sender.sent == []


def test_a_cancelled_call_stops_waiting_and_sends_nothing(world, sender):
    draft = world.draft()
    waiter = ApprovalWaiter(sleep=anyio.sleep, monotonic=time.monotonic, poll_seconds=0.05)

    async def main():
        with anyio.move_on_after(0.2):
            first = world.svc.send_email("o", draft)
            await waiter.wait(world.svc, inline, "o", draft, first)

    anyio.run(main)
    assert waiter.waiting == 0 and sender.sent == []
    world.approve(draft)                                # a later approval is still usable the old way
    assert world.svc.send_email("o", draft)["status"] == "sent" and len(sender.sent) == 1


def test_two_waiting_calls_for_one_draft_send_exactly_one_mail(world, sender):
    draft = world.draft()
    fake = FakeTime(on_sleep=lambda n, t: world.approve(draft) if n == 1 else None)
    waiter = waiter_with(fake)
    outcomes = []

    async def announce():
        await waiter.wait(world.svc, inline, "o", draft, world.svc.send_email("o", draft))
        fake.t += 10

    async def one():
        try:
            first = world.svc.send_email("o", draft)
            outcomes.append(await waiter.wait(world.svc, inline, "o", draft, first))
        except DraftNotSendable as exc:
            outcomes.append(exc)

    async def main():
        await announce()
        async with anyio.create_task_group() as tg:
            tg.start_soon(one)
            tg.start_soon(one)

    anyio.run(main)
    assert len(sender.sent) == 1
    assert sorted(type(o).__name__ for o in outcomes if not isinstance(o, dict)) in ([], ["DraftNotSendable"])
    assert sum(1 for o in outcomes if isinstance(o, dict) and o.get("status") == "sent") == 1


# -- the text the assistant sees --------------------------------------------------------------------

def test_the_instructions_name_the_wait_and_the_order(world):
    d = world.svc.prepare_email("o", "Ferdinand", "S", "T")
    text = d["instructions"]
    assert "up to 25 seconds" in text and "in the same turn" in text
    assert "on their phone or computer" in text
    assert "approval_url" in text


def test_the_instructions_put_the_link_before_the_call(world):
    text = world.svc.prepare_email("o", "Ferdinand", "S", "T")["instructions"]
    assert text.index("First write your reply") < text.index("call send_email once")
    assert "Never call it before the link is written" in text


def test_a_local_link_is_not_sent_to_the_phone(config, sender, clock):
    w = World(config, sender, clock, local=True)
    w.enroll()
    text = w.svc.prepare_email("o", "Ferdinand", "S", "T")["instructions"]
    assert "in the browser on this computer" in text and "phone" not in text


def test_without_waiting_the_old_instructions_stay(config, sender, clock):
    w = World(config, sender, clock, wait_seconds=0)
    w.enroll()
    text = w.svc.prepare_email("o", "Ferdinand", "S", "T")["instructions"]
    assert "Do not call send_email before the user tells you they approved it" in text
    assert "seconds" not in text and "same turn" not in text


def test_a_local_link_is_not_sent_to_the_phone_even_without_waiting(config, sender, clock):
    w = World(config, sender, clock, wait_seconds=0, local=True)
    w.enroll()
    text = w.svc.prepare_email("o", "Ferdinand", "S", "T")["instructions"]
    assert "in the browser on this computer" in text and "phone" not in text


# -- through the real tool ----------------------------------------------------------------------------

def test_the_send_tool_waits_and_sends_when_the_approval_comes(world, sender, monkeypatch):
    draft = world.draft(owner=LOCAL_OWNER)
    monkeypatch.setattr("outbox_mcp.approval.waiting.MIN_GAP_SECONDS", 0.05)
    server = build_server(world.svc)
    timer = threading.Timer(0.4, lambda: world.approve(draft))
    timer.start()

    async def main():
        first = await server.call_tool("send_email", {"draft_id": draft})
        await anyio.sleep(0.1)
        started = time.monotonic()
        result = await server.call_tool("send_email", {"draft_id": draft})
        return first, result, time.monotonic() - started

    try:
        first, result, took = anyio.run(main)
    finally:
        timer.cancel()
    assert "approval_required" in str(first) and "write your reply" in str(first)
    assert "sent" in str(result) and len(sender.sent) == 1
    assert took < 5


def test_the_first_call_for_a_draft_never_waits(world, sender):
    draft = world.draft()
    fake = FakeTime()
    result = go(world, waiter_with(fake), draft, announced=False)
    assert fake.sleeps == [] and sender.sent == []
    assert result["status"] == "approval_required" and "waited_seconds" not in result
    assert "write your reply" in result["note"] and result["approval_url"]


def test_only_the_second_call_waits(world, sender):
    draft = world.draft()
    fake = FakeTime(on_sleep=lambda n, t: world.approve(draft) if n == 2 else None)
    waiter = waiter_with(fake)
    go(world, waiter, draft, announced=False)
    fake.t += 10                                        # the reply with the link was written
    result = go(world, waiter, draft, announced=False)
    assert result["status"] == "sent" and len(fake.sleeps) == 2


def test_a_call_straight_after_the_first_one_does_not_wait_either(world, sender):
    draft = world.draft()
    fake = FakeTime()
    waiter = waiter_with(fake)
    go(world, waiter, draft, announced=False)
    fake.t += 1                                         # no reply written: only one second passed
    again = go(world, waiter, draft, announced=False)
    assert fake.sleeps == [] and again["status"] == "approval_required"
    assert again["note"].startswith("Too early")
    fake.t += 1                                         # the clock started over at the early call
    assert go(world, waiter, draft, announced=False)["note"].startswith("Too early")
    fake.t += 10
    assert "waited_seconds" in go(world, waiter, draft, announced=False)


def test_each_draft_is_announced_once(world):
    d1, d2 = world.draft(), world.draft()
    fake = FakeTime()
    waiter = waiter_with(fake)
    assert go(world, waiter, d1, announced=False)["note"].startswith("Do not wait yet")
    assert go(world, waiter, d2, announced=False)["note"].startswith("Do not wait yet")
    fake.t += 10
    assert "waited_seconds" in go(world, waiter, d1, announced=False)


def test_the_send_tool_returns_the_ordinary_answer_when_waiting_is_off(config, sender, clock):
    w = World(config, sender, clock, wait_seconds=0)
    w.enroll()
    draft = w.draft(owner=LOCAL_OWNER)
    server = build_server(w.svc)

    async def main():
        started = time.monotonic()
        result = await server.call_tool("send_email", {"draft_id": draft})
        return result, time.monotonic() - started

    result, took = anyio.run(main)
    assert "approval_required" in str(result) and took < 2 and sender.sent == []


# -- what the approval page says ----------------------------------------------------------------------

def _page(world):
    from starlette.applications import Starlette
    from starlette.testclient import TestClient

    from outbox_mcp.approval.web import AttemptLimiter, build_approval_routes

    app = Starlette(routes=build_approval_routes(world.approval, AttemptLimiter(world.clock)))
    return TestClient(app, base_url=world.approval.config.origin)


def test_the_page_does_not_ask_to_tell_the_assistant_when_sending_waits(world):
    draft = world.draft()
    page = _page(world).get(f"/approve/{draft}").text
    assert "sends the mail now if it is still waiting" in page
    assert "tell it that you approved" in page                    # only for the late case
    assert "You can go back to Claude now and tell it that you approved" not in page


def test_the_page_keeps_the_old_text_when_sending_does_not_wait(config, sender, clock):
    w = World(config, sender, clock, wait_seconds=0)
    w.enroll()
    draft = w.draft()
    page = _page(w).get(f"/approve/{draft}").text
    assert "You can go back to Claude now and tell it that you approved" in page
    assert "sends the mail now if it is still waiting" not in page


def test_an_approved_draft_page_names_both_ways(world, config):
    draft = world.draft()
    world.approve(draft)
    page = _page(world).get(f"/approve/{draft}").text
    assert "If Claude is still waiting, it sends the mail by itself" in page


def _approve_over_http(w):
    draft = w.draft()
    client = _page(w)
    hdr = {"Origin": w.approval.config.origin, "Content-Type": "application/json"}
    cid = client.post(f"/approve/{draft}/options", headers=hdr, json={}).json()["challenge_id"]
    return client.post(f"/approve/{draft}/verify", headers=hdr,
                       json={"challenge_id": cid, "credential": w.device.assert_(cid)}).json()


def test_the_approval_answer_names_the_waiting_mail(world):
    answer = _approve_over_http(world)
    assert answer["ok"] and "If Claude is still waiting, it sends the mail now" in answer["message"]


def test_the_approval_answer_keeps_the_old_text_without_waiting(config, sender, clock):
    w = World(config, sender, clock, wait_seconds=0)
    w.enroll()
    answer = _approve_over_http(w)
    assert "Go back to Claude and tell it to send the mail" in answer["message"]


def test_a_timeout_tells_the_assistant_not_to_repeat_the_mail_text(world):
    draft = world.draft()
    result = go(world, waiter_with(FakeTime()), draft)
    assert result["status"] == "approval_required" and result["waited_seconds"] == 25
    assert "Do not repeat the mail text" in result["note"]
