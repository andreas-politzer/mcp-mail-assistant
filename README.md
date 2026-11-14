# mcp-mail-assistant

> **Your AI assistant drafts. You decide what gets sent.**

A confirmation-first [MCP](https://modelcontextprotocol.io) server that enables AI assistants to draft and send emails through any SMTP provider.

The assistant **prepares** an e-mail as an immutable draft. It is sent only in a **separate, explicit second step**, to a fixed list of recipients, over your own SMTP account.

Typical use: you are on a walk, talking to the assistant, and say "send this to my nephew". The **intended flow** is that the assistant creates a draft, reads you the recipient, subject and text, and sends only after you have clearly said yes. This is how the server is meant to be used, not something it can enforce: it cannot check that a human really said yes (see [Honest limits](#honest-limits)).

## Status

Work in progress, built in stages. Be clear about what is and is not done:

| Part | State |
| --- | --- |
| Core: drafts, single send attempt per draft, recipient allow-list, limits, audit log | Built and covered by automated tests. A real test mail to the author's own address was sent through Infomaniak SMTP (STARTTLS, port 587). |
| OAuth sign-in (authorization server and token check, [security model](docs/oauth-security.md)) | Implemented and covered by automated tests, including parallel access. The login redirect was checked once in a real Chromium browser. |
| Remote access (HTTP transport with the sign-in wired to the mail tools) | Implemented and covered by automated tests with a fake sender: no token, no access; drafts belong to the token's owner; a slow mail server does not block the sign-in page. **Not yet run on the real host and not yet reached from the internet.** Locally, stdio still works as before. |
| Connection to a real assistant app (Claude custom connector, mobile, voice) | **Not done yet, so not verified.** `docs/oauth-security.md` lists exactly which points still need a real connector. |
| Other recipients than yourself, attachments | Planned (see [docs/architecture.md](docs/architecture.md)). |

Tested with: Infomaniak (STARTTLS). Other providers that offer standard SMTP with STARTTLS or SSL should work, but have not been tested.

## How it works

1. `list_recipients` shows who may receive mail (read from `recipients.json`).
2. `prepare_email(recipient, subject, text)` stores an **immutable draft** and returns a random draft ID. Nothing is sent.
3. `send_email(draft_id)` makes a single send attempt for exactly that draft. It is never retried automatically.
4. `cancel_draft(draft_id)` discards a pending draft.

What the server enforces by itself:

- A draft cannot be changed after it was prepared, and each draft gets only a single send attempt. (The application never changes drafts, and database triggers additionally reject changes and invalid status transitions. This guards against bugs, not against someone with write access to the database file, who could remove the triggers.)
- Drafts expire (default 10 minutes).
- Recipients must be on the allow-list; no free-form addresses, no CC/BCC, fixed sender.
- Subject and text are checked against header injection and control characters.
- Limits for preparing and sending (defaults: 3 sends/hour, 10/day).
- An append-only audit log (database triggers reject updates and deletes). It stores recipient address and subject **in plain text**, but not the message text; instead it keeps a SHA-256 hash that covers recipient address, subject and text. The log is never purged. As with drafts, these triggers protect against application-level changes, not against someone with direct access to the database file, who could remove them.
- If the result of sending is unclear (connection dropped after the message was handed over), the draft is marked `unknown` and is **never retried automatically**.
- STARTTLS never silently falls back to plain text.

## Honest limits

**The server cannot prove that a human said "yes".** If the assistant is tricked (for example by a malicious web page or document containing instructions), it could call both tools. The allow-list, the limits and the audit log reduce the damage, but they do not remove this risk. Stronger options (an approval prompt in the client, or an approval step outside the chat) are discussed in [docs/architecture.md](docs/architecture.md) and are not built yet.

The sign-in layer is described, with the tests that prove each claim and its known weaknesses, in [docs/oauth-security.md](docs/oauth-security.md).

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

## Setup (remote, HTTP with sign-in)

```bash
# Create the password hash for the sign-in page (asks for the passphrase, prints the hash):
uv run python -m outbox_mcp.auth.passwords

# In .env set OUTBOX_PUBLIC_BASE_URL, OUTBOX_OAUTH_PASSWORD_HASH and OUTBOX_OAUTH_DB_PATH
# (see .env.example), then:
uv run --env-file .env outbox-mcp-http
```

The service listens on `127.0.0.1:8430` only (override with `OUTBOX_HTTP_PORT`). Publish it through a tunnel or reverse proxy that serves HTTPS under `OUTBOX_PUBLIC_BASE_URL`.

- **Stateless transport.** Every request stands alone; the sign-in state lives in SQLite. A restart or update ends the running connection, and the client has to connect again. Sign-ins and tokens survive the restart.
- **Mail work runs in worker threads** (at most four at once), so a slow SMTP server does not block the sign-in page or other requests.
- **Owner.** Drafts belong to the signed-in owner (the subject of the access token), not to a fixed name.

## Known limitations

Documented on purpose, to be decided later:

- No schema versioning for the SQLite database yet (needed before attachments).
- Tool annotations (`readOnlyHint`, `destructiveHint`, ...) are set honestly, but they are only hints for the client, not a security measure. Whether a client asks for an extra approval because of them has not been checked with a real client.
- Dependencies: `mcp[cli]` pulls in extras the server does not need; the lower bound `mcp>=1.9.0` is untested (tests run against the locked version).
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
  server.py          MCP tools (thin shell, stdio)
  http_server.py     HTTP entry point: sign-in plus the mail tools
  service.py         prepare / send / cancel logic
  config.py          settings from environment
  security/          validation, recipient allow-list, limits
  storage/           SQLite drafts + audit log
  mail/              message building, SMTP client
  auth/              OAuth authorization server and token check (not mail-related)
scripts/               manual SMTP check for your real account
tests/
docs/architecture.md     design decisions, stages, open questions
docs/oauth-security.md  sign-in security model, limits, tests, known gaps
```

## Who built this

Design, safety rules and decisions by Andreas Politzer. The code, tests and documentation were written together with Claude (Anthropic), step by step, with every design decision and every commit reviewed by the author. That is also why the documents say plainly what has and has not been verified.

Licensed under the [MIT License](LICENSE).
