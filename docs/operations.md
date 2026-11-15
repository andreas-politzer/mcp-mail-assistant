# Operating the server

This is how the server was run for real, on a small Linux home server behind a Cloudflare Tunnel, with Infomaniak as the SMTP provider. Nothing here is specific to those two: any host with systemd, any tunnel or reverse proxy that serves HTTPS, and any SMTP account with STARTTLS or SSL will do. Ready-to-adapt files are in [`deploy/`](../deploy/); replace `youruser`, the paths and `relay.example.com`.

## Layout: code and data apart

- **Code:** a normal checkout, with its own virtual environment (`uv sync`).
- **Data:** one directory outside the checkout (here `~/outbox-data`, mode `700`) holding `.env`, `recipients.json`, the two SQLite databases (`mail.db` for drafts and audit log, `auth.db` for the sign-in state). Single files are mode `600`.

Keeping data apart lets the service run with `ProtectSystem=strict` and write only to the data directory. The sample unit [`deploy/outbox-mcp.service`](../deploy/outbox-mcp.service) shows the hardening used. The service listens on `127.0.0.1` only; it is reachable from outside only through the tunnel.

**Quote values in `.env`.** systemd's `EnvironmentFile` and the dotenv parser used by `uv --env-file` disagree about unquoted values that contain spaces (for example the sender's display name). Quote such values, and test the real service (not only a manual run) by sending a mail.

## Tunnel and rate limit

1. Create a **separate** tunnel for this service (`cloudflared tunnel create`), with its own config file and unit ([`deploy/cloudflared-outbox.service`](../deploy/cloudflared-outbox.service), [`deploy/cloudflared-outbox.config.example.yml`](../deploy/cloudflared-outbox.config.example.yml)). Do not touch tunnels of other services on the same host.
2. Create the DNS route **last** (`cloudflared tunnel route dns ...`). Until then the service is not reachable from the internet. Afterwards, check from outside: a request to `/mcp` without a token must answer `401`, and `/.well-known/oauth-protected-resource/mcp` must name your public address.
3. Add a rate-limiting rule for the sign-in endpoints (`/register`, `/authorize`, `/login`, `/token`). The author used 20 requests per 10 seconds per IP, block for 10 seconds, which is generous for a normal sign-in and still stops a guessing script from hammering the login page. On Cloudflare's free plan one rule is available, so one expression covers all four paths. Do not put an interactive login page (such as an access gateway) in front of the OAuth endpoints; Claude cannot pass it.

## Limits and what they feel like

Defaults: 3 sends per hour, 10 per day, 20 drafts per hour. They are a safety net: if something goes wrong (a bug, or hidden instructions in a document that push the assistant to send), the damage is capped. During testing the hourly limit is reached quickly (three test mails already use it up, the fourth is refused with a clear message, and the assistant relays it). The author raised the hourly limit for testing with `OUTBOX_MAX_SENDS_PER_HOUR` in `.env` and restarted the service. Pick values that fit your use and keep them small.

## Restarts

The transport is stateless and the sign-in state lives in SQLite. A restart ends the running connection; Claude (desktop and mobile) connected again by itself without a new sign-in, and the next send worked. Time zone: the server runs in UTC, so log timestamps are one hour behind central European time in winter.

## Backup

What must survive a disk failure: the two databases, `.env`, `recipients.json`, and the unit and tunnel files. What is deliberately not backed up: the deploy key (create a new one).

Design:

- A **second backup unit** next to any existing backup, same restic repository, its own tag (`outbox`), its own timer at minute 30 of every hour. A separate unit leaves an existing backup untouched, and the offset keeps the two from fighting over the repository lock.
- **SQLite is not copied as a file.** A live database, especially in WAL mode, can be copied in a broken state. [`deploy/snapshot.py`](../deploy/snapshot.py) uses SQLite's backup API to write a consistent snapshot into a staging directory (mode `700`), checks it with `integrity_check` and swaps it in atomically; it needs only Python, not the `sqlite3` program. Journal mode is switched to `delete` in the copy so each snapshot is a single file.
- `.env` and `recipients.json` are backed up directly from the data directory, so no second plaintext copy of the SMTP password lies around.
- The restic repository is encrypted; the repository password is a file readable only by the service user.

**Test the restore, not just the backup.** Restore the latest snapshot into an empty private directory, open every database read-only, run `pragma integrity_check`, compare table row counts with what you expect, compare `.env` and `recipients.json` with the originals using `cmp`, and delete the test directory (it holds a copy of `.env`). The author's run: both databases `ok`, drafts and audit entries present, both files identical.

## Pitfalls met on the way

- **SMTP error 535 (authentication failed).** Check that the stored password really is the device password. The provider's "copy" button did not copy the password in one case, the clipboard still held older text; selecting the text by hand and copying worked.
- **Edit and test on the right machine.** Server commands run on the server; a guard such as `[ "$(hostname)" = yourhost ] && { ... }` at the start of a pasted block prevents running them on your laptop.
- **Two registered clients after the first connection.** Claude registered itself twice, and the sign-in page showed once "request unknown or expired, please start again", although the connection was working afterwards. The cause is not established (a second call to the return page is the guess). It is harmless, and listed in [oauth-security.md](oauth-security.md).
- **Claude's own approval prompt.** The desktop app asked once whether the tool may be used and remembered "always allow". The server itself still requires a draft ID and the assistant's explicit send call.


## Managing recipients

Recipients are approved on the server, never in the chat. Over SSH on the server, in the code
folder (the admin tool needs only the database location, not the mail password):

```bash
export OUTBOX_DB_PATH=/path/to/outbox-data/mail.db
uv run outbox-admin pending                      # open suggestions from the AI
uv run outbox-admin approve ferdinand@example.org --alias Ferdi
uv run outbox-admin add "Anna" anna@example.org
uv run outbox-admin block someone@example.org    # can never be suggested again
uv run outbox-admin list
```

`approve` shows name, address and the AI's (unverified) note and asks for confirmation. Check the
address against the real contact. The first start after the upgrade to schema version 2 imports
the old `recipients.json` once; afterwards the database is the truth and the file is ignored.
If the old file is empty, nothing is imported and the registry stays empty, so the next start looks at the file again; this is harmless, and once any recipient exists in the database the file is never read again.
Back up before the first start with a new version.
