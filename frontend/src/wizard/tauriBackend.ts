import { invoke } from '@tauri-apps/api/core'
import type { MailboxSettings, Outcome, SetupBackend, SetupStatus, TunnelState } from './setupApi'

// The real backend: Tauri commands that start the bundled server program (see src-tauri/src/setup.rs).
// The password is an argument of exactly one command and is not kept here.

interface Reply {
  ok: boolean
  reason?: string
  lines?: string[]
  url?: string
  state?: TunnelState
  address?: string | null
  passphrase_placeholder?: boolean
}

const FAILED = 'Der Schritt ist fehlgeschlagen. Starte die App neu und versuche es noch einmal.'

// A short technical reason for the rare unexpected failure, so a bug report can say what happened.
function detail(e: unknown): string {
  const text = (e instanceof Error ? e.message : typeof e === 'object' && e !== null ? JSON.stringify(e) : String(e ?? '')).trim()
  return text ? ` Details: ${text.slice(0, 160)}` : ''
}

async function call<T>(command: string, args?: Record<string, unknown>): Promise<T | { ok: false; reason: string }> {
  try {
    return await invoke<T>(command, args)
  } catch (e) {
    return { ok: false, reason: typeof e === 'string' ? e : `${FAILED}${detail(e)}` }
  }
}

const outcome = (r: Reply): Outcome => (r.ok ? { ok: true } : { ok: false, reason: r.reason ?? FAILED })

export function tauriBackend(): SetupBackend {
  return {
    async status() {
      const r = await call<SetupStatus & Reply>('setup_status')
      if (!r.ok || !('mailbox' in r)) return { mailbox: null, password_stored: false, claude_connected: false, passkeys: 0 }
      return { mailbox: r.mailbox, password_stored: r.password_stored, claude_connected: r.claude_connected, passkeys: r.passkeys ?? 0, install_problem: r.install_problem ?? null }
    },
    async saveMailbox(s: MailboxSettings, password: string) {
      return outcome(await call<Reply>('setup_save_mailbox', { email: s.email, host: s.host, port: s.port, security: s.security, password }))
    },
    async sendTestMail(to: string, markOwn: boolean) {
      return outcome(await call<Reply>('setup_send_test', { to, markOwn }))
    },
    async previewClaudeConnection() {
      const r = await call<Reply>('setup_preview_claude')
      return r.ok && r.lines ? { ok: true as const, lines: r.lines } : { ok: false as const, reason: r.reason ?? FAILED }
    },
    async connectClaude() {
      return outcome(await call<Reply>('setup_connect_claude'))
    },
    async disconnectClaude() {
      return outcome(await call<Reply>('setup_disconnect_claude'))
    },
    async approvalLink() {
      const r = await call<Reply>('setup_approval_link')
      return r.ok && r.url ? { ok: true as const, url: r.url } : { ok: false as const, reason: r.reason ?? FAILED }
    },
    async openLink(url: string) {
      return outcome(await call<Reply>('open_link', { url }))
    },
    async tunnelStatus() {
      const r = (await call<Reply>('setup_tunnel_status')) as Reply
      if (!r.ok || !r.state) return { state: 'unreadable' as const, address: null, reason: r.reason ?? FAILED }
      return { state: r.state, address: r.address ?? null, reason: r.reason ?? null }
    },
    async tunnelEnable(address: string) {
      const r = (await call<Reply>('setup_tunnel_enable', { address })) as Reply
      if (r.ok && r.address) return { ok: true as const, address: r.address, passphrase_placeholder: r.passphrase_placeholder ?? true }
      return { ok: false as const, reason: r.reason ?? FAILED, ...(r.url ? { url: r.url } : {}) }
    },
  }
}
