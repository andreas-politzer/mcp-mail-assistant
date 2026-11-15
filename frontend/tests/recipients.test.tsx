import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import { RecipientsTile } from '../src/tiles/RecipientsTile'
import { api, ask, asks, confirmLast, nextAsk, useSignedInServer } from './support/harness'

useSignedInServer()

const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\s+/g, ' ').trim()
const mount = () => render(<RecipientsTile ask={ask} />)
const list = async () => (await api('GET', '/admin/recipients')).data.recipients as { id: number; address: string; status: string; name: string }[]
const statusOf = async (address: string) => (await list()).find((r) => r.address === address)?.status

async function importCsv(csv: string) {
  const input = document.querySelector('input[type=file]') as HTMLInputElement
  const file = new File([csv], 'kontakte.csv', { type: 'text/csv' })
  fireEvent.change(input, { target: { files: [file] } })
  return await screen.findByTestId('import-plan')
}
const rows = (prefix: string, n: number) => ['Name,E-Mail', ...Array.from({ length: n }, (_, i) => `Person ${prefix}${i},${prefix}${i}@example.org`)].join('\n')
const click = (label: string) => fireEvent.click(screen.getByText(label))

describe('recipients against the real admin API', () => {
  it('adds a recipient after a confirmation and lists it as approved', async () => {
    mount()
    await screen.findByText('Freigegeben')
    fireEvent.change(screen.getByPlaceholderText('Name'), { target: { value: 'Neue Person' } })
    fireEvent.change(screen.getByPlaceholderText('adresse@beispiel.de'), { target: { value: 'neu1@example.org' } })
    click('＋ Hinzufügen')
    expect((await nextAsk()).title).toBe('Empfänger hinzufügen?')
    expect(await statusOf('neu1@example.org')).toBeUndefined() // nothing before the confirmation
    await confirmLast()
    await screen.findByTestId('recipient-neu1@example.org')
    expect(await statusOf('neu1@example.org')).toBe('active')
  })

  it('refuses an empty name and a bad address without asking the server', async () => {
    mount()
    await screen.findByText('Freigegeben')
    click('＋ Hinzufügen')
    await screen.findByText('Ein Empfänger braucht einen Namen.')
    fireEvent.change(screen.getByPlaceholderText('Name'), { target: { value: 'X' } })
    fireEvent.change(screen.getByPlaceholderText('adresse@beispiel.de'), { target: { value: 'kein-at' } })
    click('＋ Hinzufügen')
    await screen.findByText('Das ist keine gültige Adresse.')
    expect(asks.length).toBe(0)
  })

  it('blocks for good, unblocks after a confirmation, and a blocked address cannot be added', async () => {
    await api('POST', '/admin/recipients', { name: 'Block Me', address: 'blockme@example.org', aliases: [] })
    mount()
    const row = await screen.findByTestId('recipient-blockme@example.org')
    fireEvent.click(within(row).getByText('Sperren'))
    expect((await nextAsk()).title).toBe('Adresse sperren?')
    await confirmLast()
    await waitFor(async () => expect(await statusOf('blockme@example.org')).toBe('blocked'))
    const blocked = await screen.findByTestId('recipient-blockme@example.org')
    fireEvent.change(screen.getByPlaceholderText('Name'), { target: { value: 'Block Me' } })
    fireEvent.change(screen.getByPlaceholderText('adresse@beispiel.de'), { target: { value: 'blockme@example.org' } })
    click('＋ Hinzufügen')
    await screen.findByText('Diese Adresse ist gesperrt. Hebe zuerst die Sperre auf.')
    expect(asks.length).toBe(0)
    expect(await statusOf('blockme@example.org')).toBe('blocked')
    fireEvent.click(within(blocked).getByText('Sperre aufheben'))
    expect((await nextAsk()).title).toBe('Sperre aufheben?')
    await confirmLast()
    await waitFor(async () => expect(await statusOf('blockme@example.org')).toBe('removed'))
  })

  it('removes a recipient after a confirmation', async () => {
    await api('POST', '/admin/recipients', { name: 'Weg', address: 'weg1@example.org', aliases: [] })
    mount()
    fireEvent.click(within(await screen.findByTestId('recipient-weg1@example.org')).getByText('Entfernen'))
    expect((await nextAsk()).title).toBe('Empfänger entfernen?')
    await confirmLast()
    await waitFor(async () => expect(await statusOf('weg1@example.org')).toBe('removed'))
  })

  it('previews an import, creates candidates only, and never approves anything by itself', async () => {
    mount()
    await screen.findByText('Freigegeben')
    const csv = ['Name,E-Mail', 'Imp Eins,imp1@example.org', 'Imp Zwei,imp2@example.org', 'Imp Drei,imp3@example.org', 'Doppelt,imp1@example.org', 'Kaputt,kaputt-ohne-at'].join('\n')
    await importCsv(csv)
    expect(text(screen.getByTestId('import-summary'))).toContain('3 neu als Kandidat')
    expect(await statusOf('imp1@example.org')).toBeUndefined() // the preview stores nothing
    click('3 als Kandidaten anlegen')
    await screen.findByTestId('candidate-imp1@example.org')
    expect(await statusOf('imp1@example.org')).toBe('suggested')
    expect(text(document.body)).toContain('Noch nichts davon ist freigegeben')
    expect(asks.length).toBe(0)
  })

  it('decides on candidates: approve some, reject some, and rejected ones never come back', async () => {
    mount()
    await screen.findByText('Freigegeben')
    await importCsv(['Name,E-Mail', 'Dec Eins,dec1@example.org', 'Dec Zwei,dec2@example.org', 'Dec Drei,dec3@example.org'].join('\n'))
    click('3 als Kandidaten anlegen')
    await screen.findByTestId('candidate-dec1@example.org')
    const check = (a: string) => fireEvent.click(within(screen.getByTestId(`candidate-${a}`)).getByRole('checkbox'))
    check('dec1@example.org')
    check('dec2@example.org')
    fireEvent.click(screen.getByText(/Ausgewählte freigeben/))
    expect((await nextAsk()).title).toBe('Importierte Kontakte freigeben?')
    await confirmLast()
    await waitFor(async () => expect(await statusOf('dec1@example.org')).toBe('active'))
    expect(await statusOf('dec2@example.org')).toBe('active')
    expect(await statusOf('dec3@example.org')).toBe('suggested') // not chosen, not touched
    check('dec3@example.org')
    fireEvent.click(screen.getByText('Ausgewählte ablehnen'))
    expect((await nextAsk()).title).toBe('Kontakte ablehnen?')
    await confirmLast()
    await waitFor(async () => expect(await statusOf('dec3@example.org')).toBe('rejected'))
    // the same file again: nothing becomes a candidate any more
    await importCsv(['Name,E-Mail', 'Dec Eins,dec1@example.org', 'Dec Zwei,dec2@example.org', 'Dec Drei,dec3@example.org'].join('\n'))
    const summary = text(screen.getByTestId('import-summary'))
    expect(summary).toContain('0 neu als Kandidat')
    expect(summary).toContain('2 schon freigegeben')
    expect(summary).toContain('1 kommen nie wieder')
    expect((screen.getByText('0 als Kandidaten anlegen') as HTMLButtonElement).disabled).toBe(true)
  })

  it('imports a large file in several passes', async () => {
    mount()
    await screen.findByText('Freigegeben')
    await importCsv(rows('big', 1200))
    expect(text(screen.getByTestId('import-summary'))).toContain('1200 neu als Kandidat')
    click('1200 als Kandidaten anlegen')
    await screen.findByText(/1200 Kandidaten angelegt/, undefined, { timeout: 30000 })
    expect((await list()).filter((r) => r.address.startsWith('big') && r.status === 'suggested').length).toBe(1200)
  }, 60000)

  it('reads only the first 2000 rows of a longer file and says so', async () => {
    mount()
    await screen.findByText('Freigegeben')
    await importCsv(rows('huge', 2100))
    expect(text(screen.getByTestId('import-summary'))).toContain('2000 neu als Kandidat')
    expect(text(document.body)).toContain('mehr als 2000')
    click('Abbrechen')
    cleanup()
  })

  it('shows the server problem instead of an empty list when the server does not answer', async () => {
    const real = globalThis.fetch
    globalThis.fetch = (() => Promise.reject(new TypeError('failed'))) as typeof fetch
    try {
      mount()
      await screen.findByText(/Der Server antwortet nicht/)
      expect(screen.getByText('Erneut laden')).toBeTruthy()
    } finally {
      globalThis.fetch = real
    }
  })
})
