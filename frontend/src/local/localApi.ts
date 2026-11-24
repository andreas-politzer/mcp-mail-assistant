import { invoke } from '@tauri-apps/api/core'

// Local administration: what the screen after the wizard needs. The real version starts the bundled program's
// `local` command through the Rust side (no server, no port, no token). The mock serves the browser and tests.

export type Scope = 'own' | 'external'
export type Status = 'suggested' | 'active' | 'rejected' | 'removed' | 'expired' | 'blocked'

export interface Person {
  id: number
  name: string
  address: string
  aliases: string[]
  status: Status
  note: string
  suggested_by: string | null
  scope: Scope
}
export interface PasskeyRow {
  id: string
  name: string // empty for passkeys registered before names existed
  created_at: number
  last_used_at: number | null
}
export interface Snapshot {
  mailbox: { email: string; host: string; port: number; security: string } | null
  recipients: Person[]
  passkeys: PasskeyRow[]
}

export type Action =
  | { action: 'approve'; reference: string; name?: string; own: boolean }
  | { action: 'reject' | 'block' | 'unblock' | 'remove'; reference: string }
  | { action: 'scope'; reference: string; value: Scope }
  | { action: 'add'; name: string; address: string; own: boolean }
  | { action: 'passkey-remove'; reference: string }
  | { action: 'passkey-rename'; reference: string; name: string }

export type Done = { ok: true } | { ok: false; reason: string }

export interface LocalBackend {
  snapshot(): Promise<{ ok: true; data: Snapshot } | { ok: false; reason: string }>
  act(a: Action): Promise<Done>
}

const FAILED = 'Das ging nicht.'

export function tauriLocalBackend(): LocalBackend {
  const call = async (args: Record<string, unknown>): Promise<Record<string, unknown>> => {
    try {
      return await invoke<Record<string, unknown>>('local_admin', args)
    } catch (e) {
      return { ok: false, reason: typeof e === 'string' ? e : FAILED }
    }
  }
  return {
    async snapshot() {
      const r = await call({ action: 'snapshot' })
      return r.ok ? { ok: true, data: r as unknown as Snapshot } : { ok: false, reason: String(r.reason ?? FAILED) }
    },
    async act(a) {
      const r = await call(a as unknown as Record<string, unknown>)
      return r.ok ? { ok: true } : { ok: false, reason: String(r.reason ?? FAILED) }
    },
  }
}

// In-memory stand-in with a few entries, so the screen can be looked at in a browser.
export function mockLocalBackend(seed?: Partial<Snapshot>): LocalBackend {
  let next = 10
  const state: Snapshot = {
    mailbox: { email: 'andy@example.org', host: 'mail.infomaniak.com', port: 465, security: 'ssl' },
    recipients: [
      { id: 1, name: 'Velonautics', address: 'hello@velonautics.com', aliases: [], status: 'suggested', note: '', suggested_by: 'claude', scope: 'external' },
      { id: 2, name: 'Andy', address: 'andy@example.org', aliases: [], status: 'active', note: '', suggested_by: null, scope: 'own' },
    ],
    passkeys: [],
    ...seed,
  }
  const find = (ref: string) => state.recipients.find((r) => r.address === ref || String(r.id) === ref)
  return {
    async snapshot() {
      return { ok: true, data: structuredClone(state) }
    },
    async act(a) {
      if (a.action === 'passkey-remove') {
        const n = state.passkeys.length
        state.passkeys = state.passkeys.filter((k) => k.id !== a.reference)
        return state.passkeys.length < n ? { ok: true } : { ok: false, reason: 'Dieser Passkey wurde nicht gefunden.' }
      }
      if (a.action === 'passkey-rename') {
        const k = state.passkeys.find((x) => x.id === a.reference)
        if (!k) return { ok: false, reason: 'Dieser Passkey wurde nicht gefunden.' }
        k.name = a.name.trim()
        return { ok: true }
      }
      if (a.action === 'add') {
        state.recipients.push({ id: next++, name: a.name, address: a.address, aliases: [], status: 'active', note: '', suggested_by: null, scope: a.own ? 'own' : 'external' })
        return { ok: true }
      }
      const r = find(a.reference)
      if (!r) return { ok: false, reason: 'Das ging nicht: Empfänger nicht gefunden.' }
      if (a.action === 'approve') {
        r.status = 'active'
        r.scope = a.own ? 'own' : 'external'
        if (a.name) r.name = a.name
      } else if (a.action === 'scope') r.scope = a.value
      else if (a.action === 'reject') r.status = 'rejected'
      else if (a.action === 'block') r.status = 'blocked'
      else if (a.action === 'unblock') r.status = 'active'
      else if (a.action === 'remove') r.status = 'removed'
      return { ok: true }
    },
  }
}
