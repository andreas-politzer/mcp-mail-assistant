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
| order of calls, immutability, single use, expiry | that a human really confirmed (planned: approval outside the chat for other recipients) |
| recipient allow-list, fixed sender, no CC/BCC | that the assistant was not manipulated |
| limits, audit log | theft of the SMTP password itself |

## Confirmation: current state and planned stage

**Implemented today.** The final confirmation of a send is given in the conversation (variant A). The server enforces what it can: recipient allow-list (checked again inside the claim transaction), attachment rules, send limits, a single atomic claim per draft, exactly one SMTP attempt, and the states `sent`, `failed` and `unknown`. It cannot prove that the confirmation came from a human.

**Not enforceable by a client prompt.** An approval prompt in the client (variant B) depends on the client and its settings. It is not a boundary of this server.

**Planned next stage (not built, no code yet): approval outside the chat (variant C).** Decision, as stated publicly: the server is not opened to other users or to recipients who are not the owner's own mailboxes until this stage is built and acceptable in daily use. Until then the approved list contains the owner's own addresses only. If the prototype is annoying in use, it is not adopted as it is, and other recipients wait. See [decision 0006](decisions/0006-approval-outside-the-chat.md).

- The owner's own addresses keep the current flow unchanged.
- For other recipients, the assistant can prepare a draft but `send_email` alone does not send it. The approval is bound to exactly that draft (recipient, subject, text, attachments, expiry), can be used once, and is given through a channel the assistant has no access to.
- The server needs a way to tell the owner's own mailboxes from other recipients. How this is represented is **not decided**. Requirements: only the admin side can set it; every new address starts as "not own"; changing it to "own" is a loosening that needs the existing admin confirmation; the assistant may neither propose nor decide it; a domain name is not a usable rule. A later migration keeps the existing own addresses as own and must neither silently turn them into "other" nor turn any other address into "own".
- The approval check sits **in front of** the existing claim. Claim, limits, the single SMTP attempt and the states stay as they are. SMTP is not moved into a transaction.
- The recipient allow-list stays in force. It stops arbitrary addresses from being prepared at all; the approval stops different content from going to an approved address than the one the owner saw.

**Open until tested:** how the approval feels in use (target: seconds, no copying between apps), which technology carries it (for example a passkey on a separate HTTPS origin), and how phone and desktop are covered. A throw-away prototype comes first, outside this repository and without SMTP or the real database.

**What the approval shows.** The server can verify that a valid authentication took place for exactly this approval request, which is bound to this draft. It does not show that the owner read or understood the content, so the approval screen must display recipient, subject and attachments clearly. Binding and display belong together.

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
  The origin path is stored with the copy (schema version 3) and checked against the folder rules again right
  before sending, so withdrawing a folder also stops drafts that are already prepared.

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
