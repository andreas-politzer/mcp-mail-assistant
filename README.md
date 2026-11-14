# outbox-mcp

A small [MCP](https://modelcontextprotocol.io) server that lets an AI assistant **prepare** e-mails and send them only in a **separate, explicit second step**, to a fixed list of recipients, over your own SMTP account.

Typical use: you are on a walk, talking to the assistant, and say "send this to my nephew". The assistant creates a draft, reads you the recipient, subject and text, and sends only after you have clearly said yes.

> **Status: Block 0.** Core logic, tests, stdio transport. No HTTP transport or authentication yet, so it cannot be attached to a remote assistant app yet. Nothing here has been run against a real mail account.

## How it works

1. `list_recipients` shows who may receive mail (read from `recipients.json`).
2. `prepare_email(recipient, subject, text)` stores an **immutable draft** and returns a random draft ID. Nothing is sent.
3. `send_email(draft_id)` sends exactly that draft, **once**.
4. `cancel_draft(draft_id)` discards a pending draft.

What the server enforces by itself:

- A draft cannot be changed after it was prepared, and can be sent only once. (The application never changes drafts, and database triggers additionally reject changes and invalid status transitions. This guards against bugs, not against someone with write access to the database file, who could remove the triggers.)
- Drafts expire (default 10 minutes).
- Recipients must be on the allow-list; no free-form addresses, no CC/BCC, fixed sender.
- Subject and text are checked against header injection and control characters.
- Limits for preparing and sending (defaults: 3 sends/hour, 10/day).
- An append-only audit log (database triggers reject updates and deletes). It stores recipient address and subject **in plain text**, but not the message text; instead it keeps a SHA-256 hash that covers recipient address, subject and text. The log is never purged.
- If the result of sending is unclear (connection dropped after the message was handed over), the draft is marked `unknown` and is **never retried automatically**.
- STARTTLS never silently falls back to plain text.

## Honest limits

**The server cannot prove that a human said "yes".** If the assistant is tricked (for example by a malicious web page or document containing instructions), it could call both tools. The allow-list, the limits and the audit log reduce the damage, but they do not remove this risk. Stronger options (an approval prompt in the client, or an approval step outside the chat) are discussed in [docs/architecture.md](docs/architecture.md) and are not built yet.

A stolen SMTP password bypasses this server completely. Use a per-device password and revoke it if in doubt.

## Operating rules

- **Run only one server instance** (one process, one database file) for now. Starting a second instance would mark the first one's in-flight send as `unknown` at startup, and the single-use guarantee has only been designed and tested for one process.
- **File permissions.** Keep secrets readable by your user only:

  ```bash
  chmod 600 .env recipients.json
  ```

  The server creates its database (and the `-wal`/`-shm` files SQLite adds) with mode `0600`, and tightens an existing database file to `0600` at startup. Use absolute paths for `OUTBOX_DB_PATH` and `OUTBOX_RECIPIENTS_FILE` in a service setup, because relative paths depend on the working directory.
- If a send ends as `unknown` or `sent_but_not_recorded`, check your mailbox's sent folder before trying again. The server never retries by itself.

## Setup (local, stdio)

```bash
uv sync
cp .env.example .env              # fill in your SMTP settings
cp recipients.example.json recipients.json
uv run --env-file .env outbox-mcp
```

Credentials belong only in `.env` (never committed; it is in `.gitignore`).

## Known limitations (not addressed in Block 0)

Documented on purpose, to be decided later:

- No schema versioning for the SQLite database yet (needed before attachments).
- No MCP tool annotations (`readOnlyHint`, `destructiveHint`, ...) yet.
- Dependencies: `mcp[cli]` pulls in extras the server does not need; the lower bound `mcp>=1.9.0` is untested (tests run against the locked version).
- Whether tools run blocking inside the event loop has not been checked; relevant for the HTTP transport.
- Expiry uses the system clock; a backwards clock jump would extend a draft's lifetime.
- If building the message fails before anything is sent, the draft is reported as `unknown` rather than `failed` (safe, but imprecise).
- Not yet tested: real TLS with certificate verification, SSL mode (port 465), failed login.

## Testing against your real mail account

Before attaching anything to an assistant, check that your SMTP account works. The script
`scripts/test_infomaniak_smtp.py` was written for Infomaniak but works with any provider that
offers STARTTLS on port 587. It uses the real flow (`prepare_email`, then `send_email`) and:

- sends **exactly one** test mail, **only to your own sender address** (there is no option to choose another recipient);
- shows sender, recipient, subject and text first, and sends only if you type `SEND` in capital letters; anything else cancels. It needs an interactive terminal;
- requires `OUTBOX_SMTP_SECURITY=starttls` and port 587, makes one attempt, never retries and never falls back to unencrypted connections;
- reads settings only from the environment (`.env`) and never prints passwords. It uses a temporary database that is deleted afterwards.

```bash
# 1. Look at the mail without connecting anywhere (works with the placeholder values):
uv run --env-file .env.example python scripts/test_infomaniak_smtp.py --dry-run

# 2. The real test, with your own .env:
uv run --env-file .env python scripts/test_infomaniak_smtp.py
```

"Sent" means the mail server accepted the message. Then check your inbox and spam folder.
The screen before the confirmation shows your address; do not paste it into public places.

## Tests

```bash
uv run pytest
```

The tests use a fake sender and a local test SMTP server; no real mail is sent.

## Repository layout

```
src/outbox_mcp/
  server.py          MCP tools (thin shell)
  service.py         prepare / send / cancel logic
  config.py          settings from environment
  security/          validation, recipient allow-list, limits
  storage/           SQLite drafts + audit log
  mail/              message building, SMTP client
scripts/               manual SMTP check for your real account
tests/
docs/architecture.md
```

## Who built this

Design and safety rules by Andreas Politzer; implementation built together with Claude (Anthropic). Licensed under MIT.
