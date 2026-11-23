import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Wizard } from '../src/wizard/Wizard'
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

  it('lets the phone path through and says it is work in progress', () => {
    render(<Wizard backend={mockBackend(0)} onDone={() => {}} />)
    fireEvent.click(screen.getByLabelText(/Auch vom Handy aus/))
    expect(screen.getByText(/Zwischenstand/)).toBeTruthy()
    expect((screen.getByText('Weiter') as HTMLButtonElement).disabled).toBe(false)
    expect(screen.getByText(/Schritt 1 von 8/)).toBeTruthy()
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

describe('the tunnel step (phone path)', () => {
  const ADDRESS = 'https://mein-mac.example.ts.net'

  // A computer that is set up completely, so the steps before the tunnel can be passed quickly.
  function ready(backend: SetupBackend) {
    backend.status = async () => ({ mailbox: { email: 'ich@web.de', host: 'smtp.web.de', port: 587, security: 'starttls' }, password_stored: true, claude_connected: true, passkeys: 1 })
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
    await waitFor(() => screen.getByText(/schon registriert/))
    next()
    await waitFor(() => screen.getByText('Tunnel für das Handy (Tailscale)'))
  }

  it('shows the address first, needs the confirmation, then switches the tunnel on', async () => {
    const backend = mockBackend(0)
    const enable = vi.spyOn(backend, 'tunnelEnable')
    await toTunnel(backend)
    expect(screen.getByText(/Schritt 7 von 8/)).toBeTruthy()
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
    expect(screen.getByText(/Tunnel steht: https:\/\/mein-mac/)).toBeTruthy()
    expect(screen.getByText(/in der Claude-App/)).toBeTruthy()
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
        ? { state: 'missing', address: null, reason: 'Tailscale ist auf diesem Mac nicht installiert. Installiere es von tailscale.com/download und melde dich dort an.' }
        : { state: 'ready', address: ADDRESS, reason: null }
    }
    const open = vi.spyOn(backend, 'openLink')
    await toTunnel(backend)
    await waitFor(() => expect(screen.getByRole('alert').textContent).toMatch(/nicht installiert/))
    expect(screen.queryByText('Tunnel einschalten')).toBeNull()
    fireEvent.click(screen.getByText('Tailscale-Seite öffnen'))
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
    fireEvent.click(screen.getByText('Später'))
    await waitFor(() => screen.getByText('Tunnel für das Handy (Tailscale)'))
  })

  it('has no tunnel step on the desktop path', async () => {
    render(<Wizard backend={ready(mockBackend(0))} onDone={() => {}} />)
    expect(screen.getByText(/Schritt 1 von 7/)).toBeTruthy()
    expect(screen.queryByText('Tunnel für das Handy (Tailscale)')).toBeNull()
  })
})
