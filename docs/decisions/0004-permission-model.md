# 0004 Permission model

Status: recipients and attachments built (rules in the environment file today); moving the rules into the database with an admin interface is planned; reading is planned.

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
- Every change to rules is written to the audit log.

**Check at use time.** A draft keeps a fixed copy of its attachments. Before sending, the recipient is checked again (built). The attachment's origin path is to be checked against the current folder rules again too (planned), so that withdrawing a folder also stops drafts already prepared.

## Consequences

- Simple to explain: one list of allowed folders, one list of denied folders, denial wins.
- Reading the user's mailbox and replying to mails is a different security model (incoming text is untrusted) and is **not** part of this decision.
