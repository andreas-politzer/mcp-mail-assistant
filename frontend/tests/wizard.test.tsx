import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Wizard, knownPasskeys } from '../src/wizard/Wizard'
import { guessPreset, looksLikeAddress, PRESETS } from '../src/wizard/presets'
import { mockBackend, type SetupBackend } from '../src/wizard/setupApi'
import { HINTS, STEPS, STEPS_PHONE } from '../src/wizard/hints'

afterEach(cleanup)

const next = () => fireEvent.click(screen.getByText('Weiter'))
const type = (label: string, value: string) => fireEvent.change(screen.getByLabelText(label), { target: { value } })

describe('presets', () => {
  it('guesses the provider from the address', () => {
    expect(guessPreset('a@gmail.com')).toBe('gmail')
    expect(guessPreset('a@web.de')).toBe('webde')
    expect(guessPreset('a@example.org')).toBeNull()
    expect(guessPreset('a@gmx.de')).toBe('gmx')
    expect(guessPreset('a@icloud.com')).toBe('icloud')
    expect(guessPreset('a@t-online.de')).toBe('tonline')
    expect(guessPreset('a@mailbox.org')).toBe('mailboxorg')
    expect(guessPreset('a@ik.me')).toBe('infomaniak')
    expect(looksLikeAddress('a@b.de')).toBe(true)
    expect(looksLikeAddress('a@b')).toBe(false)
  })
  it('has a note and valid ports for every provider', () => {
    for (const p of PRESETS) {
      expect(p.passwordNote.length).toBeGreaterThan(20)
      expect([465, 587]).toContain(p.port)
    }
  })
  it('has at least one Funki line per step', () => {
    for (const s of [...STEPS, ...STEPS_PHONE]) expect(HINTS[s].length).toBeGreaterThan(0)
  })
})

