import type { Security } from './presets'

// Everything the wizard needs from the computer. The real version (Tauri commands) comes later; the wizard
// is built against this interface. The password goes through exactly one call and is never kept by the wizard.

export interface MailboxSettings {
  email: string
  host: string
  port: number
  security: Security
}

export type Outcome = { ok: true } | { ok: false; reason: string }

export interface SetupStatus {
  mailbox: MailboxSettings | null
  password_stored: boolean
  claude_connected: boolean
  // Registered passkeys on the local approval page.
  passkeys: number
}

export interface SetupBackend {
  // The real state, so the wizard only asks for what is missing.
  status(): Promise<SetupStatus>
  // Stores the password in the system keychain and checks the sign-in at the provider.
  saveMailbox(settings: MailboxSettings, password: string): Promise<Outcome>
  // Sends one mail to the person's own address; optionally marks that address as "own".
  sendTestMail(to: string, markOwn: boolean): Promise<Outcome>
  // What connecting Claude Desktop will change, as plain sentences. Changes nothing.
  previewClaudeConnection(): Promise<{ ok: true; lines: string[] } | { ok: false; reason: string }>
  // Makes the change (after a backup copy of the configuration).
  connectClaude(): Promise<Outcome>
  // One-time link to register a passkey on the local approval page. Only works once Claude Desktop runs the server.
  approvalLink(): Promise<{ ok: true; url: string } | { ok: false; reason: string }>
  // Opens that link in the browser of this computer.
  openLink(url: string): Promise<Outcome>
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

// Stand-in for the browser and for tests. Password "falsch" simulates a rejected sign-in,
// a test address starting with "fehler" simulates a failed send.
export function mockBackend(delay = 700): SetupBackend {
  let passkeys = 0 // the demo "registers" a passkey as soon as the link is opened
  return {
    async status() {
      return { mailbox: null, password_stored: false, claude_connected: false, passkeys }
    },
    async saveMailbox(_s, password) {
      await wait(delay)
      return password === 'falsch'
        ? { ok: false, reason: 'Der Anbieter hat die Anmeldung abgelehnt. Passwort oder App-Passwort stimmt nicht.' }
        : { ok: true }
    },
    async sendTestMail(to) {
      await wait(delay)
      return to.toLowerCase().startsWith('fehler')
        ? { ok: false, reason: 'Der Anbieter hat die Testmail nicht angenommen.' }
        : { ok: true }
    },
    async previewClaudeConnection() {
      await wait(delay / 3)
      return {
        ok: true,
        lines: [
          'In der Konfigurationsdatei von Claude Desktop wird ein Eintrag „mail-assistant“ ergänzt.',
          'Alle anderen Einstellungen bleiben unberührt.',
          'Vorher wird eine Kopie der Datei neben das Original gelegt.',
        ],
      }
    },
    async connectClaude() {
      await wait(delay)
      return { ok: true }
    },
    async approvalLink() {
      await wait(delay / 3)
      return { ok: true, url: 'http://localhost:47821/passkey/enroll/demo' }
    },
    async openLink() {
      passkeys = 1
      return { ok: true }
    },
  }
}
