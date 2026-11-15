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

What must survive a disk failure: the two databases, `.env`, `admin.env` (the hash of the admin passphrase, if the admin API runs), `recipients.json`, and the unit and tunnel files (including `outbox-admin-api.service`). What is deliberately not backed up: the deploy key (create a new one).

Design:

- A **second backup unit** next to any existing backup, same restic repository, its own tag (`outbox`), its own timer at minute 30 of every hour. A separate unit leaves an existing backup untouched, and the offset keeps the two from fighting over the repository lock.
- **SQLite is not copied as a file.** A live database, especially in WAL mode, can be copied in a broken state. [`deploy/snapshot.py`](../deploy/snapshot.py) uses SQLite's backup API to write a consistent snapshot into a staging directory (mode `700`), checks it with `integrity_check` and swaps it in atomically; it needs only Python, not the `sqlite3` program. Journal mode is switched to `delete` in the copy so each snapshot is a single file.
- `.env`, `admin.env` and `recipients.json` are backed up directly from the data directory, so no second plaintext copy of the SMTP password lies around. `admin.env` holds only the hash of the admin passphrase, not the passphrase. After adding a file to the unit, run the backup once and check the snapshot: `restic ls -l latest --tag outbox` shows the file with its mode (`-rw-------`), and `restic dump latest <path> --tag outbox | cmp - <path>` compares content with the original without printing it.
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
Back up before the first start with a new version. After an update that adds or removes tools, disconnect and reconnect the connector in the assistant app; otherwise the app keeps showing the old tool list.

## Admin API (separate process)

The admin API is its own small service (`outbox-admin-api`, example unit in `deploy/outbox-admin-api.service`). It listens on 127.0.0.1 only and has **no route through the public tunnel**; the tunnel knows the mail service's port and nothing else.

- It shares the database file with the mail service and nothing more. Its settings (`deploy/admin.env.example`) hold the database path and the hash of the admin passphrase. No mail credentials, no OAuth data.
- Create the hash with `python -m outbox_mcp.admin.passphrase` (typed hidden, never printed). The passphrase belongs in a password manager.
- Sign-in gives a random session token that is sent as a `Bearer` header, never as a cookie. Sessions live in memory: 20 minutes without use, 4 hours at most, and a restart signs everybody out. After 5 wrong passphrases every sign-in is refused for 15 minutes, with the same answer as for a wrong passphrase.
- The `Host` header must be on a list and a browser `Origin` must be on an exact list (`OUTBOX_ADMIN_ALLOWED_ORIGINS`, no wildcard). `X-Forwarded-Host` is not trusted. CORS is not authentication; the token is.
- **The admin session protects the admin endpoints and nothing else.** Sending mail through Claude needs no admin session, an expired session does not touch the mail service or the tunnel, and the recipient and attachment rules are checked by the mail service itself on every send.
- **Folders and rules:** `GET /admin/folders?path=` shows one folder level below `OUTBOX_ADMIN_ATTACHMENT_ROOT` (folders and `.md` files; hidden entries, symbolic links and other file types are skipped) with the state of each entry, the rule it comes from, and whether that is inherited. `PUT /admin/folders/rule` sets `allow`, `deny` or `clear` for one path.
- **Tightening is immediate, loosening asks twice.** Blocking needs no confirmation. Allowing a folder, clearing a block, and unblocking an address first return a `confirmation_id` (single use, 2 minutes, bound to the session, the action, the target and the current rule state) and, for folders, a preview of how many notes would become attachable. Only a second request with that id changes anything. This guards against slips and repeated requests; it is not a second sign-in.
- `GET /admin/audit` returns the audit log, newest first, without subjects, bodies or hashes.
- **Send limits** (`GET`/`PUT /admin/limits`): the limits per hour and per day live in the database and apply to the very next send, no restart. Hard ceilings of 100 per hour and 1000 per day are part of the code; nothing stored, configured or requested can exceed them, and anything above is cut down when used. `0` stops all sending. Lowering is immediate; raising (either value) needs the confirmation step. Until a value is stored, the mail service's own settings file (`OUTBOX_MAX_SENDS_PER_HOUR`/`_DAY`) provides the starting value. The mail service writes the starting values it runs with into the database when it starts, and the admin side shows exactly those as the effective values (`source: mail_service_settings`), never a value from its own file; the admin settings file has no limit settings at all. Before the mail service has ever started and while nothing is stored, the values are `null` (`source: unknown`) and any increase needs confirmation. After the first change through the API, `source` is `stored`. The prepare limit stays in the settings file.
- **How sends are counted:** a send counts from the moment it is claimed, whatever becomes of it: sent, failed, unknown or interrupted by a crash. A failed send therefore uses up a slot, which stops a failing loop from flooding the mailbox. Refused attempts, expired drafts and cancelled drafts do not count. Counting and the decision happen inside the same write transaction as the claim, so parallel sends cannot get past the limit.
- Contact import: see "Admin functions" below.

