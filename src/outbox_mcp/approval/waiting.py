"""send_email that waits for the passkey approval (comfort function, off by default).

Without it the person approves on the page, goes back to the chat and tells the assistant that they
approved, and the assistant calls send_email again. With it, the first send_email call waits a few
seconds for the approval and sends by itself when it arrives.

What does not change: the approval is still checked, and the draft is still claimed, inside the
same transaction as before. This module only decides WHEN send_email is called again; it never
sends, never approves and never touches the draft itself.

Rules:
- It waits only when approval is set up and a passkey is registered (otherwise nothing could arrive).
- The wait is bounded by OUTBOX_APPROVAL_WAIT_SECONDS (at most 50). When time runs out the caller
  gets the ordinary "approval_required" answer and the old way still works.
- At most MAX_WAITERS calls wait at the same time. A manipulated chat that starts many sends
  cannot pile up waiting calls; the others get the ordinary answer at once.
- The FIRST send_email call for a draft never waits. It answers at once and tells the assistant to
  show the link to the person and then call send_email again. Only the next call waits. While a
  call waits the assistant cannot write anything, so this forces a turn in which the link gets
  written, whatever order the model would have chosen on its own.
- It polls the database about once a second in a worker thread. The event loop is never blocked.
- If the client gives up and cancels the call, the wait ends and nothing is sent after that.
"""

from __future__ import annotations

import time
from typing import Awaitable, Callable, Dict

import anyio

MAX_WAITERS = 2
MAX_ANNOUNCED = 200
POLL_SECONDS = 1.0


class ApprovalWaiter:
    def __init__(
        self,
        max_waiters: int = MAX_WAITERS,
        poll_seconds: float = POLL_SECONDS,
        sleep: Callable[[float], Awaitable[None]] = anyio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_waiters = max_waiters
        self.poll_seconds = poll_seconds
        self._sleep = sleep
        self._monotonic = monotonic
        self._waiting = 0
        self._announced: Dict[str, None] = {}

    @property
    def waiting(self) -> int:
        return self._waiting

    async def wait(self, service, run, owner: str, draft_id: str, first: Dict) -> Dict:
        """`first` is the answer of the first send_email call. Returns the final answer."""
        seconds = self._seconds(service)
        if (
            seconds <= 0
            or first.get("status") != "approval_required"
            or not first.get("approval_url")
            or self._waiting >= self.max_waiters
        ):
            return first
        if draft_id not in self._announced:
            self._announced[draft_id] = None
            while len(self._announced) > MAX_ANNOUNCED:
                self._announced.pop(next(iter(self._announced)))
            announced = dict(first)
            announced["note"] = (
                "Do not wait yet. First write your reply to the person with the recipient, subject, "
                "text and the link in 'approval_url'. Then call send_email once more for this draft: "
                f"that call waits up to {seconds} seconds and sends the mail when the approval arrives. "
                + str(first.get("note", ""))
            ).strip()
            return announced
        self._waiting += 1
        try:
            deadline = self._monotonic() + seconds
            while True:
                state = await run(service.approval_state, owner, draft_id)
                if state == "approved":
                    # The same call as always: it checks the approval again and claims the draft.
                    return await run(service.send_email, owner, draft_id)
                if state == "closed":
                    # Cancelled, expired, finished or no longer approvable: the ordinary call
                    # gives the exact answer for that case.
                    return await run(service.send_email, owner, draft_id)
                remaining = deadline - self._monotonic()
                if remaining <= 0:
                    break
                await self._sleep(min(self.poll_seconds, remaining))
        finally:
            self._waiting -= 1
        timed_out = dict(first)
        timed_out["waited_seconds"] = seconds
        timed_out["note"] = (
            f"The approval did not arrive within {seconds} seconds. " + str(first.get("note", ""))
        ).strip()
        return timed_out

    @staticmethod
    def _seconds(service) -> int:
        approval = getattr(service, "approval", None)
        return approval.config.wait_seconds if approval is not None else 0
