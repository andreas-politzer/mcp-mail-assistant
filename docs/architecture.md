# Architecture

## Goal

Let an AI assistant send an e-mail on behalf of its user, by voice or text, without opening a mail app, while keeping the user in control.

## Two-step sending

`prepare_email` creates an immutable draft. `send_email(draft_id)` makes a single send attempt for exactly that draft and is never retried automatically. The assistant is instructed to show the draft and send only after the user confirmed it in their own words.

## Draft states

```
pending -> sending -> sent | failed | unknown
pending -> expired | cancelled
```

- `failed`: the SMTP server provably did not accept the message.
- `unknown`: the result is unclear (e.g. connection dropped after the message body was transmitted). No automatic retry.
- `sent`: the SMTP server accepted the message. This does not guarantee delivery.

Database triggers reject changes to draft content, invalid status transitions, and updates or deletes in the audit log. This protects against programming errors and application-level changes. It does **not** protect against someone who can write to the database file (they can drop the triggers or delete the file), so the file is created with mode `0600`.

The audit log keeps recipient address and subject in plain text and never the message text; its SHA-256 hash covers recipient address, subject and text.

If the mail server accepts a message but the final status cannot be stored locally, the result is `sent_but_not_recorded`: the draft stays in `sending` (not sendable again) and becomes `unknown` at the next startup. There is no automatic retry.

## Single instance

Run exactly one server process per database for now. The startup recovery step turns drafts stuck in `sending` into `unknown`; a second process would do that to the first one's in-flight send.

## Trust boundaries

| Enforced by the server | Not enforceable by the server |
| --- | --- |
| order of calls, immutability, single use, expiry | that a human really confirmed |
| recipient allow-list, fixed sender, no CC/BCC | that the assistant was not manipulated |
| limits, audit log | theft of the SMTP password itself |

## Confirmation: open question

1. **A. Confirmation in the conversation only** (current). Simple, but vulnerable to prompt injection.
2. **B. Approval prompt in the client for `send_email`.** Depends on the client; unverified for custom connectors, mobile and voice.
3. **C. Out-of-band approval** (separate approval page or push). Strongest, most effort.

Plan: test the simplest variant with self-only sending first, decide before allowing other recipients.

## Stages

- Block 0: core, tests, stdio (this repository state).
- Stage 1: HTTP transport with authentication, real sending to oneself only.
- Stage 2: fixed list of other recipients.
- Stage 3: attachments, read by the server from an allowed folder by path (with an excluded-folders list).

## Kill switch

Stop the service, revoke the SMTP device password at the mail provider, remove the connector from the assistant.