### Installing the admin API on the server

Nothing here touches the mail service or the tunnel. Run on the server, in the code folder:

1. **Hash:** `python -m outbox_mcp.admin.passphrase` asks twice for a new passphrase (typed hidden, at least 12 characters, a different one from the connector and mailbox passwords) and prints one line `OUTBOX_ADMIN_PASSPHRASE_HASH=...`. The passphrase itself is never an argument, never printed, never stored; keep it in a password manager. If you ever pasted it into a command line by mistake, remove that line from the shell history (`history -d <number> && history -w`).
2. **Settings file** `admin.env` next to `.env`, mode `600`: `OUTBOX_DB_PATH` (the same database as the mail service), `OUTBOX_ADMIN_ATTACHMENT_ROOT` (the same folder as `OUTBOX_ATTACHMENT_ROOT`), `OUTBOX_ADMIN_PORT` (default 8431) and the hash line. No mail credentials. Browser origins stay empty (no origin allowed) until an app needs one.
3. **Unit:** copy `deploy/outbox-admin-api.service`, replace the user and paths, `systemctl daemon-reload`, `systemctl enable --now outbox-admin-api`. It needs the database at schema 5, which the mail service creates at its first start with this version.
4. **Check:** `ss -ltn` must show port 8431 on `127.0.0.1` only; `curl http://127.0.0.1:8431/admin/status` answers 401, and 400 with a wrong `Host` header. Then sign in with the passphrase (typed hidden, for example from a short script that posts it as JSON) and read `/admin/status`, `/admin/security` and `/admin/sources`; sign out; the old token must answer 401.
5. **Back up** `admin.env` and the unit file (see Backup below).
6. **Reach it from another machine only through an SSH tunnel:** `ssh -N -L 8431:127.0.0.1:8431 user@server`, then use `http://localhost:8431`. There is no public route. Some terminals replace `ssh` with a wrapper that ignores options such as `-O exit`; call `/usr/bin/ssh` if a control socket does not behave.

## File sources (schema 5)

Schema version 5 adds `file_sources` and `stored_files` and a `source_id` on rules and on the origin of a draft attachment. Opening an older database upgrades it without changing existing rows; the first start of the mail service registers the configured notes folder as the first source (`ensure_server_folder_source`) and takes over its existing rules.

* Two kinds of source: `server_folder` (a folder on this machine, Markdown only) and `upload` (copies stored in the database, `.md` and `.txt`). A source is a folder or a single file (single file: upload only).
* Rules belong to one source. A block always wins inside its source. Rules of different sources never affect each other: an upload source has no absolute path, so sources cannot overlap on disk; server folders may not nest.
* Removing a source deletes its stored copies; its rules stay as history and are never effective again. Registering the same thing again creates a new source without rules.
* Blocking a path (or clearing the allow above it) deletes the affected stored copies in the same transaction as the rule and the audit entry. Allowing again does not bring a copy back; it must be uploaded again.
* Limits (checked inside the storing transaction): 20 active sources, 500 files per source, 200 KB per file, 50 MB in total (current copies only).
* Attachments: `prepare_email` takes `source:relative/path` (a path without a known source name is a file of the notes folder). The answer names, per file, name, size, source, path, version (12 hex characters) and, for uploads, the upload date. At send time every copy is checked again inside the claim transaction: source active, rules allow the path and, for an upload, the stored copy still has the same hash. A draft whose attachment was replaced must be prepared again.
* A block, or removing a source, cancels open drafts that hold a file of it (audit `draft_cancelled_by_block`) and deletes the attachment copies of those drafts and of failed, expired or cancelled ones. Copies of sent (and unknown) drafts stay until the 7-day retention.
* `list_attachments` (read-only MCP tool): allowed current files of active sources, paginated (50 at most), no contents. Each entry has `attach_as` to pass to `prepare_email`. `path_prefix` narrows the list: the folders before the last part must match exactly, the last part may be just the beginning of a name (case ignored), and a prefix never shows anything that is not allowed. An unknown `source` names the existing sources in the error; an empty answer carries a `hint` that names the sources and top-level folders that do hold allowed files (never blocked ones), so the assistant does not have to guess folder names.

### Admin API for sources

