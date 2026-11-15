# 0004 Permission model

Status: recipients and attachments built; the attachment rules live in the database (schema 4), the admin interface for them is planned; reading is planned.

## Context

The admin tool is a small definition tool: what may the assistant read, what may it attach, to whom may it send. It is opened rarely, not daily.

## Decision

**Three areas.**

1. **Recipients** (built): who may receive mail. A suggestion is not permission; only the user activates an address.
2. **Attachments** (built): which files may leave as attachments. One permission, not two: *may this file be attached and sent?*
3. **Reading** (planned): which files the assistant may read at all. Today this is not governed by this server but by the separate service that reads notes. The admin app must not show a control it cannot enforce, so this area appears only once it can be enforced. Whether that means changing the other service is a separate decision.

**Attachment rules.**

- Folders are allowed or denied. **A denial always wins**: a folder under a denied folder cannot be allowed again. The interface will say where a state comes from (for example "denied, inherited from Banking").
- Fixed protections stay in code and cannot be loosened by a click: Markdown only, no hidden or sensitive-looking names, secret scan of the content, size and count limits, path checks that refuse links and tricks.
- Folders are chosen from existing folders, not typed as free paths.
- The file source is a plain folder. It is not tied to any particular notes application.
- Every change to rules is written to the audit log, in the same transaction as the change.
- Rules live in the table `file_access_rules` and are bound to a root folder (`root_path`) and a scope (`attach` now, `read` later). Paths are stored relative, normalised (Unicode NFC, case-folded) and compared component by component, so a rule for `Privat` never hits `Privatkopie`. A different root folder never inherits rules.
- A single file can be blocked, not allowed: only folders can be allowed.
- Rows are never deleted. "Clearing" a rule sets its effect to `cleared`.
- The folder lists in the environment file are only a starting point: they are taken over once per root folder when the server first starts with that root, then the database decides and those lines are ignored.

**Check at use time.** Recipient and attachment rules are checked inside the same SQLite write transaction (`BEGIN IMMEDIATE`) that claims the draft for sending. While it runs no other writer, such as a rule change from the admin side, can commit, so the rules it saw are the rules under which the draft was claimed. If a check fails, nothing is claimed. The SMTP call happens after the transaction and never inside it; the small window between the claim and the SMTP call is accepted. A draft keeps a fixed copy of its attachments. Before sending, the recipient is checked again, and so is the origin path of every attachment against the folder rules as they are at that moment (both built). Withdrawing a folder therefore also stops drafts that were already prepared. The check looks at the path text only; the stored copy is not read from disk again. A draft made before the origin path was recorded (older than schema version 3) is refused and has to be prepared again.

## Delivery guarantee and a possible later check

- **At most once.** A draft is claimed once and its final states (`sent`, `failed`, `unknown`) cannot change again, so this program never sends the same draft twice. If the connection breaks after the message was handed to the SMTP server, the result is `unknown`: the mail may or may not have arrived, and the user has to check. Exactly-once delivery is not promised, because SMTP cannot give it.
- **Possible later check, not built.** When sending, the program could also verify that the original file still exists with the same hash. It would stop a mail whose source file changed after the draft was prepared. It is not needed for the current guarantee (what is sent is the checked copy made when the draft was prepared) and would add different behaviour, so it stays a candidate for later.

## Consequences

- Simple to explain: one list of allowed folders, one list of denied folders, denial wins.
- Reading the user's mailbox and replying to mails is a different security model (incoming text is untrusted) and is **not** part of this decision.
