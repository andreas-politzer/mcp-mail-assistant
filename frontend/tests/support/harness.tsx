import { mkdtempSync, mkdirSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { dirname, join } from 'node:path'
import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeAll, expect, vi } from 'vitest'
import { admin } from '../../src/api/admin'
import type { ConfirmRequest } from '../../src/core/types'
import { FilesTile } from '../../src/tiles/FilesTile'
import { FakeTauri } from './fakeTauri'

export const realFetch = globalThis.fetch
export const PASSPHRASE = 'test passphrase for the front end'
export const fake = new FakeTauri()
;(globalThis as unknown as { __fake: FakeTauri }).__fake = fake


export const asks: ConfirmRequest[] = []
export const ask = (r: ConfirmRequest) => void asks.push(r)

export const api = (method: string, path: string, body?: unknown) => admin.call(method, path, body)

export function makeDir(files: Record<string, string>): string {
  const root = mkdtempSync(join(tmpdir(), 'ui-test-'))
  for (const [rel, text] of Object.entries(files)) {
    mkdirSync(dirname(join(root, rel)), { recursive: true })
    writeFileSync(join(root, rel), text)
  }
  return root
}

export function useSignedInServer() {
  beforeAll(async () => {
    const r = await admin.login(PASSPHRASE)
    if (r.status !== 200) throw new Error('login failed: ' + JSON.stringify(r.data))
  })
  afterEach(async () => {
    vi.restoreAllMocks()
    cleanup()
    asks.length = 0
    fake.nextPick = null
    fake.onRead = null
    fake.readCalls = 0
    fake.calls = []
    if (!admin.isSignedIn()) await admin.login(PASSPHRASE)
    const list = await api('GET', '/admin/sources')
    for (const s of list.data.sources as { id: string; kind: string }[]) {
      if (s.kind === 'upload') await api('POST', `/admin/sources/${s.id}/remove`, {})
    }
    fake.links.clear()
    fake.scans.clear()
  })
}

export const sourceByLabel = async (label: string) => {
  const list = await api('GET', '/admin/sources')
  return (list.data.sources as { id: string; label: string; files: number }[]).find((s) => s.label === label)
}

export const storedFiles = async (id: string) => {
  const r = await api('GET', `/admin/sources/${id}/files`)
  return r.data.files as { path: string; state: string; version: number; allowed_now: boolean }[]
}
export const currentPaths = async (id: string) => (await storedFiles(id)).filter((f) => f.state === 'current').map((f) => f.path).sort()

export function mountFiles() {
  return render(<FilesTile ask={ask} />)
}

/** Adds a folder source through the window and waits until it is listed. */
export async function addFolderSource(path: string, label: string) {
  fake.nextPick = { path, kind: 'folder' }
  fireEvent.click(await screen.findByText('Ordner hinzufügen'))
  const input = await screen.findByDisplayValue(/.*/, { selector: 'input.ginput' })
  fireEvent.change(input, { target: { value: label } })
  fireEvent.click(screen.getByText('Quelle anlegen'))
  await screen.findByText(new RegExp(`Die Quelle „${label}“ ist angelegt`))
}

export async function openSource(label: string) {
  const name = await screen.findByText((_, el) => el?.tagName === 'SPAN' && (el.textContent ?? '').includes(label) && !!el.className.includes('font-semibold'))
  const card = name.closest('div.py-2') as HTMLElement
  fireEvent.click(within(card).getByText('Öffnen'))
  await screen.findByText('← Alle Quellen')
}

export const node = (path: string) => screen.findByTestId(`node-${path}`)

/** Runs the confirmation the window asked for (what the person does by clicking the button). */
export async function nextAsk() {
  await waitFor(() => expect(asks.length).toBeGreaterThan(0), { timeout: 5000 })
  return asks[asks.length - 1]
}

export async function confirmLast() {
  await nextAsk()
  const req = asks.pop()!
  await act(async () => {
    req.run()
    await new Promise((r) => setTimeout(r, 300))
  })
  return req
}

export async function clickRow(path: string, button: string) {
  const row = await node(path)
  fireEvent.click(within(row).getByText(button))
}

