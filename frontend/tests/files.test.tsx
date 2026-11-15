import { mkdirSync, rmSync, symlinkSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, screen, waitFor, within } from '@testing-library/react'
import {
  addFolderSource, api, asks, clickRow, confirmLast, nextAsk, currentPaths, fake, makeDir, mountFiles, node,
  openSource, realFetch, sourceByLabel, storedFiles, useSignedInServer,
} from './support/harness'

useSignedInServer()

const PROJECT_FILES = {
  'Projekte/a.md': 'alpha',
  'Projekte/Sub/b.md': 'beta',
  'Projekte/passwort.md': 'secret name',
  'Privat/c.md': 'gamma',
  'root.md': 'root',
  'notizen.pdf': 'not text',
  '.versteckt.md': 'hidden',
}

async function setUp(label: string, files = PROJECT_FILES) {
  const dir = makeDir(files)
  mountFiles()
  await addFolderSource(dir, label)
  await openSource(label)
  return dir
}

const text = (el: HTMLElement) => (el.textContent ?? '').replace(/\s+/g, ' ').trim()

async function allowAndSync(dirLabel: string, folder = 'Projekte') {
  await clickRow(folder, 'Freigeben')
  await confirmLast()
  fireEvent.click(await screen.findByText('Mit diesem Computer abgleichen'))
  await screen.findByText('Vorschau des Abgleichs')
  return folder
}
const runSync = async () => {
  fireEvent.click(await screen.findByText('Abgleich ausführen'))
  return await screen.findByTestId('sync-done', undefined, { timeout: 30000 })
}
const startSync = async () => {
  fireEvent.click(await screen.findByText('Mit diesem Computer abgleichen'))
  await waitFor(() => expect(screen.queryByText('Vorschau des Abgleichs') ?? screen.queryByTestId('sync-error')).toBeTruthy(), { timeout: 20000 })
}

describe('sources and the tree', () => {
  it('lists the notes folder from the server without sync and offers no remove', async () => {
    mountFiles()
    expect(await screen.findByText('Ordner auf dem Server')).toBeTruthy()
    fireEvent.click(await screen.findByText('Öffnen'))
    await screen.findByText('← Alle Quellen')
    expect(await node('Projekte')).toBeTruthy()
    expect(screen.queryByText('Mit diesem Computer abgleichen')).toBeNull()
    expect(screen.queryByText('Entfernen')).toBeNull()
  })

  it('adds a folder, links it locally and shows only folders and text files', async () => {
    await setUp('Testquelle')
    expect(fake.links.size).toBe(1)
    expect(await node('Projekte')).toBeTruthy()
    expect(await node('Privat')).toBeTruthy()
    expect(await node('root.md')).toBeTruthy()
    expect(screen.queryByTestId('node-notizen.pdf')).toBeNull()
    expect(screen.queryByTestId('node-.versteckt.md')).toBeNull()
    // no path of this computer reaches the server
    const src = await sourceByLabel('Testquelle')
    const audit = await api('GET', '/admin/audit?limit=50')
    const dump = JSON.stringify([src, audit.data])
    for (const l of fake.links.values()) expect(dump).not.toContain(l.path)
  })

  it('shows permissions: allow a folder, block inside it, block always wins, unblock asks first', async () => {
    await setUp('Rechte')
    const src = (await sourceByLabel('Rechte'))!
    await clickRow('Projekte', 'Freigeben')
    expect((await nextAsk()).title).toBe('Ordner freigeben?')
    await confirmLast()
    expect(text(await node('Projekte'))).toContain('erlaubt')
    // children are allowed through the folder
    fireEvent.click(within(await node('Projekte')).getByLabelText('aufklappen'))
    expect(text(await node('Projekte/Sub'))).toContain('erlaubt über „Projekte“')
    // blocking is immediate and needs no confirmation
    await clickRow('Projekte/Sub', 'Sperren')
    await waitFor(() => expect(text(screen.getByTestId('node-Projekte/Sub'))).toContain('gesperrt'))
    await new Promise((r) => setTimeout(r, 400))
    expect(asks.length).toBe(0)
    // allowing the parent again does not undo the block below it
    const ev = await api('POST', `/admin/sources/${src.id}/evaluate`, { paths: ['Projekte/Sub/b.md', 'Projekte/a.md'] })
    expect(ev.data.results.map((r: { state: string }) => r.state)).toEqual(['denied', 'allowed'])
    // lifting the block loosens: it asks
    await clickRow('Projekte/Sub', 'Sperre aufheben')
    expect((await nextAsk()).title).toBe('Sperre aufheben?')
    await confirmLast()
    const ev2 = await api('POST', `/admin/sources/${src.id}/evaluate`, { paths: ['Projekte/Sub/b.md'] })
    expect(ev2.data.results[0].state).toBe('allowed')
    // a file can only be blocked, never allowed on its own
    expect(within(await node('root.md')).queryByText('Freigeben')).toBeNull()
    expect(within(await node('root.md')).getByText('Sperren')).toBeTruthy()
  })

  it('renames a source and removes it with the server copies, forgetting the local place', async () => {
    await setUp('Alt')
    fireEvent.click(screen.getByText('← Alle Quellen'))
    fireEvent.click(await screen.findByText('Umbenennen'))
    const input = await screen.findByDisplayValue('Alt')
    fireEvent.change(input, { target: { value: 'Neu' } })
    fireEvent.click(screen.getByText('Umbenennen', { selector: 'button.font-bold' }))
    await screen.findByText(/Neu/)
    expect(await sourceByLabel('Neu')).toBeTruthy()
    fireEvent.click(screen.getAllByText('Entfernen')[0])
    expect((await nextAsk()).title).toBe('Quelle entfernen?')
    await confirmLast()
    await screen.findByText(/Entfernt\./)
    expect(await sourceByLabel('Neu')).toBeUndefined()
    expect(fake.links.size).toBe(0)
  })
})

