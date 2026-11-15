import { cleanup, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Taskbar } from '../src/components/Taskbar'
import { HelpTile } from '../src/tiles/HelpTile'

afterEach(cleanup)

describe('navigation with six areas', () => {
  const items = [
    { id: 'recipients', label: 'Empfänger', open: true },
    { id: 'files', label: 'Dateien', open: true },
    { id: 'settings', label: 'Einstellungen', open: false },
    { id: 'activity', label: 'Aktivität', open: false },
    { id: 'help', label: 'Hilfe & Anleitung', open: false },
  ]

  it('shows Start, Empfänger, Dateien, Einstellungen, Aktivität and Hilfe & Anleitung in this order', () => {
    const { container } = render(<Taskbar items={items} onToggle={() => {}} onCommand={() => {}} />)
    const labels = [...container.querySelectorAll('button')].map((b) => (b.textContent ?? '').trim()).filter((t) => !['Abmelden'].includes(t))
    expect(labels).toEqual(['✉ Start', 'Empfänger', 'Dateien', 'Einstellungen', 'Aktivität', 'Hilfe & Anleitung'])
  })

  it('opens an area when its button is pressed, and marks the open ones', () => {
    const onToggle = vi.fn()
    render(<Taskbar items={items} onToggle={onToggle} onCommand={() => {}} />)
    fireEvent.click(screen.getByText('Hilfe & Anleitung'))
    expect(onToggle).toHaveBeenCalledWith('help')
    expect(screen.getByText('Dateien').getAttribute('aria-pressed')).toBe('true')
    expect(screen.getByText('Aktivität').getAttribute('aria-pressed')).toBe('false')
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
    expect(text).toContain('30 Tagen')
    expect(text).toContain('20 Minuten')
    expect(text).toContain('4 Stunden')
  })
})