describe('the wizard (desktop path)', () => {
  it('goes through all steps, clears the password and ends at the administration', async () => {
    const backend = mockBackend(0)
    backend.remoteStatus = async () => ({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
    const save = vi.spyOn(backend, 'saveMailbox')
    const onDone = vi.fn()
    render(<Wizard backend={backend} onDone={onDone} />)

    expect(screen.getByText(/Schritt 1 von 7/)).toBeTruthy()
    next()
    type('E-Mail-Adresse:', 'ich@gmail.com')
    expect((screen.getByLabelText('Anbieter:') as HTMLSelectElement).value).toBe('gmail')
    expect((screen.getByLabelText('Server:') as HTMLInputElement).value).toBe('smtp.gmail.com')
    next()

    type('Passwort:', 'geheim')
    fireEvent.click(screen.getByText('Speichern und prüfen'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Schlüsselbund/))
    expect(save).toHaveBeenCalledWith({ email: 'ich@gmail.com', host: 'smtp.gmail.com', port: 587, security: 'starttls' }, 'geheim')
    expect((screen.getByLabelText('Passwort:') as HTMLInputElement).value).toBe('')
    next()

    expect((screen.getByLabelText('Empfänger:') as HTMLInputElement).value).toBe('ich@gmail.com')
    fireEvent.click(screen.getByText('Testmail senden'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/ist raus/))
    next()

    await waitFor(() => expect((screen.getByText('Jetzt verbinden') as HTMLButtonElement).disabled).toBe(false))
    fireEvent.click(screen.getByText('Jetzt verbinden'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Verbunden/))
    next()

    fireEvent.click(screen.getByText('Passkey einrichten'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/im Browser geöffnet/))
    fireEvent.click(screen.getByText('Prüfen'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Passkey ist registriert/))
    next()

    expect(screen.getByText('Fertig eingerichtet')).toBeTruthy()
    expect(screen.getByText(/Passkey eingerichtet/)).toBeTruthy()
    fireEvent.click(screen.getByText('Zur Verwaltung'))
    expect(onDone).toHaveBeenCalled()
  })

  it('shows and hides the password with the eye, and hides it again on every new visit', () => {
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    next()
    type('E-Mail-Adresse:', 'ich@gmail.com')
    next()
    const field = screen.getByLabelText('Passwort:') as HTMLInputElement
    expect(field.type).toBe('password')
    fireEvent.click(screen.getByLabelText('Passwort anzeigen'))
    expect(field.type).toBe('text')
    fireEvent.click(screen.getByLabelText('Passwort verbergen'))
    expect(field.type).toBe('password')
  })

  it('explains Outlook and Proton addresses and does not let them through', () => {
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    next()
    type('E-Mail-Adresse:', 'ich@outlook.com')
    expect(screen.getByRole('alert').textContent).toMatch(/OAuth2/)
    fireEvent.change(screen.getByLabelText('Anbieter:'), { target: { value: 'other' } })
    type('Server:', 'smtp.example.org')
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    type('E-Mail-Adresse:', 'ich@proton.me')
    expect(screen.getByRole('alert').textContent).toMatch(/Bridge/)
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    type('E-Mail-Adresse:', 'ich@example.org')
    expect(screen.queryByRole('alert')).toBeNull()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
  })

  it('lets the person do the passkey later, and says so on the last page', async () => {
    const backend = mockBackend(0)
    backend.remoteStatus = async () => ({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
    backend.status = async () => ({ mailbox: { email: 'a@gmail.com', host: 'smtp.gmail.com', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: 0 })
    render(<Wizard backend={backend} onDone={() => {}} />)
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('a@gmail.com'))
    next()
    await waitFor(() => screen.getByText(/schon im Schlüsselbund/))
    next()
    fireEvent.click(screen.getByText('Testmail senden'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/ist raus/))
    next()
    await waitFor(() => screen.getByText(/schon verbunden/))
    next()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByText(/Passkey (später einrichten|am Handy überspringen)/))
    expect(screen.getByText(/Passkey noch nicht eingerichtet/)).toBeTruthy()
  })

  it('lets the person skip the test mail and says on the last page where to catch up', async () => {
    const backend = mockBackend(0)
    backend.remoteStatus = async () => ({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
    backend.status = async () => ({ mailbox: { email: 'a@gmail.com', host: 'smtp.gmail.com', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: 1 })
    const send = vi.spyOn(backend, 'sendTestMail')
    render(<Wizard backend={backend} onDone={() => {}} />)
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('a@gmail.com'))
    next()
    await waitFor(() => screen.getByText(/schon im Schlüsselbund/))
    next()
    fireEvent.click(screen.getByText('Überspringen'))
    expect(send).not.toHaveBeenCalled()
    expect(screen.getByText('Claude Desktop verbinden')).toBeTruthy()
    await waitFor(() => screen.getByText(/schon verbunden/))
    next()
    next()
    expect(screen.getByText('Fertig eingerichtet')).toBeTruthy()
    expect(screen.getByText(/Testmail übersprungen/).textContent).toMatch(/Einrichtungs-Wizard/)
    expect(screen.queryByText(/Testmail ist angekommen/)).toBeNull()
  })

  it('does not say the test mail was skipped when it was sent after all', async () => {
    const backend = mockBackend(0)
    backend.remoteStatus = async () => ({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
    backend.status = async () => ({ mailbox: { email: 'a@gmail.com', host: 'smtp.gmail.com', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: 1 })
    render(<Wizard backend={backend} onDone={() => {}} />)
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('a@gmail.com'))
    next()
    await waitFor(() => screen.getByText(/schon im Schlüsselbund/))
    next()
    fireEvent.click(screen.getByText('Überspringen'))
    fireEvent.click(screen.getByText('Zurück'))
    fireEvent.click(screen.getByText('Testmail senden'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/ist raus/))
    next()
    await waitFor(() => screen.getByText(/schon verbunden/))
    next()
    next()
    expect(screen.getByText(/Testmail ist angekommen/)).toBeTruthy()
    expect(screen.queryByText(/Testmail übersprungen/)).toBeNull()
  })

  const toClaudeStep = async (backend: SetupBackend) => {
    render(<Wizard backend={backend} onDone={() => {}} />)
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('a@gmail.com'))
    next()
    await waitFor(() => screen.getByText(/schon im Schlüsselbund/))
    next()
    fireEvent.click(screen.getByText('Überspringen'))
  }
  const known = (connected: boolean) => async () => ({
    mailbox: { email: 'a@gmail.com', host: 'smtp.gmail.com', port: 587, security: 'starttls' as const },
    password_stored: true,
    claude_connected: connected,
    passkeys: 1,
  })

  it('offers to remove the connection to Claude Desktop only when it exists, and says what stays', async () => {
    const backend = mockBackend(0)
    backend.status = known(true)
    const remove = vi.spyOn(backend, 'disconnectClaude')
    await toClaudeStep(backend)
    await waitFor(() => screen.getByText(/schon verbunden/))
    fireEvent.click(screen.getByText('Aus Claude Desktop entfernen'))
    expect(remove).toHaveBeenCalledOnce()
    await waitFor(() => expect(screen.getByText(/Empfänger und Einstellungen bleiben erhalten/)).toBeTruthy())
    expect(screen.getByText(/Cmd\+Q/)).toBeTruthy()
    expect((screen.getByText('Jetzt verbinden') as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByText('Aus Claude Desktop entfernen')).toBeNull()
  })

  it('does not offer to remove a connection that is not there', async () => {
    const backend = mockBackend(0)
    backend.status = known(false)
    await toClaudeStep(backend)
    await waitFor(() => expect((screen.getByText('Jetzt verbinden') as HTMLButtonElement).disabled).toBe(false))
    expect(screen.queryByText('Aus Claude Desktop entfernen')).toBeNull()
  })

  it('shows why connecting is blocked when the app runs from a temporary place, and does not let it connect', async () => {
    const backend = mockBackend(0)
    backend.status = known(false)
    backend.previewClaudeConnection = async () => ({ ok: false, reason: 'Mail Assistant läuft gerade aus einem vorläufigen Ordner. Ziehe die App in den Ordner „Programme“.' })
    const connect = vi.spyOn(backend, 'connectClaude')
    await toClaudeStep(backend)
    await waitFor(() => expect(screen.getByText(/Ordner „Programme“/)).toBeTruthy())
    const button = screen.getByText('Jetzt verbinden') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.click(button)
    expect(connect).not.toHaveBeenCalled()
  })

  it('shows a failure to remove in plain words', async () => {
    const backend = mockBackend(0)
    backend.status = known(true)
    backend.disconnectClaude = async () => ({ ok: false, reason: 'Die Konfigurationsdatei von Claude Desktop lässt sich nicht schreiben.' })
    await toClaudeStep(backend)
    await waitFor(() => screen.getByText(/schon verbunden/))
    fireEvent.click(screen.getByText('Aus Claude Desktop entfernen'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/nicht schreiben/))
  })

  it('no longer announces a device-password dialog on the password step', () => {
    expect(HINTS.password.join(' ')).not.toMatch(/Immer erlauben|Anmelde-Passwort/)
  })

  it('shows the reason when the approval page is not running yet', async () => {
    const backend = mockBackend(0)
    backend.status = async () => ({ mailbox: { email: 'a@gmail.com', host: 'smtp.gmail.com', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: 0 })
    backend.approvalLink = async () => ({ ok: false, reason: 'Die Freigabeseite läuft noch nicht.' })
    render(<Wizard backend={backend} onDone={() => {}} />)
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('a@gmail.com'))
    next()
    await waitFor(() => screen.getByText(/schon im Schlüsselbund/))
    next()
    fireEvent.click(screen.getByText('Testmail senden'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/ist raus/))
    next()
    await waitFor(() => screen.getByText(/schon verbunden/))
    next()
    fireEvent.click(screen.getByText('Passkey einrichten'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/läuft noch nicht/))
  })

  it('stays on the password step after a rejected sign-in and shows the reason', async () => {
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    next()
    type('E-Mail-Adresse:', 'ich@web.de')
    next()
    type('Passwort:', 'falsch')
    fireEvent.click(screen.getByText('Speichern und prüfen'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/abgelehnt/))
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
  })

  it('lets the phone path through to the longer list of steps', () => {
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    fireEvent.click(screen.getByLabelText(/Auch vom Handy aus/))
    expect(screen.getByText(/Dafür brauchst du ein Tailscale-Konto\. Den Tunnel richtest du erst in Schritt 7 ein\./)).toBeTruthy()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText(/Schritt 1 von 11/)).toBeTruthy()
  })

  it('offers the access ways for the phone: Tailscale now, ngrok and Cloudflare marked as coming', () => {
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    expect(screen.queryByText('Zugangsweg fürs Handy')).toBeNull()
    expect(screen.queryByText(/Tailscale/)).toBeNull()
    fireEvent.click(screen.getByLabelText(/Auch vom Handy aus/))
    expect(screen.getByText('Zugangsweg fürs Handy')).toBeTruthy()
    expect((screen.getByLabelText(/Tailscale Funnel/) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByLabelText(/ngrok/) as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText(/Cloudflare Tunnel/) as HTMLInputElement).disabled).toBe(true)
    expect(screen.getAllByText(/kommt bald/).length).toBe(2)
  })

  it('needs the server and port by hand only for another provider', () => {
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    next()
    type('E-Mail-Adresse:', 'ich@example.org')
    fireEvent.change(screen.getByLabelText('Anbieter:'), { target: { value: 'other' } })
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    type('Server:', 'smtp.example.org')
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
  })

  it('keeps Funki on screen with the hint of each step, without a tip button', () => {
    const backend: SetupBackend = mockBackend(0)
    render(<Wizard backend={backend} onDone={() => {}} />)
    expect(document.querySelector('[data-stack="funki"]')).not.toBeNull()
    expect(screen.getByText(HINTS.where[0])).toBeTruthy()
    expect(screen.queryByText('Noch ein Tipp')).toBeNull()
  })
})

describe('the wizard looks at the real state', () => {
  it('prefills what is already set up and lets the password step pass', async () => {
    const backend = mockBackend(0)
    backend.status = async () => ({ mailbox: { email: 'ich@web.de', host: 'smtp.web.de', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: 0 })
    render(<Wizard backend={backend} onDone={() => {}} />)
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('ich@web.de'))
    expect((screen.getByLabelText('Anbieter:') as HTMLSelectElement).value).toBe('webde')
    next()
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/schon im Schlüsselbund/))
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
  })

  it('offers "Zurück zur Verwaltung" in every step but the last, and only when the app offers it', () => {
    const { unmount } = render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    expect(screen.queryByText('Zurück zur Verwaltung')).toBeNull()
    unmount()
    const skip = vi.fn()
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} onSkip={skip} />)
    next() // a later step than the first
    fireEvent.click(screen.getByText('Zurück zur Verwaltung'))
    expect(skip).toHaveBeenCalled()
  })

  it('shows why the Claude Desktop preview failed and does not offer the change', async () => {
    const backend = mockBackend(0)
    backend.previewClaudeConnection = async () => ({ ok: false, reason: 'Die Konfigurationsdatei von Claude Desktop ist nicht lesbar. Ich ändere sie nicht.' })
    backend.status = async () => ({ mailbox: { email: 'a@gmail.com', host: 'smtp.gmail.com', port: 587, security: 'starttls' }, password_stored: true, claude_connected: false, passkeys: 0 })
    render(<Wizard backend={backend} onDone={() => {}} />)
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('a@gmail.com'))
    next()
    await waitFor(() => screen.getByText(/schon im Schlüsselbund/))
    next()
    fireEvent.click(screen.getByText('Testmail senden'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/ist raus/))
    next()
    await waitFor(() => expect(screen.getByText(/nicht lesbar/)).toBeTruthy())
    expect((screen.getByText('Jetzt verbinden') as HTMLButtonElement).disabled).toBe(true)
  })
})

describe('Zurück button', () => {
  it('is not shown on the first step', () => {
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    expect(screen.queryByText('Zurück')).toBeNull()
  })
})

describe('the tunnel step (phone path)', () => {
  const ADDRESS = 'https://mein-mac.example.ts.net'

  // A computer that is set up completely, so the steps before the tunnel can be passed quickly.
  // The computer already has one passkey. A phone passkey appears (the count goes up) once a phone link was made,
  // unless a test says the person did not finish on the phone.
  let phoneKeyAppears = true
  afterEach(() => {
    phoneKeyAppears = true
  })

  let startKeys = 1 // how many passkeys the demo computer already has
  function ready(backend: SetupBackend) {
    let keys = startKeys
    const makeLink = backend.approvalLink.bind(backend)
    backend.approvalLink = async (phone) => {
      const r = await makeLink(phone)
      if (phone && r.ok && phoneKeyAppears) keys += 1
      return r
    }
    backend.status = async () => ({ mailbox: { email: 'ich@web.de', host: 'smtp.web.de', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: keys })
    return backend
  }

  async function toTunnel(backend: SetupBackend) {
    render(<Wizard backend={ready(backend)} onDone={() => {}} />)
    fireEvent.click(screen.getByLabelText(/Auch vom Handy aus/))
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('ich@web.de'))
    next()
    await waitFor(() => screen.getByText(/schon im Schlüsselbund/))
    next()
    fireEvent.click(screen.getByText('Überspringen'))
    await waitFor(() => screen.getByText(/schon verbunden/))
    next()
    await waitFor(() => screen.getByText(/Bereits eingerichtet/))
    next()
    await waitFor(() => screen.getByText('Tunnel für das Handy (Tailscale)'))
  }

  it('shows the address first, needs the confirmation, then switches the tunnel on', async () => {
    const backend = mockBackend(0)
    const enable = vi.spyOn(backend, 'tunnelEnable')
    await toTunnel(backend)
    expect(screen.getByText(/Schritt 7 von 11/)).toBeTruthy()
    await waitFor(() => expect(screen.getByLabelText('Öffentliche Adresse').textContent).toBe(ADDRESS))
    const button = screen.getByText('Tunnel einschalten') as HTMLButtonElement
    expect(button.disabled).toBe(true)
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByLabelText(/darf öffentlich sichtbar sein/))
    expect(button.disabled).toBe(false)
    fireEvent.click(button)
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Der Tunnel steht: https:\/\/mein-mac\.example\.ts\.net/))
    expect(enable).toHaveBeenCalledWith(ADDRESS)
    expect(screen.getByRole('status').textContent).toMatch(/Passphrase/)
    expect(screen.getByRole('status').textContent).toMatch(/von diesem Computer aus/)
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
    next()
    expect(screen.getByText('Anmeldung vom Handy')).toBeTruthy()
  })

  const PASS = 'ABCDE-FGHJK-MNPQR-STUVW-XYZ23'

  async function toPhoneLogin(backend: SetupBackend) {
    await toTunnel(backend)
    await waitFor(() => screen.getByLabelText('Öffentliche Adresse'))
    fireEvent.click(screen.getByLabelText(/darf öffentlich sichtbar sein/))
    fireEvent.click(screen.getByText('Tunnel einschalten'))
    await waitFor(() => screen.getByText(/Der Tunnel steht/))
    next()
    await waitFor(() => screen.getByText('Anmeldung vom Handy'))
  }

  function clipboard() {
    const writeText = vi.fn().mockResolvedValue(undefined)
    Object.defineProperty(navigator, 'clipboard', { value: { writeText }, configurable: true })
    return writeText
  }

  it('shows the new passphrase once, copies only on a click and needs the "saved" tick before it goes on', async () => {
    const writeText = clipboard()
    const backend = mockBackend(0)
    const create = vi.spyOn(backend, 'newPassphrase')
    await toPhoneLogin(backend)
    expect(screen.getByText(/Schritt 8 von 11/)).toBeTruthy()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.queryByLabelText('Passphrase')).toBeNull()
    fireEvent.click(screen.getByText('Passphrase erzeugen'))
    await waitFor(() => expect(screen.getByLabelText('Passphrase').textContent).toBe(PASS))
    expect(create).toHaveBeenCalledTimes(1)
    expect(writeText).not.toHaveBeenCalled() // never on its own
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getAllByText('Kopieren')[0])
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(PASS))
    await waitFor(() => expect(screen.getAllByText('Kopiert').length).toBe(1))
    fireEvent.click(screen.getByLabelText(/Passphrase gespeichert/))
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
  })

  it('tells how to connect Claude on its own step: the settings page, the address with /mcp, the passphrase on sign-in', async () => {
    const writeText = clipboard()
    const backend = mockBackend(0)
    const open = vi.spyOn(backend, 'openLink')
    await toPhoneLogin(backend)
    expect(screen.queryByLabelText('Adresse für den Connector')).toBeNull() // the passphrase page has nothing of it
    expect(screen.queryByText('Einstellungen öffnen')).toBeNull()
    fireEvent.click(screen.getByText('Passphrase erzeugen'))
    await waitFor(() => screen.getByLabelText('Passphrase'))
    expect(screen.queryByLabelText('Adresse für den Connector')).toBeNull()
    expect(screen.getByText(/ersetze ihn, statt einen zweiten anzulegen/)).toBeTruthy()
    fireEvent.click(screen.getByLabelText(/Passphrase gespeichert/))
    next()
    await waitFor(() => screen.getByLabelText('Adresse für den Connector'))
    expect(screen.getByText(/Schritt 9 von 11/)).toBeTruthy()
    expect(screen.queryByLabelText('Passphrase')).toBeNull() // shown once, not here
    expect(screen.getByLabelText('Adresse für den Connector').textContent).toBe(`${ADDRESS}/mcp`)
    fireEvent.click(screen.getByText('Einstellungen öffnen'))
    expect(open).toHaveBeenCalledWith('https://claude.ai/settings/connectors')
    fireEvent.click(screen.getByText('Kopieren'))
    await waitFor(() => expect(writeText).toHaveBeenCalledWith(`${ADDRESS}/mcp`))
    expect(screen.getByText(/nach der Passphrase/)).toBeTruthy()
    expect(screen.getByText(/Als Name zum Beispiel „Mail Mac“/)).toBeTruthy()
    expect(screen.getByLabelText('Hinweis zu mehreren Verbindungen').textContent).toMatch(/Nur eine Verbindung pro Chat/)
    expect(screen.getByText(/Passphrase nicht gespeichert\? Mit „Zurück“ kannst du eine neue erzeugen\./)).toBeTruthy()
    expect(screen.getByText(/Benutzerdefinierten Connector hinzufügen/)).toBeTruthy()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false) // nothing to verify here, no tick
    fireEvent.click(screen.getByText('Zurück')) // a new one can be made on the step before
    await waitFor(() => screen.getByText('Anmeldung vom Handy'))
    expect(screen.getByText('Passphrase erzeugen')).toBeTruthy()
  })

  it('forgets the passphrase when the step is left', async () => {
    const backend = mockBackend(0)
    await toPhoneLogin(backend)
    fireEvent.click(screen.getByText('Passphrase erzeugen'))
    await waitFor(() => screen.getByLabelText('Passphrase'))
    fireEvent.click(screen.getByLabelText(/Passphrase gespeichert/))
    next()
    await waitFor(() => screen.getByText('Claude mit deinem Computer verbinden'))
    fireEvent.click(screen.getByText('Zurück'))
    await waitFor(() => screen.getByText('Anmeldung vom Handy'))
    expect(screen.queryByText(PASS)).toBeNull()
    expect(screen.queryByLabelText('Passphrase')).toBeNull()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
  })

  it('warns before replacing a real passphrase, and lets the person go on without a new one', async () => {
    const backend = mockBackend(0)
    backend.remoteStatus = async () => ({ enabled: true, traces: true, address: ADDRESS, passphrase_placeholder: false })
    await toPhoneLogin(backend)
    await waitFor(() => screen.getByText(/Es gibt schon eine gültige Passphrase/))
    expect(screen.getByText(/Du musst nichts tun und kannst weitergehen/)).toBeTruthy()
    expect(screen.getByText(/Die alte wird dann ungültig/)).toBeTruthy()
    expect(screen.getByText('Neue Passphrase erzeugen')).toBeTruthy()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
    expect(screen.queryByLabelText('Adresse für den Connector')).toBeNull()
    next()
    await waitFor(() => screen.getByLabelText('Adresse für den Connector')) // the existing passphrase goes on to the connector step
  })

  it('shows the reason when no passphrase could be made, and when copying is refused', async () => {
    const backend = mockBackend(0)
    backend.newPassphrase = async () => ({ ok: false, reason: 'Der Tunnel ist noch nicht eingerichtet. Schalte ihn zuerst ein.' })
    await toPhoneLogin(backend)
    fireEvent.click(screen.getByText('Passphrase erzeugen'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/noch nicht eingerichtet/))
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    cleanup()

    Object.defineProperty(navigator, 'clipboard', { value: { writeText: vi.fn().mockRejectedValue(new Error('no')) }, configurable: true })
    await toPhoneLogin(mockBackend(0))
    fireEvent.click(screen.getByText('Passphrase erzeugen'))
    await waitFor(() => screen.getByLabelText('Passphrase'))
    fireEvent.click(screen.getAllByText('Kopieren')[0])
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/Kopieren hat nicht geklappt/))
  })

  async function toPhoneKey(backend: SetupBackend) {
    await toPhoneLogin(backend)
    fireEvent.click(screen.getByText('Passphrase erzeugen'))
    await waitFor(() => screen.getByLabelText('Passphrase'))
    fireEvent.click(screen.getByLabelText(/Passphrase gespeichert/))
    next()
    await waitFor(() => screen.getByText('Claude mit deinem Computer verbinden'))
    next()
    await waitFor(() => screen.getByText('Freigabe vom Handy (Passkey)'))
  }

  it('makes a one-time link for the phone, and goes on only when a new passkey has appeared', async () => {
    clipboard()
    const backend = mockBackend(0)
    const link = vi.spyOn(backend, 'approvalLink')
    await toPhoneKey(backend)
    expect(screen.getByText(/Schritt 10 von 11/)).toBeTruthy()
    expect(screen.queryByText('Prüfen')).toBeNull() // checking only makes sense once there is a link
    fireEvent.click(screen.getByText('Link erzeugen'))
    await waitFor(() => screen.getByLabelText('Link fürs Handy'))
    expect(link).toHaveBeenCalledWith(true)
    expect(screen.getByLabelText('Link fürs Handy').textContent).toBe(`${ADDRESS}/passkey/enroll/demo`)
    expect(screen.getByRole('status').textContent).toMatch(/einmal, 10 Minuten/)
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByText('Prüfen'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Passkey am Handy ist registriert/))
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
  })

  it('notices the new phone passkey by itself, without a click on "Prüfen"', async () => {
    clipboard()
    const backend = mockBackend(0)
    await toPhoneKey(backend)
    expect(screen.getByText(/Link kopieren, in Nachrichten einfügen und an dich selbst schicken/)).toBeTruthy()
    fireEvent.click(screen.getByText('Link erzeugen'))
    await waitFor(() => screen.getByLabelText('Link fürs Handy'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Passkey am Handy ist registriert/), { timeout: 4500 })
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
  }, 9000)

  it('at the limit of 2 passkeys the phone step is one note and one way on', async () => {
    startKeys = 2
    try {
      await toPhoneKey(mockBackend(0))
      expect(screen.getByRole('note').textContent).toMatch(/2 Passkeys/)
      expect(screen.getByRole('note').textContent).toMatch(/Mehr als 2 gehen nicht/)
      expect(screen.queryByText('Link erzeugen')).toBeNull()
      expect(screen.queryByText('Prüfen')).toBeNull()
      expect(screen.queryByText('Passkey am Handy überspringen')).toBeNull()
      expect(screen.queryByText('Zur Verwaltung')).toBeNull()
      expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
    } finally {
      startKeys = 1
    }
  })

  it('asks the person to name the phone passkey', async () => {
    await toPhoneKey(mockBackend(0))
    expect(screen.getByText(/einen Namen geben, zum Beispiel „iPhone“/)).toBeTruthy()
    expect(screen.queryByText(/Es sind schon 2 Passkeys eingerichtet/)).toBeNull() // only at the limit
  })

  it('has clear names for skipping, and the side buttons are small', async () => {
    const backend = mockBackend(0)
    await toPhoneKey(backend)
    expect(screen.getByText('Passkey am Handy überspringen')).toBeTruthy()
    expect(screen.queryByText('Später')).toBeNull()
    expect(screen.queryByText('Noch ein Tipp')).toBeNull()
  })

  it('does not count the passkey of the computer: an unchanged number is not a phone passkey', async () => {
    phoneKeyAppears = false
    const backend = mockBackend(0)
    await toPhoneKey(backend)
    fireEvent.click(screen.getByText('Link erzeugen'))
    await waitFor(() => screen.getByLabelText('Link fürs Handy'))
    fireEvent.click(screen.getByText('Prüfen'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/noch kein neuer Passkey/))
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
  })

  it('says what to do when the limit of two passkeys is reached, and lets the person skip the step', async () => {
    const backend = mockBackend(0)
    backend.approvalLink = async () => ({ ok: false, reason: 'Es sind schon zwei Passkeys eingerichtet, mehr geht nicht. Entferne zuerst einen alten in der Verwaltung (Kachel „Passkeys“).' })
    await toPhoneKey(backend)
    fireEvent.click(screen.getByText('Link erzeugen'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/zwei Passkeys.*Kachel/))
    expect(screen.queryByLabelText('Link fürs Handy')).toBeNull()
    fireEvent.click(screen.getByText(/Passkey (später einrichten|am Handy überspringen)/))
    expect(screen.getByText('Fertig eingerichtet')).toBeTruthy()
    expect(screen.getByText(/Passkey am Handy noch nicht eingerichtet/)).toBeTruthy()
  })

  it('ends with what the phone needs: the test mail in the Claude app, the Mac as server, no Claude Desktop test', async () => {
    clipboard()
    await toPhoneKey(mockBackend(0))
    fireEvent.click(screen.getByText('Link erzeugen'))
    await waitFor(() => screen.getByLabelText('Link fürs Handy'))
    fireEvent.click(screen.getByText('Prüfen'))
    await waitFor(() => screen.getByText(/Passkey am Handy ist registriert/))
    next()
    expect(screen.getByText('Fertig eingerichtet')).toBeTruthy()
    expect(screen.getByText(/Tunnel steht: https:\/\/mein-mac/)).toBeTruthy()
    expect(screen.getByText(/Passphrase festgelegt/)).toBeTruthy()
    expect(screen.getByText(/Passkey am Handy eingerichtet/)).toBeTruthy()
    expect(screen.getByText(/in der Claude-App/)).toBeTruthy()
    expect(screen.getByText(/persönlicher Mail-Server/)).toBeTruthy()
    expect(screen.queryByText(/Schreib Claude in Claude Desktop/)).toBeNull()
  })

  it('says why the switch is locked until the confirmation is ticked', async () => {
    await toTunnel(mockBackend(0))
    await waitFor(() => screen.getByLabelText('Öffentliche Adresse'))
    expect(screen.getByText('Setze zuerst das Häkchen.')).toBeTruthy()
    fireEvent.click(screen.getByLabelText(/darf öffentlich sichtbar sein/))
    expect(screen.queryByText('Setze zuerst das Häkchen.')).toBeNull()
  })

  it('says what is wrong with Tailscale, links the download page only and checks again on request', async () => {
    const backend = mockBackend(0)
    let calls = 0
    backend.tunnelStatus = async () => {
      calls += 1
      return calls === 1
        ? { state: 'missing', address: null, reason: 'Tailscale ist auf diesem Mac nicht installiert.' }
        : { state: 'ready', address: ADDRESS, reason: null }
    }
    const open = vi.spyOn(backend, 'openLink')
    await toTunnel(backend)
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/nicht installiert/))
    expect(screen.queryByText('Tunnel einschalten')).toBeNull()
    expect(screen.getByText(/ein Konto anlegen/)).toBeTruthy()
    expect(screen.getByText(/privates Konto/)).toBeTruthy()
    fireEvent.click(screen.getByText('tailscale.com/download'))
    expect(open).toHaveBeenCalledWith('https://tailscale.com/download')
    fireEvent.click(screen.getByText('Erneut prüfen'))
    await waitFor(() => expect(screen.getByLabelText('Öffentliche Adresse').textContent).toBe(ADDRESS))
    expect(calls).toBe(2)
  })

  it('offers the Tailscale page when Funnel must be allowed first and tries again with the same address', async () => {
    const backend = mockBackend(0)
    const admin = 'https://login.tailscale.com/f/funnel?node=nABC123'
    let tries = 0
    backend.tunnelEnable = async (address) => {
      tries += 1
      return tries === 1
        ? { ok: false, reason: 'Funnel ist in deinem Tailscale-Netz noch nicht freigeschaltet. Öffne die Seite, schalte es dort ein und klicke danach auf „Erneut prüfen“.', url: admin }
        : { ok: true, address, passphrase_placeholder: true }
    }
    const enable = vi.spyOn(backend, 'tunnelEnable')
    const open = vi.spyOn(backend, 'openLink')
    await toTunnel(backend)
    await waitFor(() => screen.getByLabelText('Öffentliche Adresse'))
    fireEvent.click(screen.getByLabelText(/darf öffentlich sichtbar sein/))
    fireEvent.click(screen.getByText('Tunnel einschalten'))
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/noch nicht freigeschaltet/))
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    fireEvent.click(screen.getByText('Seite bei Tailscale öffnen'))
    expect(open).toHaveBeenCalledWith(admin)
    fireEvent.click(screen.getByText('Erneut prüfen'))
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Der Tunnel steht/))
    expect(enable).toHaveBeenCalledTimes(2)
    expect(enable).toHaveBeenLastCalledWith(ADDRESS)
    expect(screen.queryByText('Seite bei Tailscale öffnen')).toBeNull()
  })

  it('says plainly when Tailscale cannot be asked, with the cause, instead of blaming a stopped Tailscale', async () => {
    const backend = mockBackend(0)
    backend.tunnelStatus = async () => ({ state: 'unreadable', address: null, reason: 'Tailscale konnte nicht abgefragt werden. Details: The Tailscale GUI failed to start' })
    await toTunnel(backend)
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/nicht abgefragt werden\. Details: The Tailscale GUI/))
    expect(screen.queryByText('Tailscale-Seite öffnen')).toBeNull()
    expect(screen.queryByText('Tunnel einschalten')).toBeNull()
    expect(screen.getByText('Erneut prüfen')).toBeTruthy()
  })

  it('does not offer the switch when somebody else holds the Tailscale share', async () => {
    const backend = mockBackend(0)
    backend.tunnelStatus = async () => ({ state: 'funnel_busy', address: null, reason: 'Auf diesem Mac nutzt schon etwas anderes die HTTPS-Freigabe von Tailscale. Mail Assistant überschreibt das nicht.' })
    await toTunnel(backend)
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/überschreibt das nicht/))
    expect(screen.queryByText('Tunnel einschalten')).toBeNull()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
  })

  it('goes on to the tunnel when the passkey step is postponed on the phone path', async () => {
    const backend = mockBackend(0)
    backend.status = async () => ({ mailbox: { email: 'ich@web.de', host: 'smtp.web.de', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: 0 })
    render(<Wizard backend={backend} onDone={() => {}} />)
    fireEvent.click(screen.getByLabelText(/Auch vom Handy aus/))
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('ich@web.de'))
    next()
    await waitFor(() => screen.getByText(/schon im Schlüsselbund/))
    next()
    fireEvent.click(screen.getByText('Überspringen'))
    await waitFor(() => screen.getByText(/schon verbunden/))
    next()
    fireEvent.click(screen.getByText(/Passkey (später einrichten|am Handy überspringen)/))
    await waitFor(() => screen.getByText('Tunnel für das Handy (Tailscale)'))
  })

  it('has no tunnel step on the desktop path', async () => {
    render(<Wizard backend={ready(mockBackend(0))} onDone={() => {}} />)
    expect(screen.getByText(/Schritt 1 von 7/)).toBeTruthy()
    expect(screen.queryByText('Tunnel für das Handy (Tailscale)')).toBeNull()
  })
})


