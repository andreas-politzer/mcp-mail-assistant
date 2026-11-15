# 0002 The assistant suggests, only the user decides

Status: built.

## Context

The assistant reads untrusted material (web pages, documents, other people's messages). Any text there may try to steer it. Whatever the assistant can do on its own, a successful manipulation can do too.

## Decision

- The assistant has six tools: list recipients, prepare a draft, send a confirmed draft, cancel a draft, suggest a recipient, list its own suggestions. A test asserts that exactly this set exists.
- **No tool can approve, reject, block, add or remove a recipient, and no tool can change attachment rules.** A suggestion is only a waiting-list entry and grants no sending right. The user approves outside the chat (today with `outbox-admin`).
- Sending always needs a separate, explicit confirmation of the exact draft. **There is no "auto-send" switch and there will not be one in the admin interface.** If this is ever wanted, it needs its own written decision.

## Consequences

- The worst a manipulated assistant can do is make a suggestion the user will see and can refuse.
- Setting up a new recipient takes one extra step by the user. That is the price of the guarantee.
- Honest limit: the server cannot check that a human really confirmed. It can only demand a separate confirming call (see README, "Honest limits").
