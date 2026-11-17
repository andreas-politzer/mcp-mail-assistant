# mcp-mail-assistant

> **Your AI assistant drafts. You decide what gets sent.**

A confirmation-first [MCP](https://modelcontextprotocol.io) server that enables AI assistants to draft and send emails through any SMTP provider.

The assistant **prepares** an e-mail as an immutable draft. It is sent only in a **separate, explicit second step**, to a fixed list of recipients, over your own SMTP account.

Typical use: you are on a walk, talking to the assistant, and say "send this to my nephew". The **intended flow** is that the assistant creates a draft, reads you the recipient, subject and text, and sends only after you have clearly said yes. This is how the server is meant to be used, not something it can enforce: it cannot check that a human really said yes (see [Honest limits](#honest-limits)).

## Status

Working end to end for the author, built in stages. Be clear about what is and is not done:

| Part | State |
| --- | --- |
| Core: drafts, single send attempt per draft, recipient allow-list, limits, audit log | Built and covered by automated tests. A real test mail to the author's own address was sent through Infomaniak SMTP (STARTTLS, port 587). |
| OAuth sign-in (authorization server and token check, [security model](docs/oauth-security.md)) | Implemented and covered by automated tests, including parallel access. The login redirect was checked once in a real Chromium browser. |
| Remote access (HTTP transport with the sign-in wired to the mail tools) | Runs on the author's own server behind a Cloudflare Tunnel and is reachable from the internet; without a token every request is refused (checked from outside: HTTP 401). Covered by automated tests with a fake sender: drafts belong to the token's owner, a slow mail server does not block the sign-in page. See [docs/operations.md](docs/operations.md). |
| Connection to a real assistant app (Claude custom connector) | **Used for real** on Claude desktop, on mobile in a text chat and in a live voice chat: draft, read-out, spoken confirmation, send, test mails arrived with correct umlauts. Reconnecting after a restart worked without a new sign-in. What was observed and what is still open: [docs/oauth-security.md](docs/oauth-security.md#verified-with-a-real-claude-connector-14-november-2026). |
| Backup and restore | Hourly encrypted backup of the data directory (databases via SQLite snapshots, configuration). The first automatic run of the timer was verified, and a restore was tested once (databases open and pass `integrity_check`, config files identical). See [docs/operations.md](docs/operations.md#backup). |
| Markdown attachments from an allowed notes folder | **Used for real**: a note was attached and sent from a voice chat, and later a recipe note from another allowed folder arrived intact (see [docs/architecture.md](docs/architecture.md)). |
| New recipients: the AI suggests, you approve outside the chat (admin command line) | **Used for real**: a suggestion made in the chat was approved on the server, then a mail with an attachment went to the new address. Reviewed by a second model; two small findings fixed. |
| Admin API | Implemented and covered by automated tests: recipients (approve, reject, block, remove, CSV import with preview), file sources and rules, send limits, settings, activity log. It listens locally only and has its own sign-in. Running on the author's server in its latest version since 16 November 2026 (code update with a backup, a restore test and a rollback mark first). Reachable only through an SSH tunnel. |
| Desktop app (Tauri 2, React, Rust) | Implemented: sign-in, recipients, files and attachments, settings, activity, help. Frontend tests run against the real admin API; Rust tests cover the local file access. Run in development mode on macOS by the author. Release builds: see [Desktop app](#desktop-app). |

Tested with: Infomaniak (STARTTLS). Other providers that offer standard SMTP with STARTTLS or SSL should work, but have not been tested.

## What is what

**Mail Assistant is the product in this repository, and it works on its own.** For the basic setup you need Python with [uv](https://docs.astral.sh/uv/), an SMTP account and an AI assistant that can use MCP servers. No domain, no Cloudflare account, no tunnel and no note vault are needed. Follow [Setup (local, stdio)](#setup-local-stdio) and you are done.

Two optional additions make the whole setup more useful. They are separate from Mail Assistant and from each other, and Mail Assistant does not depend on either:

| | What it is | What you gain with it | In this repository? |
| --- | --- | --- | --- |
| **Mail Assistant** | MCP service for drafts, recipients, attachments and sending, plus the desktop app to manage it | The product itself | Yes |
| **Vault Assistant** | Separate MCP service for controlled access to your notes in an Obsidian vault | The assistant can read and write your notes, and you can have it attach a note to a mail in the same conversation | No. The author runs the third-party [obsidian-web-mcp](#credits-and-third-party-projects) for this |
| **Remote access** | An HTTPS address with a sign-in and a tunnel that only makes outgoing connections | You can use the assistant from your phone, also by voice, away from home | The sign-in layer for Mail Assistant is (`src/outbox_mcp/auth/`). The tunnel is not. A worked Cloudflare example is in [docs/operations.md](docs/operations.md) |

```
 Claude (desktop, phone, voice)
        |   local, or over remote access (HTTPS + sign-in + tunnel)
        +--------------------+------------------------+
                             |                        |
                     Mail Assistant            Vault Assistant
                    (this repository)           (optional, separate)
                             |
                     your SMTP account
```

Mail Assistant has its own small, allow-listed notes folder for attachments. That folder can be a part of your vault, and the author does it that way, but nothing requires it.

The everyday experience is a natural conversation with Claude: you say who gets which message, Claude drafts, you confirm. The desktop app is only for setting things up and keeping them in order: who may receive mail, which files may be attached, how much may be sent. It is an independent desktop with a playful retro look, nothing more.

## How it works

1. `list_recipients` shows who may receive mail (the active entries of the recipient registry in the database; `recipients.json` is imported once on the first start). `suggest_recipient` puts a new contact on a waiting list and `list_suggestions` shows its status. Only the user can approve a suggestion, with the admin command line `outbox-admin` outside the chat; no tool can.
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

**The server cannot prove that a human said "yes".** The final confirmation of a send happens in the conversation with the assistant. If the assistant is tricked (for example by a malicious web page or document containing instructions), it could call both `prepare_email` and `send_email` itself, and the server cannot tell the difference.

What a tricked assistant can and cannot do today:

- It **can** send mail to any approved recipient, with attachments the rules already allow, within the send limits.
- It **cannot** add recipients, unblock addresses, open folders or raise limits. Those changes exist only in the admin API, which listens on the loopback interface only and has its own passphrase.

A mail to one of the owner's own mailboxes can also be forwarded or affect other people, so the planned distinction between own and other recipients ranks the risk; it is not a hard boundary. The allow-list, the limits and the audit log reduce the damage, but they do not remove this risk. "Nothing is sent without your explicit yes" describes the **intended flow**, not a property the server enforces.

**Approval outside the chat (built, not yet in daily use).** Every recipient is either `own` or `external`. For an `external` recipient, `send_email` does nothing until the owner has approved exactly that draft with a passkey (WebAuthn, user verification required) on a separate page of the public process. The approval covers recipient, subject, full text and attachments as shown on the approval page, works once and ends with the draft (ten minutes by default). The assistant gets the link but cannot approve. Own addresses keep the flow above ("yes" in the chat). Without `OUTBOX_APPROVAL_HOST` or without a registered passkey, nothing can be sent to external recipients. See [decision 0006](docs/decisions/0006-approval-outside-the-chat.md).

What this does **not** prove: that the owner read the text. It proves that someone who could unlock the registered passkey (Face ID or device code, or the cloud keychain it syncs through) approved this exact draft. Whoever controls that device or keychain can approve. Until the stage has been tested on a real server and used in daily use, the approved list should still be limited to the owner's own addresses.

Setup: set `OUTBOX_APPROVAL_HOST` to the public host name of the server (only the name, for example `relay.example.org`; a passkey works on that host only), then run `outbox-admin passkey-enroll` on the server and open the printed one-time link on the device that should hold the passkey (at most two passkeys). `outbox-admin passkey-list` and `passkey-remove` manage them. Mark your own addresses with `outbox-admin scope <address> own`; new recipients start as `external`. **Local desktop use without a domain** is being developed as a laboratory state: the approval page is then served on `http://localhost` by the stdio server itself and works for the person at that computer only (not from a phone). See [decision 0007](docs/decisions/0007-local-approval-page.md).

The admin service binds to `127.0.0.1` in code. Whoever runs it must not create a public route to it (see [decision 0003](docs/decisions/0003-admin-access-never-through-the-public-tunnel.md)). This repository cannot check the tunnel configuration of a given installation.

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
cp recipients.example.json recipients.json   # imported once on the first start; later use outbox-admin
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

The service listens on `127.0.0.1:8430` only (override with `OUTBOX_HTTP_PORT`). Publish it through a tunnel or reverse proxy that serves HTTPS under `OUTBOX_PUBLIC_BASE_URL`. A complete example (hardened service, tunnel, rate limit, backup with restore test) is in [docs/operations.md](docs/operations.md), with templates in [`deploy/`](deploy/).

- **Stateless transport.** Every request stands alone; the sign-in state lives in SQLite. A restart or update ends the running connection, and the client has to connect again. Sign-ins and tokens survive the restart.
- **Mail work runs in worker threads** (at most four at once), so a slow SMTP server does not block the sign-in page or other requests.
- **Owner.** Drafts belong to the signed-in owner (the subject of the access token), not to a fixed name.

## Desktop app

The app in `frontend/` manages the Mail Assistant: recipients (including CSV import and approval of candidates), files and attachments, send limits, activity and a built-in help. It talks to the admin API and never touches the mail credentials.

**Connect.** The admin API listens on `127.0.0.1` of the server and has no route through the public tunnel. Reach it from your computer through an SSH tunnel and keep that terminal open:

```bash
/usr/bin/ssh -N -L 8431:127.0.0.1:8431 BENUTZER@SERVER
```

Replace BENUTZER and SERVER with your own login and server. To make the app copy your real command, put it in `frontend/.env.local` as `VITE_TUNNEL_COMMAND=...`. That file is ignored by Git and its content is never shown in a window or in the help. The admin API needs `OUTBOX_ADMIN_ALLOWED_ORIGINS` set to the origin of the app: `tauri://localhost` on macOS and Linux, `http://tauri.localhost` on Windows. See `deploy/admin.env.example`.

**Run from source.** You need Node.js 22 or newer and a current stable Rust toolchain (plus the [Tauri system packages](https://tauri.app/start/prerequisites/) for your system).

```bash
cd frontend
npm install
npm run tauri dev      # development window against your real server (needs the SSH tunnel)
npm run dev:test       # development window against a throw-away test server, no tunnel needed
npm run tauri build    # installable package for the system you are on
```

`npm run dev:test` starts a real admin API with a temporary database on port 18432, waits until it answers, opens the app pointed at it and stops the test server when you close the app. It prints the test passphrase. It never touches port 8431, so it can run while the tunnel to your real server is open.

**Operating systems.** What has really been done, and what has not:

| System | Package | State |
| --- | --- | --- |
| macOS | `.dmg`, `.app` | Run in development mode by the author, Rust and frontend tests passed there. The release bundle was built, and its start screen was opened on 16 November 2026. Sign-in and the rest of the app inside the built package have not been checked yet. |
| Windows | `.msi`, `.exe` | Build prepared in the workflow, **not tested**. Differences known so far: hidden files are recognised by a leading dot only, and the private permissions of the settings folder rely on the default protection of the user profile. |
| Linux | `.deb`, `.AppImage` | Build prepared in the workflow, **not tested**. |

Nothing is called supported until it has been built and used on that system. The workflow `.github/workflows/desktop-build.yml` runs the tests and builds all three packages when you start it by hand in the Actions tab.

**What the app keeps on your computer.** Where a folder lies on your computer is stored in the app's own folder with private permissions and is never sent to the server, the audit log or the help. The server receives file names relative to a source, contents, sizes and hashes of the files you allow. Files are only copied when you start a synchronisation, after a preview. A synchronisation that finds a missing or replaced folder stops and deletes nothing.

## Security model in short

- Claude can only prepare drafts and suggest recipients. Approving a recipient, allowing a file and changing a limit are possible only in the admin API, behind its own passphrase, never through the assistant's connection.
- A block always wins over an allowance. Names that look like secrets and hidden files are never transferred. Only `.md` and `.txt` files can be attached.
- The admin session ends after 20 minutes without input and after 4 hours at the latest. These are the server's defaults. Shutting down the app signs you out first.
- The activity log shows actions, not message texts, subject lines or private paths.
- Details and open points: [docs/oauth-security.md](docs/oauth-security.md), [decision 0003](docs/decisions/0003-admin-access-never-through-the-public-tunnel.md) and [decision 0004](docs/decisions/0004-permission-model.md).

## Known limitations

Documented on purpose, to be decided later:

- The SQLite schema is versioned (`PRAGMA user_version`); opening an older database upgrades it in place. Back up before the first start with a new version.
- Tool annotations (`readOnlyHint`, `destructiveHint`, ...) are set honestly, but they are only hints for the client, not a security measure. The permission prompt a client shows before a tool call belongs to that client, not to this server. Its behavior depends on the client and its settings, so it is not relied on as a boundary. Other clients have not been checked.
- Dependencies: `mcp[cli]` pulls in extras the server does not need; the lower bound `mcp>=1.9.0` is untested (tests run against the locked version).
- Expiry uses the system clock; a backwards clock jump would extend a draft's lifetime.
- If building the message fails before anything is sent, the draft is reported as `unknown` rather than `failed` (safe, but imprecise).
- Not yet tested: real TLS with certificate verification, SSL mode (port 465), failed login.
- The desktop app is not reachable without an SSH tunnel on purpose: the admin API is kept off the public internet. This is a real hurdle for people who are not used to a terminal, and it is not solved. The app can copy the tunnel command for you, and the in-app help explains it, nothing more.
- The desktop app is verified on macOS only (see [Desktop app](#desktop-app)). Windows and Linux builds are prepared but untested.
- The desktop app has no automatic update and no code signing yet, so macOS and Windows will show warnings for downloaded packages.
- Contact import reads CSV files with a name and an e-mail column, up to 2000 valid rows per file.

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
  admin/             admin API (`outbox-admin-api`): its own sign-in, recipients, sources, settings, activity
  server.py          MCP tools (thin shell, stdio)
  admin_cli.py       `outbox-admin`: approve, add, block recipients (outside the chat)
  http_server.py     HTTP entry point: sign-in plus the mail tools
  service.py         prepare / send / cancel logic
  config.py          settings from environment
  security/          validation, attachment policy, recipient list, limits
  storage/           SQLite drafts, attachments, audit log, recipient registry
  mail/              message building, SMTP client
  auth/              OAuth authorization server and token check (not mail-related)
scripts/               manual SMTP check for your real account
deploy/                example systemd units, tunnel config and the SQLite snapshot script
tests/
docs/architecture.md     design decisions, stages, open questions
docs/oauth-security.md  sign-in security model, limits, tests, known gaps
docs/operations.md      running it for real: layout, tunnel, limits, backup and restore test
docs/decisions/         short records of decisions (who may decide what, permission model, look)
```

`frontend/` is the desktop app (see [decision 0001](docs/decisions/0001-monorepo-with-separate-frontend.md) and [frontend/README.md](frontend/README.md)). `.github/workflows/` holds the manual build for three systems.

## Credits and third-party projects

- **obsidian-web-mcp** is the remote access server for Obsidian vaults that the author runs next to the Mail Assistant. It is a separate project by the GitHub user **jimprosser**, published at <https://github.com/jimprosser/obsidian-web-mcp> under the **MIT License** (file `LICENSE` in that repository). It is neither copied into this repository nor required by it. If you want it, take it from its original source and read its license and documentation there. The Vault Assistant mentioned above is a different thing from this project.
- **Cloudflare Tunnel (`cloudflared`)** and **restic** appear in the operations notes as tools the author uses. They are separate products with their own licenses and are not part of this repository.
- The desktop app builds on [Tauri](https://tauri.app) (MIT or Apache-2.0) and [React](https://react.dev) (MIT); the full list of dependencies and their licenses is in `frontend/package.json`, `frontend/src-tauri/Cargo.toml` and `uv.lock`.

## Who built this

Design, safety rules and decisions by Andreas Politzer. The code, tests and documentation were written together with Claude (Anthropic), step by step, with every design decision and every commit reviewed by the author. That is also why the documents say plainly what has and has not been verified.

Licensed under the [MIT License](LICENSE).
