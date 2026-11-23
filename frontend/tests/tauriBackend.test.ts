import { afterEach, describe, expect, it, vi } from 'vitest'

const invoke = vi.fn()
vi.mock('@tauri-apps/api/core', () => ({ invoke: (...a: unknown[]) => invoke(...a) }))

import { tauriBackend } from '../src/wizard/tauriBackend'

afterEach(() => invoke.mockReset())

describe('the desktop backend when a step fails unexpectedly', () => {
  it('passes a plain reason from the helper program through unchanged', async () => {
    invoke.mockRejectedValue('Der Server ist nicht erreichbar.')
    expect(await tauriBackend().sendTestMail('a@b.de', false)).toEqual({ ok: false, reason: 'Der Server ist nicht erreichbar.' })
  })

  it('says what to do and keeps a short technical cause as details', async () => {
    invoke.mockRejectedValue(new Error('boom in the bridge'))
    const r = await tauriBackend().sendTestMail('a@b.de', false)
    expect(r.ok).toBe(false)
    if (!r.ok) expect(r.reason).toMatch(/^Der Schritt ist fehlgeschlagen\. Starte die App neu und versuche es noch einmal\. Details: boom in the bridge$/)
  })

  it('cuts very long causes', async () => {
    invoke.mockRejectedValue(new Error('x'.repeat(500)))
    const r = await tauriBackend().sendTestMail('a@b.de', false)
    if (r.ok) throw new Error('expected a failure')
    expect(r.reason.length).toBeLessThan(260)
  })

  it('asks the helper program to remove the Claude Desktop entry', async () => {
    invoke.mockResolvedValue({ ok: true, removed: true })
    expect(await tauriBackend().disconnectClaude()).toEqual({ ok: true })
    expect(invoke).toHaveBeenCalledWith('setup_disconnect_claude', undefined)
  })

  it('hands on why the program cannot be connected from where it runs', async () => {
    invoke.mockResolvedValue({ ok: true, mailbox: null, password_stored: false, claude_connected: false, passkeys: 0, install_problem: 'Ziehe die App in den Ordner „Programme“.' })
    expect((await tauriBackend().status()).install_problem).toBe('Ziehe die App in den Ordner „Programme“.')
  })
})

describe('the desktop backend for the tunnel', () => {
  it('reads Tailscale\'s state and the address from the helper program', async () => {
    invoke.mockResolvedValue({ ok: true, supported: true, state: 'ready', address: 'https://mac.example.ts.net', funnel: 'off', reason: null })
    expect(await tauriBackend().tunnelStatus()).toEqual({ state: 'ready', address: 'https://mac.example.ts.net', reason: null })
    expect(invoke).toHaveBeenCalledWith('setup_tunnel_status', undefined)
  })

  it('hands on the reason when Tailscale is not ready', async () => {
    invoke.mockResolvedValue({ ok: true, supported: true, state: 'stopped', address: null, funnel: null, reason: 'Tailscale ist ausgeschaltet.' })
    expect(await tauriBackend().tunnelStatus()).toEqual({ state: 'stopped', address: null, reason: 'Tailscale ist ausgeschaltet.' })
  })

  it('never claims a state when the helper program fails', async () => {
    invoke.mockRejectedValue('Das Mail-Assistant-Programm lässt sich nicht starten.')
    expect(await tauriBackend().tunnelStatus()).toEqual({ state: 'unreadable', address: null, reason: 'Das Mail-Assistant-Programm lässt sich nicht starten.' })
  })

  it('passes the confirmed address to the helper program and returns the result', async () => {
    invoke.mockResolvedValue({ ok: true, address: 'https://mac.example.ts.net', passphrase_placeholder: true })
    expect(await tauriBackend().tunnelEnable('https://mac.example.ts.net')).toEqual({ ok: true, address: 'https://mac.example.ts.net', passphrase_placeholder: true })
    expect(invoke).toHaveBeenCalledWith('setup_tunnel_enable', { address: 'https://mac.example.ts.net' })
  })

  it('hands on the reason and the Tailscale link of a refusal', async () => {
    invoke.mockResolvedValue({ ok: false, reason: 'Funnel ist noch nicht freigeschaltet.', url: 'https://login.tailscale.com/f/funnel?node=nABC' })
    expect(await tauriBackend().tunnelEnable('https://mac.example.ts.net')).toEqual({ ok: false, reason: 'Funnel ist noch nicht freigeschaltet.', url: 'https://login.tailscale.com/f/funnel?node=nABC' })
    invoke.mockResolvedValue({ ok: false, reason: 'Tailscale ist ausgeschaltet.' })
    expect(await tauriBackend().tunnelEnable('https://mac.example.ts.net')).toEqual({ ok: false, reason: 'Tailscale ist ausgeschaltet.' })
  })
})

