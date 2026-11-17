# 0007: Approval page for the local (stdio) setup

Status: **laboratory state, not adopted.** Built and tested with a software passkey, with real Chrome and a virtual authenticator, and end to end through the real stdio server. Not yet tried in the embedded browser of Claude desktop.

## Problem

The approval page of [decision 0006](0006-approval-outside-the-chat.md) needs a public HTTPS address. Someone who runs the Mail Assistant only on one computer (stdio, no domain, no tunnel) could not approve mail to external recipients at all. That is safe, but it makes the first steps harder than they need to be. A domain must not be a precondition for local use, and the security design must not depend on whether a user owns one.

## Decision (proposed)

For a local setup the approval page is served on `http://localhost:<port>` by a small web server that the stdio process starts in a background thread.

- Browsers treat `localhost` as a secure context, so passkeys work there without a domain or certificate.
- `OUTBOX_APPROVAL_HOST=localhost` selects this form. Only the exact name `localhost` may use `http`. Every other host must be a public name and uses `https`.
- All checks are the same as for the public origin: exact Host, exact Origin on every POST, passkey with user verification, one approval per draft, hash of the content, size and rate limits. Nothing is relaxed because the server is on the same machine.
- The listener binds to `127.0.0.1` only and writes nothing to standard output (that is the protocol channel).
- A busy port only moves the page to the next free one (the configured port and the nine after it). Several stdio servers can run at the same time, for example when Claude desktop starts the server more than once, and each needs its own port. The page that answers on a port knows only that port: every origin, Host and link check uses the port that was actually taken, exactly as strict as before. If no port in that range is free or the page cannot start, approval stays switched off and mail to external recipients stays unsendable. There is no fallback to a weaker check.
- The command that prints the one-time passkey link looks for the page that answers (by its own style sheet, so another program on the port does not count) and prints that port. If no page answers, it says that the assistant has to be started first.
- The HTTP server (public process) refuses the local form.

## Things to know

1. **The port is a laboratory setting.** `OUTBOX_APPROVAL_PORT` is only the first port tried; a user must not have to pick or configure a port. The first real test showed why the fallback is needed: Claude desktop started the server twice, and the second one could not take the port. The number of ports tried (ten) is an internal limit, not a setting. Which port is the first one, and whether it is fixed or remembered in the data folder, is still an open design question; links and passkeys do not depend on it, because every link is created with the port of the instance that makes it.
2. **This is desktop only.** The local page lets the person at that computer approve. A phone cannot reach `localhost` on the computer. Approving from a phone still needs a reachable public approval address (the public setup of decision 0006).
3. **The page lives as long as the stdio process.** The listener runs only while the assistant app has the server running. A link does not work when the app is closed or the server was restarted (a restarted server keeps the draft and the stored approval, but the page is only reachable while it runs). The setup instructions and the interface have to say so.
4. **Passkeys for `localhost` are not bound to a port; this application is.** A passkey is tied to the relying-party name `localhost`, which is the same for every port and every local web page. The server therefore checks the complete origin, including the port, in every registration and approval (`http://localhost:<port>`), and checks the relying-party hash. A page on another local port cannot get an approval accepted for this one. A side effect is useful: instances with their own ports and the same data folder share passkeys, drafts and approvals (the database is shared), and an approval link made by one instance works on that instance's page. The cost is that the browser may offer the passkey to any page on `localhost`; only the user verification prompt and the server checks stand in between.

## What it does not protect against

A model that runs on the same computer and can read the data folder or the `.env` can get the SMTP password and send mail without this server. Approval stops a model that uses the mail tool or clicks a button on the page. It does not stop a model with the user's file rights. See the open topic *protecting the SMTP credentials* (operating system key store, or separating the assistant from the mail service) in the security notes.

## Open questions

- Embedded browser of Claude desktop: does it offer passkeys on `localhost`? (to be tried with the app)
- Other browsers (Firefox, Safari, Edge) and Windows: not tried in the lab.
- Which port is tried first without user input (fixed default or remembered), and what the setup shows about it. A page that moved to a later port is only mentioned on standard error; the person is not told.
- An approval link from an instance that has ended does not work any more (the page lives as long as its process). Only the link printed by the passkey command follows the running page.
- Several users on one computer: another user can take the port first. If all ports of the range are taken, the page does not start and approval stays off (safe, but a way to disturb).
