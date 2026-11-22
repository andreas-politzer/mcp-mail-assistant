# 0010: On macOS the SMTP password goes through `/usr/bin/security`

Status: accepted, 22 November 2026 (author's decision after a test on a Mac).

## Context

macOS asks for the device password whenever a program reads a keychain item that another program created. With the `keyring` library the program is our server, a Python file or the desktop app. Every rebuild counts as a different program, so the dialog came back many times during setup and testing.

## Decision

On macOS the password is stored, read and deleted with the system program `/usr/bin/security` (the password goes in over standard input as hex, never as an argument). It is the same program every time, so macOS asks nothing. Windows and Linux keep `keyring`. `OUTBOX_KEYCHAIN_BACKEND=keyring` switches macOS back. The value is stored as `ma1:` plus the hex of the password so that odd characters survive.

## Consequence (accepted)

Any process of the same user can read the SMTP password through `/usr/bin/security` without a dialog. Before, macOS asked once per program. The passkey approval still guards every send through this server, but it does not protect against a process that reads the password and talks to the mail server itself, for example a shell agent. Whoever needs that protection should keep the password out of the keychain (server mode with `OUTBOX_SMTP_PASSWORD`) or switch back to `keyring` and accept the dialogs. Signing the desktop app would restore stable per-program access, but would not close this gap for `security`.
