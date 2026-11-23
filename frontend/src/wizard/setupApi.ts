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
  // Why the program cannot be connected from where it runs now (temporary macOS folder, disk image), or null.
  install_problem?: string | null
}

export type TunnelState =
  | 'ready'
  | 'missing'
  | 'stopped'
  | 'needs_login'
  | 'needs_approval'
  | 'not_running'
  // Tailscale answered, but not in a way we understand (or not in time). The reason names a short cause.
  | 'unreadable'
  | 'no_magicdns'
  | 'funnel_busy'
  | 'funnel_unknown'
  | 'unsupported'

export interface TunnelStatus {
  state: TunnelState
  // Only set when the state is "ready".
  address: string | null
  // The reason in plain German when it is not ready.
  reason: string | null
}

export type TunnelOutcome =
  | { ok: true; address: string; passphrase_placeholder: boolean }
  // `url`: a Tailscale page where the person has to switch something on first.
  | { ok: false; reason: string; url?: string }

export type DisableOutcome =
  // `removed`: there was something to remove. `notes`: plain German remarks (e.g. Tailscale was not reachable).
  | { ok: true; removed: boolean; funnel_closed: boolean; notes: string[] }
  | { ok: false; reason: string }

export interface RemoteStatus {
  enabled: boolean
  // Any leftover of phone use, also half of it: the way back is offered as long as this is true.
  traces: boolean
  address: string | null
  // True while only a throw-away passphrase exists (nobody can sign in yet).
  passphrase_placeholder: boolean
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
  // Removes only our own entry from Claude Desktop's configuration (after a backup copy). Fine if there is none.
  disconnectClaude(): Promise<Outcome>
  // One-time link to register a passkey on the local approval page (needs Claude Desktop running the server).
  // With `phone`: on the public page instead, to be opened on the phone itself.
  approvalLink(phone?: boolean): Promise<{ ok: true; url: string } | { ok: false; reason: string }>
  // Opens that link in the browser of this computer (the enrollment link and a few Tailscale pages only).
  openLink(url: string): Promise<Outcome>
  // Phone use: Tailscale's state and the public address, shown before anything changes. Changes nothing.
  tunnelStatus(): Promise<TunnelStatus>
  // Starts the service, switches the Funnel on and tries the address. `address` is the one the person confirmed.
  tunnelEnable(address: string): Promise<TunnelOutcome>
  // Phone use: is the service set up, what is its address, and is its passphrase still the throw-away one?
  remoteStatus(): Promise<RemoteStatus>
  // The way back to "only this computer": Funnel off, service removed, phone sign-ins ended. Mailbox, recipients
  // and passkeys stay. Safe to repeat.
  tunnelDisable(): Promise<DisableOutcome>
  // A new sign-in passphrase for the phone, returned exactly once. Earlier sign-ins end; only its hash is stored.
  newPassphrase(): Promise<{ ok: true; passphrase: string; address: string } | { ok: false; reason: string }>
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

// Stand-in for the browser and for tests. Password "falsch" simulates a rejected sign-in,
// a test address starting with "fehler" simulates a failed send.
export function mockBackend(delay = 700): SetupBackend {
  let phoneOn = true // the demo has phone use switched on until the way back is walked
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
    async disconnectClaude() {
      await wait(delay)
      return { ok: true }
    },
    async approvalLink(phone) {
      await wait(delay / 3)
      if (phone) {
        passkeys += 1 // the demo "registers" the phone's passkey as soon as the link exists
        return { ok: true, url: 'https://mein-mac.example.ts.net/passkey/enroll/demo' }
      }
      return { ok: true, url: 'http://localhost:47821/passkey/enroll/demo' }
    },
    async openLink() {
      passkeys = 1
      return { ok: true }
    },
    async tunnelStatus() {
      await wait(delay / 3)
      return { state: 'ready', address: 'https://mein-mac.example.ts.net', reason: null }
    },
    async tunnelEnable(address) {
      await wait(delay)
      return { ok: true, address, passphrase_placeholder: true }
    },
    async tunnelDisable() {
      await wait(delay)
      const removed = phoneOn
      phoneOn = false
      return { ok: true, removed, funnel_closed: removed, notes: [] }
    },
    async remoteStatus() {
      return { enabled: phoneOn, traces: phoneOn, address: 'https://mein-mac.example.ts.net', passphrase_placeholder: true }
    },
    async newPassphrase() {
      await wait(delay / 3)
      return { ok: true, passphrase: 'ABCDE-FGHJK-MNPQR-STUVW-XYZ23', address: 'https://mein-mac.example.ts.net' }
    },
  }
}