describe('the way back to "only this computer"', () => {
  const ADDRESS = 'https://mein-mac.example.ts.net'
  const phoneOn = (backend: SetupBackend, enabled = true) => {
    let traces = true
    backend.remoteStatus = async () => ({ enabled: enabled && traces, traces, address: ADDRESS, passphrase_placeholder: false })
    const real = backend.tunnelDisable.bind(backend)
    backend.tunnelDisable = async () => {
      const r = await real()
      if (r.ok) traces = false
      return r
    }
  }
  const off = () => screen.getByText('Handy-Zugang abschalten') as HTMLButtonElement
  // The page preselects the running phone access; choosing "only this computer" opens the warning.
  const openWarning = async () => {
    await waitFor(() => expect((screen.getByLabelText(/Auch vom Handy aus/) as HTMLInputElement).checked).toBe(true))
    fireEvent.click(screen.getByLabelText(/Nur an diesem Computer/))
  }

  it('warns first, names what goes and what stays, needs the tick and holds "Weiter" back', async () => {
    const backend = mockBackend(0)
    phoneOn(backend)
    const disable = vi.spyOn(backend, 'tunnelDisable')
    render(<Wizard backend={backend} onDone={() => {}} />)
    await openWarning()
    expect(screen.getByText('Der Handy-Zugang ist noch eingerichtet.')).toBeTruthy()
    expect(screen.getByText(/Die öffentliche Adresse wird abgeschaltet/)).toBeTruthy()
    expect(screen.getByText(/neuen Connector-Eintrag und einen neuen Passkey am Handy/)).toBeTruthy()
    expect(screen.getByText(/kann diese App nicht löschen/)).toBeTruthy()
    expect(screen.getByText(/Dein alter Handy-Passkey bleibt in der Passkey-Kachel/)).toBeTruthy()
    expect(screen.getByText(/höchstens 2 Passkeys/)).toBeTruthy()
    expect(screen.getByText(/Postfach, Empfänger und der Passkey dieses Computers bleiben/)).toBeTruthy()
    expect(off().disabled).toBe(true)
    expect(screen.getByText('Setze zuerst das Häkchen.')).toBeTruthy()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText(/„Weiter“ geht erst, wenn du den Handy-Zugang abschaltest/)).toBeTruthy()
    fireEvent.click(off())
    expect(disable).not.toHaveBeenCalled()
  })

  it('switches off after the tick, says so, reminds of the connector and hides the warning', async () => {
    const backend = mockBackend(0)
    phoneOn(backend)
    const disable = vi.spyOn(backend, 'tunnelDisable')
    const open = vi.spyOn(backend, 'openLink')
    render(<Wizard backend={backend} onDone={() => {}} />)
    await openWarning()
    fireEvent.click(screen.getByLabelText(/Ja, den Handy-Zugang abschalten/))
    expect(off().disabled).toBe(false)
    fireEvent.click(off())
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Der Handy-Zugang ist abgeschaltet/))
    expect(disable).toHaveBeenCalledTimes(1)
    expect(screen.queryByText('Der Handy-Zugang ist noch eingerichtet.')).toBeNull()
    expect(screen.queryByText('Handy-Zugang abschalten')).toBeNull()
    expect((screen.getByLabelText(/Nur an diesem Computer/) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText(/Entferne jetzt noch den Connector/)).toBeTruthy()
    fireEvent.click(screen.getByText('Einstellungen öffnen'))
    expect(open).toHaveBeenCalledWith('https://claude.ai/settings/connectors')
  })

  it('has a cancel button that closes the warning, restores the choice and switches nothing off', async () => {
    const backend = mockBackend(0)
    phoneOn(backend)
    const disable = vi.spyOn(backend, 'tunnelDisable')
    render(<Wizard backend={backend} onDone={() => {}} />)
    await openWarning()
    fireEvent.click(screen.getByText('Abbrechen'))
    expect(screen.queryByText('Der Handy-Zugang ist noch eingerichtet.')).toBeNull()
    expect((screen.getByLabelText(/Auch vom Handy aus/) as HTMLInputElement).checked).toBe(true)
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
    expect(disable).not.toHaveBeenCalled()
  })

  it('shows the program\'s remarks, for instance that Tailscale was not reachable', async () => {
    const backend = mockBackend(0)
    phoneOn(backend)
    backend.tunnelDisable = async () => ({ ok: true, removed: true, funnel_closed: false, notes: ['Tailscale war nicht erreichbar.'] })
    render(<Wizard backend={backend} onDone={() => {}} />)
    await openWarning()
    fireEvent.click(screen.getByLabelText(/Ja, den Handy-Zugang abschalten/))
    fireEvent.click(off())
    await waitFor(() => screen.getByText('Tailscale war nicht erreichbar.'))
  })

  it('keeps the warning and the tick after an error, shows the reason, and works on a second try', async () => {
    const backend = mockBackend(0)
    phoneOn(backend)
    let calls = 0
    const real = backend.tunnelDisable.bind(backend)
    backend.tunnelDisable = async () => (++calls === 1 ? { ok: false, reason: 'Tailscale konnte den Funnel nicht ausschalten. Es wurde nichts weiter verändert.' } : real())
    render(<Wizard backend={backend} onDone={() => {}} />)
    await openWarning()
    fireEvent.click(screen.getByLabelText(/Ja, den Handy-Zugang abschalten/))
    fireEvent.click(off())
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/nicht ausschalten/))
    expect(off().disabled).toBe(false)
    expect((screen.getByLabelText(/Ja, den Handy-Zugang abschalten/) as HTMLInputElement).checked).toBe(true)
    fireEvent.click(off())
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/abgeschaltet/))
    expect(calls).toBe(2)
  })

  it('does not start twice while it is working', async () => {
    const backend = mockBackend(0)
    phoneOn(backend)
    let release: (v: { ok: true; removed: boolean; funnel_closed: boolean; notes: string[] }) => void = () => {}
    const disable = vi.fn(() => new Promise<{ ok: true; removed: boolean; funnel_closed: boolean; notes: string[] }>((r) => (release = r)))
    backend.tunnelDisable = disable
    render(<Wizard backend={backend} onDone={() => {}} />)
    await openWarning()
    fireEvent.click(screen.getByLabelText(/Ja, den Handy-Zugang abschalten/))
    fireEvent.click(off())
    expect(off().disabled).toBe(true)
    fireEvent.click(off())
    expect(disable).toHaveBeenCalledTimes(1)
    release({ ok: true, removed: true, funnel_closed: true, notes: [] })
    await waitFor(() => screen.getByRole('status'))
  })

  it('says so when nothing was left to remove', async () => {
    const backend = mockBackend(0)
    phoneOn(backend)
    backend.tunnelDisable = async () => ({ ok: true, removed: false, funnel_closed: false, notes: [] })
    render(<Wizard backend={backend} onDone={() => {}} />)
    await openWarning()
    fireEvent.click(screen.getByLabelText(/Ja, den Handy-Zugang abschalten/))
    fireEvent.click(off())
    await waitFor(() => expect(screen.getByRole('status').textContent).toMatch(/Es war nichts mehr eingerichtet/))
  })

  it('offers "Reste entfernen" for leftovers of a half-finished setup', async () => {
    const backend = mockBackend(0)
    phoneOn(backend, false)
    render(<Wizard backend={backend} onDone={() => {}} />)
    await waitFor(() => screen.getByText('Es gibt noch Reste eines Handy-Zugangs.'))
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
    fireEvent.click(screen.getByText('Reste entfernen …'))
    expect(off()).toBeTruthy()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
  })

  it('stays out of the way when nothing is set up, and while the running phone access is still chosen', async () => {
    const quiet = mockBackend(0)
    quiet.remoteStatus = async () => ({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
    const first = render(<Wizard backend={quiet} onDone={() => {}} />)
    await waitFor(() => screen.getByText('Wo willst du Mail Assistant benutzen?'))
    await new Promise((r) => setTimeout(r, 0))
    expect(screen.queryByText('Handy-Zugang abschalten')).toBeNull()
    expect(screen.queryByText(/Reste/)).toBeNull()
    first.unmount()

    const busy = mockBackend(0)
    phoneOn(busy)
    render(<Wizard backend={busy} onDone={() => {}} />)
    await waitFor(() => expect((screen.getByLabelText(/Auch vom Handy aus/) as HTMLInputElement).checked).toBe(true))
    expect(screen.queryByText('Handy-Zugang abschalten')).toBeNull()
    expect(screen.queryByText('Der Handy-Zugang ist noch eingerichtet.')).toBeNull()
  })
})

