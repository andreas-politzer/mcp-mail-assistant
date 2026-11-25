# OAuth security model

This document says what the sign-in protects, how, and what it does **not** protect. Each protection in the table names the test that proves it. Claims that cannot be tested without a real Claude connector are listed separately and marked as unverified.

## Goal

Only the owner can connect an AI assistant to the mail server, and a connection can be cut off at any time. A stranger who finds the public address must not be able to read, send or even prepare mail.

## How it works

The server contains two logically separate parts that run in one process:

- an **authorization server** (login page, `/authorize`, `/token`, `/register`, `/revoke`, discovery documents), in `src/outbox_mcp/auth/`;
- the **resource server**, the MCP endpoint `/mcp`, which accepts only bearer tokens issued for exactly that address.

Sign-in is the standard OAuth 2.1 authorization code flow with PKCE (S256) for public clients. The owner proves identity once on the login page with a password. Access tokens live 1 hour, refresh tokens 90 days of inactivity and are rotated on every use.

## What is protected, and how it is tested

| Protection | Where | Proven by |
| --- | --- | --- |
| A failed or missing login never creates an authorization code | `web.py` `login_post` | `tests/auth/test_login_security.py` |
| Redirect addresses are compared as exact strings (no wildcards); default allowlist is only the claude.ai callback | `redirects.py`, checked at registration, authorize and login | `tests/auth/test_redirects.py` |
| PKCE S256 is mandatory; wrong or missing verifier is refused; a code works once, a replay revokes the tokens it created; codes expire after 120 s | SDK handlers + `store.py` `redeem_code` | `tests/auth/test_pkce.py` |
| Refresh tokens are single use; reusing an old one revokes the whole token family | `store.py` `rotate_refresh` | `tests/auth/test_tokens.py` |
| Tokens are bound to this server's own address; foreign addresses, missing address and foreign scopes are refused | `provider.py`, `verifier.py`, SDK middleware | `tests/auth/test_resource_binding.py` |
| Revocation kills the whole family; a different client cannot revoke someone else's tokens | `web.py` `revoke` | `tests/auth/test_revocation.py` |
| Codes and tokens are stored only as SHA-256 hashes; the database file has mode 0600 | `store.py` | `tests/auth/test_store_and_independence.py`, `test_pkce.py`, `test_tokens.py` |
| Only public clients (`token_endpoint_auth_method=none`); no client secret is ever stored | `provider.py` `register_client` | `tests/auth/test_registration.py` |
| Password attempts are limited globally to 5 per 15 minutes, **atomically**: the attempt is claimed before the password is checked, so parallel requests cannot exceed the limit; a successful login resets finished failures but never releases checks that are still running | `store.py` `reserve_login_attempt` | `tests/auth/test_concurrency.py` |
| At most 2 expensive password checks (scrypt) run at the same time | `web.py` `check_password` | `tests/auth/test_concurrency.py` |
| Codes and refresh tokens have exactly one winner under parallel use | `store.py` (`BEGIN IMMEDIATE` transactions) | `tests/auth/test_concurrency.py` |
| Login form: the page may submit only to itself and, for the redirect back, to the allowed return origin (`form-action`), CSRF token, `HttpOnly` + `SameSite=Strict` + `Secure` cookie, no framing, no caching, request body capped at 8 KiB, user-supplied names are escaped | `web.py` | `tests/auth/test_login_security.py` |
| The login page tells the user to continue only if they just connected Claude themselves | `web.py` | `tests/auth/test_login_security.py` |
| The authentication code does not import any mail code, and the token check asks the local store directly (no network call) | `verifier.py` | `tests/auth/test_store_and_independence.py` |

## Limits, and why these numbers

| Setting | Default | Reasoning |
| --- | --- | --- |
| Clients that never finished a sign-in (`OUTBOX_OAUTH_MAX_UNUSED_CLIENTS`) | 20 | The abuse guard. A real owner needs 1 to 3 at a time. |
| Registered clients in total (`OUTBOX_OAUTH_MAX_CLIENTS`) | 200 | Every connect or reconnect in claude.ai registers a new client, and one with a usable refresh token is kept for up to 90 days. The first version had a single limit of 20 and locked the owner out after a few weeks of testing. |
| Unused client lifetime | 1 hour | A complete connection takes minutes; the login request lives 10 minutes. A client is kept only while it has a refresh token that can still be used; clients whose tokens expired or were revoked are removed. |
| Open login requests | 20 | One owner starts at most a few at a time. |
| Password attempts | 5 per 15 minutes, global | A strong password cannot be guessed at this rate. |
| Parallel password checks | 2 | scrypt uses about 16 MiB each; this keeps a small server responsive. |
| Login form size | 8 KiB | A password and three short fields need far less. |

## Known and accepted trade-offs

These are deliberate. They are listed so nobody has to discover them.

