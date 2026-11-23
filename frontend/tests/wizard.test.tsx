import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Wizard } from '../src/wizard/Wizard'
import { guessPreset, looksLikeAddress, PRESETS } from '../src/wizard/presets'
import { mockBackend, type SetupBackend } from '../src/wizard/setupApi'
import { HINTS, STEPS } from '../src/wizard/hints'

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
    for (const s of STEPS) expect(HINTS[s].length).toBeGreaterThan(0)
  })
})

describe('the wizard (desktop path)', () => {
  it('goes through all steps, clears the password and ends at the administration', async () => {
    const backend = mockBackend(0)
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
    fireEvent.click(screen.getByText('Später'))
    expect(screen.getByText(/Passkey noch nicht eingerichtet/)).toBeTruthy()
  })

  it('lets the person skip the test mail and says on the last page where to catch up', async () => {
    const backend = mockBackend(0)
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

  it('does not let the phone path through yet', () => {
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    fireEvent.click(screen.getByLabelText(/Auch vom Handy aus/))
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(true)
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

  it('keeps Funki on screen with the hint of each step and a second tip', () => {
    const backend: SetupBackend = mockBackend(0)
    render(<Wizard backend={backend} onDone={() => {}} />)
    expect(document.querySelector('[data-stack="funki"]')).not.toBeNull()
    expect(screen.getByText(HINTS.where[0])).toBeTruthy()
    fireEvent.click(screen.getByText('Noch ein Tipp'))
    expect(screen.getByText(HINTS.where[1])).toBeTruthy()
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
