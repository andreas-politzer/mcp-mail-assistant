# 0009: The approval page sends the mail

Status: accepted (lab), not yet deployed on the server.

## Problem

With the waiting send (0008) the mail only leaves by itself if the person approves within the wait,
at most 50 seconds. Reading a longer text takes longer than that, and the person has to tell the
assistant afterwards. People read at their own speed, take a call in between, and come back later.
A stopwatch is the wrong model.

## Decision

The approval page sends the mail itself, right after the passkey check.

- The person reads the draft on the page, approves with the passkey whenever it suits them, and the
  server sends at that moment. The assistant does not have to do anything afterwards.
- Drafts are valid for 24 hours (`OUTBOX_DRAFT_TTL_SECONDS=86400`, was 600). A link that is open
  does nothing without the passkey, and an approval covers exactly one draft with exactly its text.
- The send is the same step as before (`send_email`): it checks the approval again and claims the
  draft inside one transaction. The page only calls it after a successful passkey check, for the
  owner of the draft. Nothing is sent twice, nothing is sent without approval.
- The page shows the result: sent (the mail server accepted it), not sent (it refused), unclear (the
  connection broke off; no automatic second try, check the sent folder), or approved but not sent
  (for example the hourly limit). Opening the link again later shows the same result.
- `OUTBOX_APPROVAL_SENDS=0` switches this off and brings back the old way (the person tells the
  assistant, the assistant calls `send_email`). The waiting send (0008) only works in that mode; with
  the page sending there is nothing to wait for.

## Several versions of one mail

All versions stay open. The first one that is sent does not close the others, but they warn: the
page names the version that was already sent (subject, time) and the button says "Send this one
anyway". That needs a new passkey check, so a second mail cannot leave by accident, and the person
keeps the freedom to choose another version.

Versions are found by the server, not by the assistant: drafts to the same address and the same owner
that were alive at the same time. A later, separate mail to the same person is not a version, because
the earlier mail was already finished when the later draft was made. The page shows "Version 2 of 3".

## What the assistant is told

- Show the draft and the link; do not call `send_email`; do not write that nothing has been sent yet
  (that sentence would be wrong after the click).
- When the person talks about the mail again, call `draft_status` first. It reports where the draft
  stands (not sent, approved, sent, failed, unclear, expired, discarded). The server cannot write into
  the chat by itself, so the status is fetched when it is needed.
- For changes, prepare a new draft and do not cancel the old one.

## Not changed

Approval is bound to the exact text (fingerprint). The claim of the draft is atomic. No retries after
an unclear result. The audit log records approval and sending as before.
