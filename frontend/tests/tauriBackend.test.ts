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
})