describe('the desktop backend for the phone sign-in', () => {
  it('asks for the phone page only when told to, and for the local one without any argument', async () => {
    invoke.mockResolvedValue({ ok: true, url: 'https://mac.example.ts.net/passkey/enroll/abc' })
    expect(await tauriBackend().approvalLink(true)).toEqual({ ok: true, url: 'https://mac.example.ts.net/passkey/enroll/abc' })
    expect(invoke).toHaveBeenCalledWith('setup_approval_link', { phone: true })
    invoke.mockResolvedValue({ ok: true, url: 'http://localhost:47821/passkey/enroll/abc' })
    await tauriBackend().approvalLink()
    expect(invoke).toHaveBeenLastCalledWith('setup_approval_link', undefined)
  })

  it('passes the reason of a refused phone link through', async () => {
    invoke.mockResolvedValue({ ok: false, reason: 'Es sind schon zwei Passkeys eingerichtet, mehr geht nicht.' })
    expect(await tauriBackend().approvalLink(true)).toEqual({ ok: false, reason: 'Es sind schon zwei Passkeys eingerichtet, mehr geht nicht.' })
  })

  it('returns the new passphrase with the address, or the reason', async () => {
    invoke.mockResolvedValue({ ok: true, passphrase: 'ABCDE-FGHJK-MNPQR-STUVW-XYZ23', address: 'https://mac.example.ts.net' })
    expect(await tauriBackend().newPassphrase()).toEqual({ ok: true, passphrase: 'ABCDE-FGHJK-MNPQR-STUVW-XYZ23', address: 'https://mac.example.ts.net' })
    expect(invoke).toHaveBeenCalledWith('setup_passphrase_new', undefined)
    invoke.mockResolvedValue({ ok: false, reason: 'Der Tunnel ist noch nicht eingerichtet. Schalte ihn zuerst ein.' })
    expect(await tauriBackend().newPassphrase()).toEqual({ ok: false, reason: 'Der Tunnel ist noch nicht eingerichtet. Schalte ihn zuerst ein.' })
    invoke.mockResolvedValue({ ok: true })
    const odd = await tauriBackend().newPassphrase()
    expect(odd.ok).toBe(false) // an answer without a passphrase is never shown as a success
  })

  it('reads the phone state and treats anything unclear as "not set up, throw-away passphrase"', async () => {
    invoke.mockResolvedValue({ ok: true, enabled: true, address: 'https://mac.example.ts.net', passphrase_placeholder: false })
    expect(await tauriBackend().remoteStatus()).toEqual({ enabled: true, traces: true, address: 'https://mac.example.ts.net', passphrase_placeholder: false })
    invoke.mockResolvedValue({ ok: true, enabled: false, address: null })
    expect(await tauriBackend().remoteStatus()).toEqual({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
    invoke.mockRejectedValue(new Error('boom'))
    expect(await tauriBackend().remoteStatus()).toEqual({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
  })
  it('reads a leftover of phone use even when the service is not set up', async () => {
    invoke.mockResolvedValue({ ok: true, enabled: false, traces: true, address: null, passphrase_placeholder: true })
    expect((await tauriBackend().remoteStatus()).traces).toBe(true)
  })

  it('walks the way back through one command and passes the program\'s answer on', async () => {
    invoke.mockResolvedValue({ ok: true, removed: true, funnel_closed: true, notes: ['Hinweis.'] })
    expect(await tauriBackend().tunnelDisable()).toEqual({ ok: true, removed: true, funnel_closed: true, notes: ['Hinweis.'] })
    expect(invoke).toHaveBeenCalledWith('setup_tunnel_disable', undefined)
    invoke.mockResolvedValue({ ok: true })
    expect(await tauriBackend().tunnelDisable()).toEqual({ ok: true, removed: false, funnel_closed: false, notes: [] })
    invoke.mockResolvedValue({ ok: false, reason: 'Tailscale konnte den Funnel nicht ausschalten.' })
    expect(await tauriBackend().tunnelDisable()).toEqual({ ok: false, reason: 'Tailscale konnte den Funnel nicht ausschalten.' })
    invoke.mockRejectedValue('Der Schritt wurde unterbrochen.')
    expect(await tauriBackend().tunnelDisable()).toEqual({ ok: false, reason: 'Der Schritt wurde unterbrochen.' })
  })
})
