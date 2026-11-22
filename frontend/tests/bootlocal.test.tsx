import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { Boot } from '../src/boot/Boot'
import { admin } from '../src/api/admin'

afterEach(() => {
  cleanup()
  admin.useLocal(null)
})

describe('start show with the admin application inside the app', () => {
  it('plays the show, asks for no sign-in and leaves for the desktop', async () => {
    admin.useLocal(async () => ({ status: 200, data: { schema_version: 5, recipients: { active: 2 } } }))
    const done = vi.fn()
    render(<Boot resume={false} local onDone={done} />)
    fireEvent.pointerDown(window) // skips the minimum show time
    await waitFor(() => expect(screen.getByText('Der Briefkasten hat Dienst.')).toBeTruthy(), { timeout: 8000 })
    expect(screen.queryByText('Anmelden')).toBeNull()
    await waitFor(() => expect(done).toHaveBeenCalled(), { timeout: 8000 })
  })

  it('says so when the admin application does not answer', async () => {
    admin.useLocal(async () => ({ status: 0, data: { message: 'Das Mail-Assistant-Programm lässt sich nicht starten.' } }))
    render(<Boot resume={false} local onDone={() => {}} />)
    expect(await screen.findByText('Die Verwaltung in dieser App ist nicht angesprungen.')).toBeTruthy()
    expect(screen.getByText('Das Mail-Assistant-Programm lässt sich nicht starten.')).toBeTruthy()
    expect(screen.queryByText('Befehl kopieren')).toBeNull()
  })
})