describe('synchronisation', () => {
  it('copies only what is allowed, refuses protected names, then handles change and deletion', async () => {
    const dir = await setUp('Sync')
    const src = (await sourceByLabel('Sync'))!
    await allowAndSync('Sync')
    const summary = text(screen.getByTestId('sync-summary'))
    expect(summary).toContain('2 neu') // a.md and Sub/b.md; passwort.md is a protected name
    expect(text(await screen.findByText(/werden nicht übertragen|wird nicht übertragen/))).toMatch(/nicht übertragen/)
    // nothing is stored before the person confirms
    expect(await currentPaths(src.id)).toEqual([])
    await runSync()
    expect(await currentPaths(src.id)).toEqual(['Projekte/Sub/b.md', 'Projekte/a.md'].sort())
    // version and identifier are shown
    expect((await screen.findAllByText(/Version 1 · Kennung/)).length).toBe(2)

    // a change and a deletion on the computer
    writeFileSync(join(dir, 'Projekte/a.md'), 'alpha changed')
    rmSync(join(dir, 'Projekte/Sub/b.md'))
    await startSync()
    const s2 = text(screen.getByTestId('sync-summary'))
    expect(s2).toContain('1 geändert')
    expect(s2).toContain('1 wird vom Server gelöscht')
    // half of the copies would go: a clear yes is required
    const run = screen.getByText('Abgleich ausführen') as HTMLButtonElement
    expect(run.disabled).toBe(true)
    fireEvent.click(screen.getByRole('checkbox'))
    expect(run.disabled).toBe(false)
    await runSync()
    expect(await currentPaths(src.id)).toEqual(['Projekte/a.md'])
    const a = (await storedFiles(src.id)).find((f) => f.path === 'Projekte/a.md')!
    expect(a.version).toBe(2)

    // nothing changed: nothing to do
    await startSync()
    expect(text(await screen.findByText(/Alles ist aktuell/))).toContain('unverändert')
    expect(screen.queryByText('Abgleich ausführen')).toBeNull()
  })

  it('aborts and deletes nothing when the folder on the computer is gone', async () => {
    const dir = await setUp('Weg')
    const src = (await sourceByLabel('Weg'))!
    await allowAndSync('Weg')
    await runSync()
    const before = await currentPaths(src.id)
    expect(before.length).toBe(2)
    rmSync(dir, { recursive: true })
    const button = screen.queryByText('Mit diesem Computer abgleichen')
    if (button) fireEvent.click(button)
    await waitFor(() => expect(text(document.body)).toContain('nicht erreichbar'))
    expect(await currentPaths(src.id)).toEqual(before)
  })

  it('replaces the folder by a link: not reachable either, nothing is deleted', async () => {
    const dir = await setUp('Link')
    const src = (await sourceByLabel('Link'))!
    await allowAndSync('Link')
    await runSync()
    const before = await currentPaths(src.id)
    const other = makeDir({ 'x.md': 'x' })
    rmSync(dir, { recursive: true })
    symlinkSync(other, dir)
    // either the window already noticed and took the sync button away, or the scan refuses
    const button = screen.queryByText('Mit diesem Computer abgleichen')
    if (button) fireEvent.click(button)
    await waitFor(() => expect(text(document.body)).toContain('nicht erreichbar'))
    expect(await currentPaths(src.id)).toEqual(before)
  })

  it('asks for an explicit yes when the folder is read as empty but the server holds copies', async () => {
    const dir = await setUp('Leer')
    const src = (await sourceByLabel('Leer'))!
    await allowAndSync('Leer')
    await runSync()
    for (const p of ['Projekte/a.md', 'Projekte/Sub/b.md', 'Privat/c.md', 'root.md', 'Projekte/passwort.md']) rmSync(join(dir, p))
    await startSync()
    expect(text(screen.getByTestId('sync-summary'))).toContain('2 werden vom Server gelöscht')
    expect(text(document.body)).toContain('keine einzige Textdatei')
    const run = screen.getByText('Abgleich ausführen') as HTMLButtonElement
    expect(run.disabled).toBe(true)
    // cancelling leaves everything as it is
    fireEvent.click(screen.getByText('Abbrechen'))
    expect(await currentPaths(src.id)).toEqual(['Projekte/Sub/b.md', 'Projekte/a.md'].sort())
  })

  it('judges deletions by the plan, even while the list on screen is still showing old numbers', async () => {
    const dir = await setUp('Alt-Zahlen')
    const src = (await sourceByLabel('Alt-Zahlen'))!
    await allowAndSync('Alt-Zahlen')
    // from now on the list of sources answers slowly, so the window keeps showing "0 Dateien" for a while
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input).endsWith('/admin/sources') && (init?.method ?? 'GET') === 'GET') await new Promise((r) => setTimeout(r, 2500))
      return realFetch(input, init)
    })
    await runSync()
    for (const p of ['Projekte/a.md', 'Projekte/Sub/b.md', 'Privat/c.md', 'root.md', 'Projekte/passwort.md']) rmSync(join(dir, p))
    await startSync()
    expect(text(document.body)).toContain('keine einzige Textdatei')
    expect((screen.getByText('Abgleich ausführen') as HTMLButtonElement).disabled).toBe(true)
    spy.mockRestore()
    expect((await currentPaths(src.id)).length).toBe(2)
  })

  it('shares a folder that holds files directly in its main folder', async () => {
    const dir = await setUp('Aric', { '2026 Paper.md': 'one', 'MOC.md': 'two', 'passwort.md': 'secret name', 'Sub/c.md': 'three' })
    const src = (await sourceByLabel('Aric'))!
    // there is a way to share the main folder, and nothing is shared before it is used
    const root = await screen.findByTestId('root-rule')
    expect(text(root)).toContain('nicht freigegeben')
    fireEvent.click(within(root).getByText('Ganzen Ordner freigeben'))
    expect((await nextAsk()).title).toBe('Ganzen Ordner freigeben?')
    expect(text(await screen.findByTestId('root-rule'))).toContain('nicht freigegeben')
    await confirmLast()
    await waitFor(() => expect(text(screen.getByTestId('root-rule'))).toContain('erlaubt'))
    expect(text(await node('MOC.md'))).toContain('erlaubt')
    fireEvent.click(await screen.findByText('Mit diesem Computer abgleichen'))
    await screen.findByText('Vorschau des Abgleichs')
    await runSync()
    // the protected name is still never sent, and everything else arrives
    expect((await currentPaths(src.id)).sort()).toEqual(['2026 Paper.md', 'MOC.md', 'Sub/c.md'])
    expect(dir).toBeTruthy()
    // a block inside still wins, and it removes the copies at once
    await clickRow('Sub', 'Sperren')
    await waitFor(async () => expect((await currentPaths(src.id)).sort()).toEqual(['2026 Paper.md', 'MOC.md']))
    // blocking everything removes all copies
    fireEvent.click(within(screen.getByTestId('root-rule')).getByText('Alles sperren'))
    await waitFor(async () => expect(await currentPaths(src.id)).toEqual([]))
    await waitFor(() => expect(text(screen.getByTestId('root-rule'))).toContain('gesperrt'))
  })

  it('sends in several passes, removes only in the last one, and survives a failed pass', async () => {
    const files: Record<string, string> = { 'Doc/old.md': 'old' }
    const dir = await setUp('Passes', files)
    const src = (await sourceByLabel('Passes'))!
    await allowAndSync('Passes', 'Doc')
    await runSync()
    expect(await currentPaths(src.id)).toEqual(['Doc/old.md'])

    // 45 new files (two passes of at most 40) and the old one is gone
    rmSync(join(dir, 'Doc/old.md'))
    for (let i = 0; i < 45; i++) writeFileSync(join(dir, `Doc/f${String(i).padStart(2, '0')}.md`), `n${i}`)
    // the second pass fails
    fake.readCalls = 0
    fake.onRead = (call) => {
      if (call === 2) throw 'Die Datei konnte nicht gelesen werden.'
    }
    await startSync()
    expect(text(screen.getByTestId('sync-summary'))).toContain('2 Durchgängen')
    fireEvent.click(screen.getByRole('checkbox')) // the only copy would be removed
    fireEvent.click(await screen.findByText('Abgleich ausführen'))
    const err = await screen.findByTestId('sync-error', undefined, { timeout: 30000 })
    expect(text(err)).toContain('bisher wurde also nichts gelöscht')
    const mid = await currentPaths(src.id)
    expect(mid).toContain('Doc/old.md') // the removal waits for the last pass
    expect(mid.length).toBe(41) // old + the first pass of 40

    // try again: planned from scratch, completes, now the old file goes
    fake.onRead = null
    await startSync()
    if (screen.queryByRole('checkbox')) fireEvent.click(screen.getByRole('checkbox'))
    await runSync()
    const end = await currentPaths(src.id)
    expect(end.length).toBe(45)
    expect(end).not.toContain('Doc/old.md')
  })

  it('repeats a request whose answer was lost, and the server stores nothing twice', async () => {
    await setUp('Replay')
    const src = (await sourceByLabel('Replay'))!
    await allowAndSync('Replay')
    const real = realFetch
    let lost = 0
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      const res = await real(input, init)
      if (String(input).endsWith('/sync/apply') && lost === 0) {
        lost += 1
        throw new TypeError('network lost after the server answered')
      }
      return res
    })
    try {
      await runSync()
    } finally {
      spy.mockRestore()
    }
    expect(lost).toBe(1)
    expect(await currentPaths(src.id)).toEqual(['Projekte/Sub/b.md', 'Projekte/a.md'].sort())
    const audit = await api('GET', '/admin/audit?type=sources&limit=50')
    const synced = (audit.data.entries as { event: string; detail: string }[]).filter((e) => e.event === 'source_synced' && e.detail.includes(`source=${src.id}`))
    expect(synced.length).toBe(1)
  })

  it('refuses a file that changed between reading and sending', async () => {
    const dir = await setUp('Race')
    const src = (await sourceByLabel('Race'))!
    await allowAndSync('Race')
    writeFileSync(join(dir, 'Projekte/a.md'), 'changed after the plan')
    fireEvent.click(await screen.findByText('Abgleich ausführen'))
    const err = await screen.findByTestId('sync-error', undefined, { timeout: 20000 })
    expect(text(err)).toContain('geändert')
    expect(await currentPaths(src.id)).toEqual([])
  })

  it('a single file source takes its one file and can only be blocked', async () => {
    const dir = makeDir({ 'Plan.md': 'plan', 'Andere.md': 'x' })
    mountFiles()
    fake.nextPick = { path: join(dir, 'Plan.md'), kind: 'file' }
    fireEvent.click(await screen.findByText('Einzelne Datei hinzufügen'))
    fireEvent.change(await screen.findByDisplayValue(/.*/, { selector: 'input.ginput' }), { target: { value: 'Einzel' } })
    fireEvent.click(screen.getByText('Quelle anlegen'))
    await screen.findByText(/ist angelegt/)
    await openSource('Einzel')
    expect(within(await node('Plan.md')).queryByText('Freigeben')).toBeNull()
    fireEvent.click(await screen.findByText('Mit diesem Computer abgleichen'))
    await screen.findByText('Vorschau des Abgleichs')
    await runSync()
    const src = (await sourceByLabel('Einzel'))!
    expect(await currentPaths(src.id)).toEqual(['Plan.md'])
    await clickRow('Plan.md', 'Sperren')
    await waitFor(async () => expect(await currentPaths(src.id)).toEqual([]))
    await screen.findByText(/gesperrt/)
  })

  it('blocking a folder deletes its server copies at once and says so', async () => {
    await setUp('Sperre')
    const src = (await sourceByLabel('Sperre'))!
    await allowAndSync('Sperre')
    await runSync()
    expect((await currentPaths(src.id)).length).toBe(2)
    await clickRow('Projekte', 'Sperren')
    await screen.findByText(/2 Dateien auf dem Server gelöscht/)
    expect(await currentPaths(src.id)).toEqual([])
  })
})