All endpoints need the session; loosening changes (allowing, clearing a block) use the confirmation step.

| Endpoint | Purpose |
| --- | --- |
| `GET /admin/sources` | sources with file count, bytes, epoch, and the quota block |
| `POST /admin/sources` | create an upload source (`label`, `scope` folder or file, `origin_display`, `file_name` for a single file) |
| `POST /admin/sources/{id}/rename` | change the name only |
| `POST /admin/sources/{id}/remove` | remove at once; returns `purged_files`, `cancelled_drafts`, `deleted_copies` |
| `POST /admin/sources/{id}/evaluate` | state, rule, inherited, own rule, sensitive for up to 1000 paths |
| `PUT /admin/sources/{id}/rule` | allow / deny / clear a path; a block is immediate and reports its consequences |
| `GET /admin/sources/{id}/folders` | folder tree (the server's notes folder only) |
| `GET /admin/sources/{id}/files` | stored files with state and version, paginated (100) |
| `POST /admin/sources/{id}/sync/plan` | manifest (path, sha256, size) in, plan and confirmation out; nothing is stored |
| `POST /admin/sources/{id}/sync/apply` | files of one pass in; one transaction, one audit entry `source_synced` |

The plan stays on the server with the confirmation, so the apply step cannot add files, change a hash or remove more than planned. A pass takes at most 40 files and 8 MB; larger plans run in several passes with the same confirmation, removals happen in the last pass. Integrity, permission, quota and state violations undo the whole pass; a file whose content is unacceptable (too large, not text, secret found) is skipped and reported. Any change to the source between plan and apply (a rule, a removal, another upload) invalidates the confirmation. Failure and repeat semantics: a successful pass keeps the confirmation valid for the next pass (the answer carries the new `confirmation_id`). A failed pass voids the rest of the plan; passes already stored stay, and the next plan lists them as unchanged, so nothing is uploaded twice, and removals stay deferred. If an answer is lost (network drop after the server finished), the app repeats the identical request (same `confirmation_id`, same files): within 15 minutes, for the same session and source, it gets the remembered answer with `replayed: true`, and nothing is applied or audited a second time. A request that differs in any file gets 409. A duplicate that arrives while the first is still running gets 409 and can be repeated shortly after. If in doubt, plan again: the plan shows the real outcome. The apply route accepts bodies up to 16 MB, all other routes 16 KB. HTTP mapping: unknown or removed source 404, quota or conflict 409, everything else 400.

## Admin functions: settings, security information, activity, contact import

* `GET/PUT /admin/settings`: only `candidate_expiry_days` (1 to 365, default 30). The mail service uses the stored value, otherwise its own starting value (`OUTBOX_SUGGESTION_TTL_DAYS`), which it reports so the admin side can show what really applies. A longer window needs the confirmation step, a shorter one applies at once and tells how many open candidates are now due to expire. Send limits stay under `/admin/limits`.
* `GET /admin/security`: read-only explanation (sign-in and session times, allowed hosts and origins, request size limits, what Claude can and cannot do, hard send ceilings, file types, protections and quotas, schema version). It contains no secrets, tokens, hashes or keys.
* `GET /admin/audit` takes `type` (one or more, comma separated or repeated), `since` (seconds since 1970 or an ISO 8601 date, without a zone it means UTC) and `before`; all combine. Types: `recipients`, `files` (rules and stored copies), `limits`, `sources` (created, renamed, removed, synced), `sends` (draft life cycle, rate limits, `draft_cancelled_by_block`, `draft_copies_deleted`), `settings`, `access` (sign-ins). An unknown type gives 400.
* Contact import, `POST /admin/recipients/import/plan` with `{"rows": [{name, address, note?}]}` (at most 2000 rows; reading the CSV file and mapping its columns is the app's job): the plan classifies every row as new, known, candidate, excluded (turned down, removed or blocked before: never a candidate again), invalid or duplicate, and writes nothing. `POST /admin/recipients/import/apply` with the `confirmation_id` adds the next 500 new addresses as candidates (status "suggested", never active), all or nothing per pass. Every pass writes one audit entry `recipient_csv_imported` with the same `import=<id>`, the pass number and, in the last pass, the total. Several passes use the same repeat rules as the file sync: a successful pass keeps the rest of the plan, a failed pass voids it (plan again; addresses already added show as candidates), an identical repeat within 15 minutes gets the remembered answer with `replayed: true`. Rows are checked again inside the transaction, so an address that was blocked, approved or turned down in between is skipped. Open candidates count towards the limit of open suggestions Claude can have, so a big import must be decided on before Claude can suggest again.
