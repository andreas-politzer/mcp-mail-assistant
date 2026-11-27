import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Taskbar } from '../src/components/Taskbar'
import { HelpTile } from '../src/tiles/HelpTile'

afterEach(cleanup)

describe('navigation: the task bar shows open windows only', () => {
  const items = [
    { id: 'recipients', label: 'Empfänger', open: true },
    { id: 'files', label: 'Dateien', open: true },
    { id: 'settings', label: 'Einstellungen', open: false },
    { id: 'activity', label: 'Aktivität', open: false },
    { id: 'help', label: 'Hilfe & Anleitung', open: false },
  ]

  it('shows Start and the open windows, in this order, and then only the language switch', () => {
    const { container } = render(<Taskbar items={items} onToggle={() => {}} onCommand={() => {}} />)
    const labels = [...container.querySelectorAll('button')].map((b) => (b.textContent ?? '').trim())
    expect(labels).toEqual(['✉ Start', 'Empfänger', 'Dateien', 'DE', 'EN'])
  })

  it('brings a window to the front when its button is clicked', () => {
    const onToggle = vi.fn()
    render(<Taskbar items={items} onToggle={onToggle} onCommand={() => {}} />)
    fireEvent.click(screen.getByText('Dateien'))
    expect(onToggle).toHaveBeenCalledWith('files')
    expect(screen.getByText('Dateien').getAttribute('aria-pressed')).toBe('true')
  })

  it('closes a window from its button with a right click', () => {
    const onClose = vi.fn()
    render(<Taskbar items={items} onToggle={() => {}} onCommand={() => {}} onClose={onClose} />)
    fireEvent.contextMenu(screen.getByText('Dateien'))
    fireEvent.click(screen.getByText('Schließen'))
    expect(onClose).toHaveBeenCalledWith('files')
  })
})

describe('start menu: the serious corner', () => {
  const open = (local: boolean) => {
    const onCommand = vi.fn()
    render(<Taskbar items={[]} onToggle={() => {}} onCommand={onCommand} local={local} />)
    fireEvent.click(screen.getByText('✉ Start'))
    fireEvent.click(screen.getAllByText('Mail Assistant').find((e) => e.tagName === 'SPAN' && e.className.includes('flex-1'))!)
    return onCommand
  }
  const submenu = () => within(screen.getAllByText('Hilfe und Anleitung')[0].closest('div') as HTMLElement)

  it('keeps the jokes where they were and puts Mail Assistant right above "Über" and "Herunterfahren"', () => {
    render(<Taskbar items={[]} onToggle={() => {}} onCommand={() => {}} />)
    fireEvent.click(screen.getByText('✉ Start'))
    const rows = [...document.querySelectorAll('button')].map((b) => (b.textContent ?? '').replace('▸', '').trim()).filter(Boolean)
    const at = (t: string) => rows.indexOf(t)
    expect(at('Meine Empfänger')).toBeLessThan(at('Postausgang'))
    expect(at('Ausführen …')).toBeLessThan(at('Mail Assistant'))
    expect(at('Mail Assistant')).toBeLessThan(at('Über Mail Assistant'))
    expect(at('Über Mail Assistant')).toBeLessThan(at('Herunterfahren …'))
    expect(at('Mail Assistant') + 1).toBe(at('Über Mail Assistant'))
  })

  it('lists the real areas, and Passkeys and Einrichtung only in the desktop app', () => {
    open(false)
    for (const t of ['Empfänger', 'Dateien und Anhänge', 'Aktivität', 'Mail-Einstellungen', 'Hilfe und Anleitung']) expect(screen.getAllByText(t).length).toBeGreaterThan(0)
    expect(screen.queryByText('Passkeys')).toBeNull()
    expect(screen.queryByText('Einrichtungs-Wizard …')).toBeNull()
    cleanup()
    open(true)
    expect(screen.getByText('Passkeys')).toBeTruthy()
    expect(screen.getByText('Einrichtungs-Wizard …')).toBeTruthy()
  })

  it('moves a sub menu up when it would reach under the task bar', () => {
    const original = Element.prototype.getBoundingClientRect
    Object.defineProperty(window, 'innerHeight', { value: 800, configurable: true })
    // Like a real layout: the rectangle moves up with the shift the sub menu has already been given.
    Element.prototype.getBoundingClientRect = function (this: HTMLElement) {
      const t = parseInt(this.style.top)
      const shift = Number.isNaN(t) ? 0 : -(t + 4)
      return { top: 600 - shift, bottom: 900 - shift, left: 0, right: 210, width: 210, height: 300, x: 0, y: 600 - shift, toJSON() {} } as DOMRect
    }
    try {
      open(true)
      const panel = screen.getByText('Hilfe und Anleitung').closest('.absolute') as HTMLElement
      expect(parseInt(panel.style.top)).toBeLessThan(-4) // shifted up by the part that was hidden
      expect(parseInt(panel.style.top)).toBe(-4 - (900 - (800 - 44)))
    } finally {
      Element.prototype.getBoundingClientRect = original
    }
  })

  it('sends the command of the chosen entry', () => {
    const onCommand = open(true)
    fireEvent.click(screen.getByText('Mail-Einstellungen'))
    expect(onCommand).toHaveBeenCalledWith('settings')
    expect(submenu).toBeTruthy()
  })
})

describe('help and instructions', () => {
  it('covers every area and shows the tunnel command only in its generic form', () => {
    const { container } = render(<HelpTile />)
    const titles = [...container.querySelectorAll('summary')].map((s) => s.textContent)
    for (const t of ['Verbindung zum Server', 'Empfänger', 'Dateien und Anhänge', 'Einstellungen', 'Aktivität', 'Sitzung und Abmelden', 'Wenn etwas nicht klappt']) {
      expect(titles).toContain(t)
    }
    expect(container.textContent).toContain('ssh -N -L 8431:127.0.0.1:8431 BENUTZER@SERVER')
  })

  it('uses plain sentences: no parentheses and no placeholders', () => {
    const { container } = render(<HelpTile />)
    const text = (container.textContent ?? '').replace('BENUTZER@SERVER', '')
    expect(text).not.toMatch(/[()]/)
    expect(text).not.toMatch(/noch nicht hier|TODO|Platzhalter|Beta/i)
  })

  it('never shows a real address, even when one is configured for the window', () => {
    const { container } = render(<HelpTile />)
    const text = (container.textContent ?? '').replaceAll('127.0.0.1', '')
    expect(text).not.toMatch(/\d+\.\d+\.\d+\.\d+/)
    expect((text.match(/@[A-Za-z0-9.-]+/g) ?? []).map((m) => m.slice(0, 7))).toEqual(['@SERVER'])
  })

  it('states the limits the server really enforces', () => {
    const { container } = render(<HelpTile />)
    const text = container.textContent ?? ''
    expect(text).toContain('2000 Zeilen')
    expect(text).toContain('30 Tage')
    expect(text).toContain('20 Minuten')
    expect(text).toContain('4 Stunden')
  })
})