describe('page 1 shows the real state, and the help at the Tailscale card', () => {
  const ADDRESS = 'https://mein-mac.example.ts.net'
  const done = (backend: SetupBackend, traces: boolean, enabled = traces) => {
    backend.status = async () => ({ mailbox: { email: 'a@gmail.com', host: 'smtp.gmail.com', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: 1 })
    backend.remoteStatus = async () => ({ enabled, traces, address: traces ? ADDRESS : null, passphrase_placeholder: false })
  }
  async function toTunnel(backend: SetupBackend) {
    backend.status = async () => ({ mailbox: { email: 'ich@web.de', host: 'smtp.web.de', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: 1 })
    backend.remoteStatus = async () => ({ enabled: false, traces: false, address: null, passphrase_placeholder: true })
    render(<Wizard backend={backend} onDone={() => {}} />)
    fireEvent.click(screen.getByLabelText(/Auch vom Handy aus/))
    next()
    await waitFor(() => expect((screen.getByLabelText('E-Mail-Adresse:') as HTMLInputElement).value).toBe('ich@web.de'))
    next()
    await waitFor(() => screen.getByText(/schon im Schlüsselbund/))
    next()
    fireEvent.click(screen.getByText('Überspringen'))
    await waitFor(() => screen.getByText(/schon verbunden/))
    next()
    await waitFor(() => screen.getByText(/Bereits eingerichtet/))
    next()
    await waitFor(() => screen.getByText('Tunnel für das Handy (Tailscale)'))
  }

  it('preselects and marks the running phone access, keeps ngrok and Cloudflare visible, and shows no warning on opening', async () => {
    const backend = mockBackend(0)
    done(backend, true)
    render(<Wizard backend={backend} onDone={() => {}} />)
    await waitFor(() => screen.getByText('Mail Assistant ist eingerichtet'))
    await waitFor(() => expect((screen.getByLabelText(/Auch vom Handy aus/) as HTMLInputElement).checked).toBe(true))
    expect(screen.getByLabelText(/Auch vom Handy aus/).closest('label')!.textContent).toMatch(/\(aktuell\)/)
    expect(screen.getByLabelText(/Tailscale Funnel/).closest('label')!.textContent).toMatch(/\(aktiv\)/)
    expect((screen.getByLabelText(/ngrok/) as HTMLInputElement).disabled).toBe(true)
    expect((screen.getByLabelText(/Cloudflare Tunnel/) as HTMLInputElement).disabled).toBe(true)
    expect(screen.queryByText('Der Handy-Zugang ist noch eingerichtet.')).toBeNull()
    expect(screen.queryByText('Handy-Zugang abschalten')).toBeNull()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText(/Mit „Weiter“ kannst du deine Einstellungen/)).toBeTruthy()
    expect(screen.getByText(/Schritt 1 von 11/)).toBeTruthy()
  })

  it('marks "only this computer" as current when no phone access runs', async () => {
    const backend = mockBackend(0)
    done(backend, false)
    render(<Wizard backend={backend} onDone={() => {}} />)
    await waitFor(() => screen.getByText('Mail Assistant ist eingerichtet'))
    expect(screen.getByLabelText(/Nur an diesem Computer/).closest('label')!.textContent).toMatch(/\(aktuell\)/)
    expect((screen.getByLabelText(/Nur an diesem Computer/) as HTMLInputElement).checked).toBe(true)
    expect(screen.getByText('Weiter')).toBeTruthy()
    expect(screen.queryByText('Handy-Zugang einrichten')).toBeNull()
  })

  it('turns the main button into "Handy-Zugang einrichten" when only the phone is missing, and jumps to the tunnel card', async () => {
    const backend = mockBackend(0)
    done(backend, false)
    render(<Wizard backend={backend} onDone={() => {}} />)
    await waitFor(() => screen.getByText('Mail Assistant ist eingerichtet'))
    fireEvent.click(screen.getByLabelText(/Auch vom Handy aus/))
    expect(screen.getByText(/Die Schritte 2 bis 6 sind schon erledigt\. Es geht gleich bei Schritt 7 weiter/)).toBeTruthy()
    expect(screen.queryByText('Weiter')).toBeNull()
    fireEvent.click(screen.getByText('Handy-Zugang einrichten'))
    await waitFor(() => screen.getByText('Tunnel für das Handy (Tailscale)'))
    expect(screen.getByText(/Schritt 7 von 11/)).toBeTruthy()
  })

  it('asks for nothing at the passkey step when one is already there', async () => {
    const backend = mockBackend(0)
    await toTunnel(backend)
    fireEvent.click(screen.getByText('Zurück'))
    await waitFor(() => screen.getByText(/Du musst hier nichts mehr tun/))
    expect(screen.queryByText('Passkey einrichten')).toBeNull()
    expect(screen.queryByText('Prüfen')).toBeNull()
    expect(screen.queryByText(/Passkey später einrichten/)).toBeNull()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
  })

  it('opens the help closed, opens on click, and closes again when the step changes', async () => {
    const backend = mockBackend(0)
    const open = vi.spyOn(backend, 'openLink')
    await toTunnel(backend)
    await waitFor(() => screen.getByLabelText('Öffentliche Adresse'))
    expect(screen.queryByText(/Was ist Tailscale\?/)).toBeNull()
    const q = screen.getByText('Wie geht das genau?')
    expect(q.getAttribute('aria-expanded')).toBe('false')
    fireEvent.click(q)
    expect(q.getAttribute('aria-expanded')).toBe('true')
    expect(screen.getByText(/Was ist Tailscale\?/)).toBeTruthy()
    expect(screen.getByText(/Apple, Google, Microsoft, GitHub oder ein Passkey/)).toBeTruthy()
    expect(screen.queryByText(/Okta/)).toBeNull()
    expect(q.textContent).toBe('Schließen')
    expect(screen.getByText(/benenne ihn vorher in Tailscale um/)).toBeTruthy()
    fireEvent.click(screen.getByText('Tailscale Funnel'))
    expect(open).toHaveBeenLastCalledWith('https://tailscale.com/docs/features/tailscale-funnel')
    fireEvent.click(screen.getByText('HTTPS-Zertifikate'))
    expect(open).toHaveBeenLastCalledWith('https://tailscale.com/docs/how-to/set-up-https-certificates')
    expect(document.querySelector('a[href]')).toBeNull()
    fireEvent.click(screen.getByText('Zurück'))
    await waitFor(() => screen.getByText(/Bereits eingerichtet/))
    next()
    await waitFor(() => screen.getByText('Tunnel für das Handy (Tailscale)'))
    expect(screen.queryByText(/Was ist Tailscale\?/)).toBeNull()
  })

  it('points to the DNS page when MagicDNS is missing', async () => {
    const backend = mockBackend(0)
    backend.tunnelStatus = async () => ({ state: 'no_magicdns', address: null, reason: 'Für den Tunnel braucht dein Tailscale-Netz MagicDNS und HTTPS-Zertifikate.' })
    const open = vi.spyOn(backend, 'openLink')
    await toTunnel(backend)
    await waitFor(() => screen.getByText('DNS-Seite öffnen'))
    fireEvent.click(screen.getByText('DNS-Seite öffnen'))
    expect(open).toHaveBeenCalledWith('https://login.tailscale.com/admin/dns')
  })
})


describe('the line about passkeys that are already there', () => {
  it('names them neutrally with the day they were made, and falls back to a count', () => {
    const day = new Date(1700000000 * 1000).toLocaleDateString('de-DE')
    expect(knownPasskeys([{ name: 'MacBook', created_at: 1700000000 }], 1)).toBe(`Bereits eingerichtet: MacBook (angelegt am ${day}). Du musst hier nichts mehr tun.`)
    expect(knownPasskeys([{ name: 'MacBook', created_at: 1700000000 }, { name: '', created_at: 1700000000 }], 2)).toContain(`ohne Namen (angelegt am ${day})`)
    expect(knownPasskeys(undefined, 1)).toBe('Bereits eingerichtet: 1 Passkey. Du musst hier nichts mehr tun.')
    expect(knownPasskeys([], 2)).toBe('Bereits eingerichtet: 2 Passkeys. Du musst hier nichts mehr tun.')
  })
})
