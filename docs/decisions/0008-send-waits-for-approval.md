# 0008: send_email can wait for the passkey approval

Status: laboratory state, off by default.

## Problem

For an external recipient the person approves a draft with a passkey on a page outside the chat. They then have to go back to the chat and tell the assistant that they approved, so that it calls `send_email` again. That last sentence adds nothing to the security: the passkey is the approval.

## Decision

`OUTBOX_APPROVAL_WAIT_SECONDS` (default 0 = off, at most 50). When it is set, a `send_email` call that answers "approval required" does not return at once. It checks about once a second whether this exact draft has been approved and, when the approval arrives in time, calls the existing send path again. If time runs out, the caller gets the ordinary "approval required" answer plus `waited_seconds`, and the old way (approve, tell the assistant, call again) works as before.

## What stays the same

- The approval is checked, and the draft is claimed exactly once, inside the same transaction as without waiting. The wait only decides when `send_email` runs again. It never sends, approves or changes a draft itself.
- No tool can approve. A draft that is cancelled, expired, finished or not the caller's ends the wait and gets the ordinary answer or error.
- Approval for another draft or for other content does not end the wait (the content hash is part of the check).

## Limits

- At most two calls wait at the same time. More calls get the ordinary answer at once, so a manipulated chat cannot pile up waiting calls.
- The wait runs in the background of the event loop (a worker thread checks the database), so the approval page keeps being served. A call that the client cancels stops waiting and sends nothing afterwards.
- The upper limit of 50 seconds is deliberate. Clients end a tool call that takes too long: reports for MCP clients mention about one minute; for connectors in Claude desktop a limit of four minutes has been reported. Both come from user bug reports, not from documentation, and can change. The default of 25 seconds is a starting value to be tested.

## What the assistant is told

With waiting on, the instructions of an external draft say: show recipient, subject and text, give the link in the reply, then call `send_email` once in the same turn. Claude cannot write while a tool call is waiting, so the link has to be in the chat before the call. A link that works only on this computer (local approval page) is no longer described as "open it on the phone".

## Open questions

- Does the text shown before a waiting tool call reach the person in time in Claude desktop and in the mobile apps? (to be tried)
- Is 25 seconds enough on a phone, where the person has to switch apps? (to be tried)
- Which value suits a public setup behind a tunnel, where the call travels through a proxy with its own limits? (to be tried)
