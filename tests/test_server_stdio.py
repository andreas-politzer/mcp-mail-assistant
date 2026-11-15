"""The local (stdio) server shell: same tools, fixed owner "local", no network involved."""

from __future__ import annotations

import asyncio
import json
import threading
import time

import pytest

from outbox_mcp.server import LOCAL_OWNER, build_server
from outbox_mcp.service import OutboxService


@pytest.fixture
def service(config, recipients, db, sender):
    return OutboxService(config, recipients, db, sender)


def call(mcp, name, arguments):
    return json.loads(asyncio.run(mcp.call_tool(name, arguments))[0].text)


def test_tools_and_annotations(service):
    tools = {t.name: t for t in asyncio.run(build_server(service).list_tools())}
    assert set(tools) == {"list_recipients", "prepare_email", "send_email", "cancel_draft",
                          "suggest_recipient", "list_suggestions", "list_attachments"}
    assert tools["list_recipients"].annotations.readOnlyHint is True
    assert tools["send_email"].annotations.destructiveHint is True


def test_prepare_and_send_use_the_local_owner(service, sender, db):
    mcp = build_server(service)
    draft = call(mcp, "prepare_email", {"recipient": "Ferdinand", "subject": "Hi", "text": "Text"})
    assert db.get_draft(draft["draft_id"], LOCAL_OWNER).id == draft["draft_id"]
    assert sender.sent == []
    assert call(mcp, "send_email", {"draft_id": draft["draft_id"]})["status"] == "sent"
    assert len(sender.sent) == 1


def test_cancel_draft(service, sender):
    mcp = build_server(service)
    draft = call(mcp, "prepare_email", {"recipient": "Ferdinand", "subject": "Hi", "text": "Text"})
    assert call(mcp, "cancel_draft", {"draft_id": draft["draft_id"]})["status"] == "cancelled"
    with pytest.raises(Exception):
        call(mcp, "send_email", {"draft_id": draft["draft_id"]})
    assert sender.sent == []


class SlowSender:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.sent = []

    def send(self, message, envelope_from, envelope_to):
        self.started.set()
        assert self.release.wait(10), "test never released the sender"
        self.sent.append(envelope_to)


def test_client_giving_up_does_not_leave_a_send_half_recorded(config, recipients, db):
    """If the caller is cancelled while the mail is in flight, the send still ends cleanly."""
    slow = SlowSender()
    mcp = build_server(OutboxService(config, recipients, db, slow))

    async def scenario():
        prep = await mcp.call_tool("prepare_email", {"recipient": "Ferdinand", "subject": "s", "text": "t"})
        draft_id = json.loads(prep[0].text)["draft_id"]
        task = asyncio.create_task(mcp.call_tool("send_email", {"draft_id": draft_id}))
        assert await asyncio.to_thread(slow.started.wait, 5)
        task.cancel()          # the client disconnects
        slow.release.set()     # the mail server answers afterwards
        await asyncio.gather(task, return_exceptions=True)
        return draft_id

    draft_id = asyncio.run(scenario())
    # The worker thread finishes on its own; wait for its final status.
    for _ in range(100):
        if db.get_draft(draft_id, LOCAL_OWNER).status != "sending":
            break
        time.sleep(0.05)
    assert slow.sent == ["ferdinand@example.org"]
    assert db.get_draft(draft_id, LOCAL_OWNER).status == "sent"


def test_prepare_email_tool_accepts_attachments(service):
    tools = {t.name: t for t in asyncio.run(build_server(service).list_tools())}
    props = tools["prepare_email"].inputSchema["properties"]
    assert "attachments" in props
    assert "attachments" not in tools["prepare_email"].inputSchema.get("required", [])