describe('relinking', () => {
  it('shows a source without a local place and links a new place without touching the server', async () => {
    const created = await api('POST', '/admin/sources', { kind: 'upload', scope: 'folder', label: 'Ohne Ort' })
    expect(created.status).toBe(201)
    const dir = makeDir({ 'Projekte/a.md': 'a' })
    mountFiles()
    expect(await screen.findByText(/kein Ort für diese Quelle hinterlegt/)).toBeTruthy()
    fake.nextPick = { path: dir, kind: 'folder' }
    fireEvent.click(screen.getByText('Neu zuordnen'))
    await screen.findByText(/zeigt jetzt auf den neu gewählten Ort/)
    expect(fake.links.get(created.data.source.id)?.path).toBe(dir)
    await waitFor(() => expect(screen.queryByText('Neu zuordnen')).toBeNull())
  })

  it('shows a place that is not reachable any more and offers to link it again', async () => {
    const dir = makeDir({ 'Projekte/a.md': 'a' })
    mountFiles()
    await addFolderSource(dir, 'Verschwunden')
    rmSync(dir, { recursive: true })
    cleanupAndRemount()
    expect(await screen.findByText(/Nicht erreichbar:/)).toBeTruthy()
    expect(screen.getByText('Neu zuordnen')).toBeTruthy()
  })
})

function cleanupAndRemount() {
  cleanup()
  mountFiles()
}