- **The login lockout is global.** Anyone who can reach the server can use up the 5 attempts and block *new* logins for 15 minutes, again and again. Existing connections keep working. Put a rate limit in front (see below) if this matters to you.
- **Open client registration (denial of service for first connections).** Claude registers itself, so `/register` must be reachable by anyone. A stranger can fill the slots for clients that never finished a sign-in (20 by default) within seconds, and can simply do it again whenever unused clients expire (1 hour after registration). While those slots are full, a *new* first connection fails. Clients that did finish a sign-in have their own, larger limit and are not affected by this. The same applies to the 20 open login requests (they expire after 10 minutes). Existing connections are not affected. The code cannot fully prevent this; rate limiting in front of the server (see below) is the real remedy.
- **Refresh rotation is strict.** If the same refresh token arrives twice (a retry after a lost response, or two parallel requests), the whole token family is revoked and the owner signs in again. This is the safest choice. It is covered by a test that documents the behaviour (`test_documented_strict_behaviour_parallel_refresh_ends_the_session`). Whether a short grace window is needed will be decided after watching how Claude actually refreshes.
- **Revoking an already expired access token does not revoke its refresh token.** Revoke the refresh token, or use `revoke-all`.
- **No automatic cleanup of expired codes, tokens and token families yet.** They are small and harmless but accumulate. Planned for a maintenance step.
- **A blocked address gets an honest refusal, not a pretended acceptance.** When the AI suggests an address that was blocked, it is told the address cannot be suggested. Pretending the suggestion arrived would tell nobody anything: the person would hear "suggested", nothing would appear in the approval list, and the confusion would hide a real decision. The cost is that the AI can learn that one particular address does not work. The message does not say "blocked", and the AI gains no additional sending capability: it still cannot send to that address, and it cannot approve anything. We chose honest feedback over secrecy.
- **The password is the single factor.** Choose a long passphrase. The server stores only a scrypt hash.

## Connection to the mail tools

`outbox-mcp-http` serves the sign-in and the mail tools (including the recipient suggestion tools) in one process (stateless transport, loopback only).

- **Owner.** Drafts are stored under the subject of the verified access token. A token with another subject cannot see, send or cancel them. No authenticated subject means the call fails (tested).
- **Restart.** The transport keeps no sessions, and sign-in state lives in SQLite. A restart ends the running connection; the client has to connect again. Existing tokens stay valid (tested with a second app instance on the same database). Mails still in flight at the restart are marked `unknown` at startup.
- **Slow mail server.** Blocking work runs in at most four worker threads; the sign-in page stays responsive while a send is in flight (tested, and the test fails if the work runs on the event loop).
- **Annotations.** The tools carry honest hints (`list_recipients` read-only; `send_email` marked destructive and open-world). They are hints for the client, not a security measure. Whether a client asks for an extra approval because of them is not yet verified.

## Not covered by this module

- Theft of the SMTP password or of the server itself.
- Whether a human really confirmed a mail (see `architecture.md`, "Trust boundaries").
- Tunnel and firewall configuration of your host.

## Recommended operation

- Put the server behind a reverse proxy or tunnel that offers rate limiting, and rate-limit `/register`, `/authorize` and `/login`. Do not put an interactive login (for example an access-gateway page) in front of the OAuth endpoints, because Claude cannot pass it.
- Run `python -m outbox_mcp.auth.admin revoke-all --db <path>` to cut off every connection at once.
- Use a long, unique passphrase and create its hash with `python -m outbox_mcp.auth.passwords`.

## Verified in a real browser and on Python 3.14

- **Redirect after login (Chromium 156, 14 Nov 2026).** The login page originally allowed form submission only to itself (`form-action 'self'`). In a real Chromium browser the login succeeded on the server, but the browser blocked the redirect back to `claude.ai` (console: CSP `form-action` violation, the page stayed on the login form). The page now allows form submission to itself and to the origins of the allowed return addresses, by default only `https://claude.ai`. The same flow was repeated in the same browser against a local server and the redirect went through. The automated tests cannot see this, because they run no browser rules; the unit test `test_login_page_may_only_submit_to_itself_and_the_allowed_return_origin` pins the header, and the browser check has to be repeated after any change to the page's security headers.
- **Python 3.14 (macOS).** The full suite passed on Python 3.14 (321 tests) as well as on Python 3.10.

## Verified with a real Claude connector (14 November 2026)

Setup: custom connector in the Claude desktop app, then mobile (text chat and live voice chat), against the real server behind a Cloudflare Tunnel. Observed:

- **Discovery and registration.** Claude accepted the discovery documents and registered itself as a public client; registration was not refused, so the issuer spelling (with trailing slash) and the registration request were accepted. The database held two registered clients afterwards.
- **Sign-in.** The login page and redirect worked in the desktop app and the connector became usable. After entering the passphrase the page showed once "request unknown or expired, please start again", although the connection worked afterwards. The cause is **not established** (a second call to the return page is the guess). Repeating the connection and watching the log is still open.
- **Host header and tunnel.** Requests through the tunnel reached the MCP endpoint (HTTP 200 for authorized calls, 401 without a token); no host header problem appeared.
- **Stateless transport and restart.** After a service restart, the desktop app and the mobile app used the tools again without a new sign-in.
- **Streaming and voice mode.** In a live voice chat Claude used the tools (draft, read-out, spoken confirmation, send). The server's send limit was reached once during testing and Claude relayed the refusal correctly.
- **Approval prompt.** The desktop app asked once whether the tool may be used and remembered "always allow"; later sends showed no prompt. The mobile app showed no prompt in the tests. This is client behaviour, not a protection of the server.

## Still not verified

- How Claude refreshes tokens (parallelism, retries). Access tokens live one hour; the tests so far did not run long enough to see a refresh in the log.
- Whether and when Claude calls the revocation endpoint, and the effect of `revoke-all` on a live connection.
- What the failed first return page was exactly (see above).
- Behaviour after the refresh token expires (90 days of inactivity).
