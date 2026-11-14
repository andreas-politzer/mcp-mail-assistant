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

Over HTTP the same rule holds: one process per database, so the update procedure is stop, update, start. The stateless transport means a restart ends the running connection and the client reconnects.

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
  Version 1 (`security/attachments.py`): Markdown only, at most 3 files, 200 KB each and 500 KB in total;
  only inside allowed folders, blocked folders win; folder names compared case- and Unicode-insensitively;
  hidden files, symlinks, `..`, absolute paths, sensitive names (password, secret, token, .env, .pem ...)
  and files that contain secret patterns (a warning layer, not proof) are refused. One refused file refuses
  the whole draft. The file is opened once, checked on the open handle and stored as an immutable copy with
  the draft; the audit log records name, size and SHA-256, never the content.

## Kill switch

Stop the service, revoke the SMTP device password at the mail provider, remove the connector from the assistant.


## Recipient registry (stage 2)

Recipients live in the database (`recipients`, schema version 2), not in a file. The recipients
file is imported once on the first start with an empty registry and ignored afterwards.

- The AI side has two tools: `suggest_recipient` (puts a name and address on a waiting list) and
  `list_suggestions` (status of its own suggestions). No tool can approve, reject, block or add.
- The admin side is `outbox-admin` (command line, no mail credentials needed). It is the only
  place where a suggestion becomes an active recipient. A later admin API and app use the same
  `RecipientStore`, on a listener that is not reachable through the public tunnel.
- States: suggested, active, rejected, removed, expired (30 days without a decision), blocked
  (the address can never be suggested again). History is kept; every change is in the audit log.
- Limits: 5 open suggestions, 10 per day. Claude's note is stored, marked "not verified" in the
  admin output, and never decides anything. Names and aliases of active recipients stay unique.
- A recipient that is blocked or removed stops pending drafts too: the allowed-check runs again
  when a draft is sent.
- Not verifiable by the server: that an address came from the user and not from a document. The
  safeguard is the user's check of name and address at approval time.
