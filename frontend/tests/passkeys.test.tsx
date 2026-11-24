import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PasskeysTile } from '../src/local/PasskeysTile'
import { mockLocalBackend } from '../src/local/localApi'
import { mockBackend } from '../src/wizard/setupApi'
import type { ConfirmRequest } from '../src/core/types'

afterEach(cleanup)

const key = { id: 'abc123def456', name: 'MacBook', created_at: 1700000000, last_used_at: null }

describe('passkey tile', () => {
  it('lists passkeys and removes one only after the confirmation', async () => {
    const local = mockLocalBackend({ passkeys: [key] })
    const asked: ConfirmRequest[] = []
    render(<PasskeysTile ask={(r) => asked.push(r)} local={local} setup={mockBackend(0)} />)
    expect(await screen.findByText('MacBook')).toBeTruthy()
    expect(screen.getByText('Kennung abc123', { exact: false })).toBeTruthy()
    fireEvent.click(screen.getByText('Entfernen'))
    expect(asked[0].title).toBe('Passkey entfernen?')
    expect((await local.snapshot()).ok && (await local.snapshot())).toBeTruthy()
    asked[0].run()
    await waitFor(() => expect(screen.queryByText('MacBook')).toBeNull())
    expect(await screen.findByText('Der Passkey ist entfernt.')).toBeTruthy()
  })

  it('names the passkey in the row and warns that the device keeps its own entry', async () => {
    const asked: ConfirmRequest[] = []
    render(<PasskeysTile ask={(r) => asked.push(r)} local={mockLocalBackend({ passkeys: [key] })} setup={mockBackend(0)} />)
    fireEvent.click(await screen.findByText('Entfernen'))
    const text = JSON.stringify(asked[0].body)
    expect(text).toContain('MacBook')
    expect(text).toContain('Passwörter-App')
  })

  it('shows older passkeys without a name as such and renames one', async () => {
    const old = { id: 'zzz999yyy888', name: '', created_at: 1700000000, last_used_at: 1700001000 }
    const local = mockLocalBackend({ passkeys: [old] })
    render(<PasskeysTile ask={() => {}} local={local} setup={mockBackend(0)} />)
    expect(await screen.findByText('Ohne Namen (älterer Passkey)')).toBeTruthy()
    fireEvent.click(screen.getByText('Umbenennen'))
    fireEvent.change(screen.getByLabelText('Neuer Name'), { target: { value: '  iPhone  ' } })
    fireEvent.click(screen.getByText('Speichern'))
    expect(await screen.findByText('Der Name ist gespeichert.')).toBeTruthy()
    expect(await screen.findByText('iPhone')).toBeTruthy()
    expect(screen.queryByText('Ohne Namen (älterer Passkey)')).toBeNull()
  })

  it('does not rename to an empty name and can be cancelled', async () => {
    render(<PasskeysTile ask={() => {}} local={mockLocalBackend({ passkeys: [key] })} setup={mockBackend(0)} />)
    fireEvent.click(await screen.findByText('Umbenennen'))
    fireEvent.change(screen.getByLabelText('Neuer Name'), { target: { value: '   ' } })
    fireEvent.click(screen.getByText('Speichern'))
    expect(await screen.findByText('Bitte einen Namen eingeben.')).toBeTruthy()
    fireEvent.click(screen.getByText('Abbrechen'))
    expect(screen.getByText('MacBook')).toBeTruthy()
    expect(screen.queryByLabelText('Neuer Name')).toBeNull()
  })

  it('switches enrolling off at the limit and says why', async () => {
    const two = [key, { ...key, id: 'def456abc123', name: 'iPhone' }]
    render(<PasskeysTile ask={() => {}} local={mockLocalBackend({ passkeys: two })} setup={mockBackend(0)} />)
    expect(await screen.findByText('iPhone')).toBeTruthy()
    expect((screen.getByText('Neuen Passkey einrichten') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('Es sind schon 2 Passkeys eingerichtet', { exact: false })).toBeTruthy()
  })

  it('opens the enrollment link for a new passkey', async () => {
    const setup = mockBackend(0)
    const open = vi.spyOn(setup, 'openLink')
    render(<PasskeysTile ask={() => {}} local={mockLocalBackend()} setup={setup} />)
    fireEvent.click(await screen.findByText('Neuen Passkey einrichten'))
    await waitFor(() => expect(open).toHaveBeenCalledWith('http://localhost:47821/passkey/enroll/demo'))
  })

  it('shows the reason when the link cannot be made', async () => {
    const setup = mockBackend(0)
    setup.approvalLink = async () => ({ ok: false, reason: 'Claude Desktop läuft nicht.' })
    render(<PasskeysTile ask={() => {}} local={mockLocalBackend()} setup={setup} />)
    fireEvent.click(await screen.findByText('Neuen Passkey einrichten'))
    expect(await screen.findByText('Claude Desktop läuft nicht.')).toBeTruthy()
  })
})
