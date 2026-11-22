# 0006: Approval outside the chat

Status: implemented in code and tested with software passkeys. Not yet tested on a real server with a real device (the isolated prototype was, on an iPhone with Face ID and on a Mac through the iCloud keychain). Other recipients are enabled only after that end-to-end test.

Context: the server cannot prove that the final "yes" in the conversation came from a human (see README, "Honest limits").

Decision: recipients who are not the owner's own mailboxes require an approval that is bound to exactly one draft, can be used once, expires quickly, and is given through a channel the assistant cannot use. It sits in front of the existing atomic claim; claim, limits, the single SMTP attempt and the states `sent`, `failed` and `unknown` stay unchanged. The owner's own addresses keep the current flow.

Condition: other recipients are not enabled until the stage is built and acceptable in daily use. A throw-away prototype outside this repository comes first.

Decided and built:
- Technology: WebAuthn passkeys (discoverable, user verification required), verified server-side with `webauthn` (py_webauthn). Since 22 Nov 2026 the kind of authenticator is not restricted: Touch ID, Face ID and Windows Hello stay the default, and a computer without them can use a phone (QR code) or a USB security key. The protection is the same, because the passkey must still be unlocked by the person (user verification stays required). Before, it said "platform authenticator" only, which locked out a desktop computer without a built-in sensor.
- Origin: the public host of the mail server itself, `https://<OUTBOX_APPROVAL_HOST>`. The passkey is bound to that host name.
- Representation: `recipients.scope` is `own` or `external` (schema 6; existing active recipients become `own`, everything new starts `external`). Only the admin side changes it; loosening needs a confirmation.
- Enrollment only through the admin side: a one-time link that lives ten minutes, at most two passkeys.
- What the approval page shows: recipient, subject, the full text, attachments with name and size.
- The approval binds to the draft fingerprint (recipient, subject, text, attachment names and hashes). It is checked inside the same transaction in which the draft is claimed.
- Used-up challenge: a challenge is consumed by the first attempt that names it, in its own committed transaction. The verification and the approval run in a second transaction that is rolled back as a whole on any failure.

Honest limit: the approval proves an authenticated approval of this exact draft, not that the owner read it.
