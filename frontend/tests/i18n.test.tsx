import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join } from 'node:path'
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it } from 'vitest'
import { EN } from '../src/i18n/en'
import { getLang, isChosen, setChosen, setLang, t } from '../src/i18n'
import App from '../src/App'
import { Taskbar } from '../src/components/Taskbar'
import { Wizard } from '../src/wizard/Wizard'
import { HINTS } from '../src/wizard/hints'
import { PRESETS, unsupportedReason } from '../src/wizard/presets'
import { mockBackend } from '../src/wizard/setupApi'

afterEach(cleanup)

// Every file under src (except the i18n folder itself).
function sources(dir: string, out: string[] = []): string[] {
  for (const name of readdirSync(dir)) {
    const p = join(dir, name)
    if (statSync(p).isDirectory()) {
      if (name !== 'i18n') sources(p, out)
    } else if (/\.(ts|tsx)$/.test(name)) out.push(p)
  }
  return out
}

// All the literals that are passed to t('...') in the source.
function literals(): { file: string; text: string }[] {
  const found: { file: string; text: string }[] = []
  const re = /\bt\(\s*'((?:[^'\\]|\\.)*)'/g
  for (const file of sources(join(__dirname, '..', 'src'))) {
    const code = readFileSync(file, 'utf8')
    for (const m of code.matchAll(re)) found.push({ file, text: m[1].replace(/\\'/g, "'").replace(/\\\\/g, '\\') })
  }
  return found
}

const placeholders = (s: string) => [...s.matchAll(/\{(\w+)\}/g)].map((m) => m[1]).sort().join(',')

describe('translations', () => {
  it('has an English text for every t() in the source', () => {
    const missing = literals().filter((l) => !(l.text in EN)).map((l) => `${l.file.split('src/')[1]}: ${l.text.slice(0, 70)}`)
    expect(missing).toEqual([])
  })
  it('has no English text that nothing uses', () => {
    // A text counts as used when it stands in the source as written: in t('...'), or as data (labels, hints) that is translated when shown.
    const all = sources(join(__dirname, '..', 'src')).map((f) => readFileSync(f, 'utf8')).join('\n')
    // Messages that the Rust side and the Python program send in German also count as users of their English text.
    const outside = ['src-tauri/src', '../src/outbox_mcp'].flatMap((d) => readdirSync(join(__dirname, '..', d)).filter((f) => /\.(rs|py)$/.test(f)).map((f) => readFileSync(join(__dirname, '..', d, f), 'utf8'))).join('\n')
    const composed = new Set<string>(PRESETS.map((p) => p.passwordNote)) // built from pieces in presets.ts
    const hay = all + outside.replace(/f?"\s*\n\s*f?"/g, '') // adjacent string pieces in Python count as one text
    const standsIn = (k: string) => hay.includes(k) || hay.includes(k.replace(/'/g, "\\'")) || hay.includes(k.replace(/\n/g, '\\n'))
    // A text with pieces filled in at run time counts when each fixed piece of it stands in the source.
    const piecesStandIn = (k: string) => /\{\w+\}/.test(k) && k.split(/\{\w+\}| ?Details: /).filter((x) => x.trim()).every((x) => hay.includes(x))
    const unused = Object.keys(EN).filter((k) => !composed.has(k) && !standsIn(k) && !piecesStandIn(k))
    expect(unused).toEqual([])
  })
  it('keeps the placeholders of every text', () => {
    for (const [de, en] of Object.entries(EN)) expect(placeholders(en), de).toBe(placeholders(de))
  })
  it('has English for the data that is translated when shown (hints, providers, warnings)', () => {
    for (const lines of Object.values(HINTS)) for (const l of lines) expect(EN[l], l).toBeTruthy()
    for (const p of PRESETS) {
      expect(EN[p.label] ?? p.label, p.label).toBeTruthy()
      expect(EN[p.passwordNote], p.id).toBeTruthy()
    }
    for (const mail of ['a@outlook.com', 'a@proton.me']) expect(EN[unsupportedReason(mail)!]).toBeTruthy()
  })
  it('does not leave German behind in an English text', () => {
    const german = /\b(und|nicht|ist|der|die|das|dein|deine|deinen|du|ein|eine|mit|auf|wird)\b|[äöüß]/
    const bad = Object.entries(EN).filter(([, en]) => german.test(en)).map(([de, en]) => `${de.slice(0, 40)} -> ${en.slice(0, 70)}`)
    expect(bad).toEqual([])
  })
})

describe('t()', () => {
  it('gives German as written and fills placeholders', () => {
    setLang('de', false)
    expect(t('Schritt {n} von {total}', { n: 2, total: 7 })).toBe('Schritt 2 von 7')
  })
  it('gives English, and German again for a text without an English entry', () => {
    setLang('en', false)
    expect(t('Schritt {n} von {total}', { n: 2, total: 7 })).toBe('Step 2 of 7')
    expect(t('Ein Text, den es nicht gibt')).toBe('Ein Text, den es nicht gibt')
  })
})

describe('the language switch', () => {
  it('switches the wizard to English and back without losing what was typed', async () => {
    const backend = mockBackend(0)
    backend.remoteStatus = async () => ({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
    render(<Wizard backend={backend} onDone={() => {}} />)
    expect(await screen.findByText('Wo willst du Mail Assistant benutzen?')).toBeTruthy()
    fireEvent.click(screen.getByText('Weiter'))
    fireEvent.change(await screen.findByLabelText('E-Mail-Adresse:'), { target: { value: 'ich@gmail.com' } })
    fireEvent.click(screen.getByRole('button', { name: 'EN' }))
    expect(getLang()).toBe('en')
    expect(await screen.findByLabelText('Email address:')).toHaveProperty('value', 'ich@gmail.com')
    expect(screen.getByText('Which address should the mails come from?')).toBeTruthy()
    expect(screen.getByText('Continue')).toBeTruthy()
    expect(screen.getByText('Step 2 of 7')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'DE' }))
    expect(await screen.findByLabelText('E-Mail-Adresse:')).toHaveProperty('value', 'ich@gmail.com')
  })
  it('walks through the whole wizard in English', async () => {
    setLang('en', false)
    const backend = mockBackend(0)
    backend.remoteStatus = async () => ({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
    render(<Wizard backend={backend} onDone={() => {}} />)
    const go = () => fireEvent.click(screen.getByText('Continue'))
    expect(await screen.findByText('Where do you want to use Mail Assistant?')).toBeTruthy()
    go()
    fireEvent.change(await screen.findByLabelText('Email address:'), { target: { value: 'ich@gmail.com' } })
    go()
    fireEvent.change(await screen.findByLabelText('Password:'), { target: { value: 'geheim' } })
    fireEvent.click(screen.getByText('Save and check'))
    await waitFor(() => expect(screen.getByText(/Password saved in the keychain/)).toBeTruthy())
    go()
    expect(await screen.findByText('Test mail to yourself')).toBeTruthy()
    fireEvent.click(screen.getByText('Send test mail'))
    await waitFor(() => expect(screen.getByText(/Test mail to ich@gmail.com is on its way/)).toBeTruthy())
    go()
    await waitFor(() => expect((screen.getByText('Connect now') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(screen.getByText('Connect now'))
    await waitFor(() => expect(screen.getByText(/^Connected\./)).toBeTruthy())
    go()
    expect(await screen.findByText('Approval for mails to other people (passkey)')).toBeTruthy()
  })
})

describe('messages from the Rust side and the Python program', () => {
  it('are translated, also with a piece filled in', () => {
    setLang('en', false)
    expect(t('Die Datei ist zu groß.')).toBe('The file is too large.')
    expect(t("'a.md' ist nicht lesbar (Permission denied). Der Abgleich wurde abgebrochen.")).toBe("'a.md' cannot be read (Permission denied). The synchronisation was cancelled.")
    expect(t("'x.md': Die Datei ist zu groß. Der Abgleich wurde abgebrochen.")).toBe("'x.md': The file is too large. The synchronisation was cancelled.")
    expect(t('Der Anbieter meldet einen Fehler: 550 no')).toBe('The provider reports an error: 550 no')
  })
  it('stay German in German', () => {
    setLang('de', false)
    expect(t('Die Datei ist zu groß.')).toBe('Die Datei ist zu groß.')
  })
})


describe('first-start language question', () => {
  it('comes before everything else and sets the language for the whole app', () => {
    localStorage.clear()
    setChosen(false)
    render(<App />)
    expect(screen.getByTestId('language-picker')).toBeTruthy()
    fireEvent.click(screen.getByRole('button', { name: 'English' }))
    expect(screen.queryByTestId('language-picker')).toBeNull()
    expect(getLang()).toBe('en')
    expect(isChosen()).toBe(true)
    expect(localStorage.getItem('mail-assistant-lang')).toBe('en')
  })
  it('lets the person choose German on an English system too', () => {
    localStorage.clear()
    setLang('en', false)
    setChosen(false)
    render(<App />)
    fireEvent.click(screen.getByRole('button', { name: 'Deutsch' }))
    expect(getLang()).toBe('de')
  })
  it('leaves a switch next to the clock in the task bar', () => {
    render(<Taskbar items={[]} onToggle={() => {}} onCommand={() => {}} />)
    fireEvent.click(screen.getByRole('button', { name: 'EN' }))
    expect(getLang()).toBe('en')
  })
})
