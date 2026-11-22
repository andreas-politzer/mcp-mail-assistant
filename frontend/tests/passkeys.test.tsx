import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { PasskeysTile } from '../src/local/PasskeysTile'
import { mockLocalBackend } from '../src/local/localApi'
import { mockBackend } from '../src/wizard/setupApi'
import type { ConfirmRequest } from '../src/core/types'

afterEach(cleanup)

const key = { id: 'abc123def456', created_at: 1700000000, last_used_at: null }

describe('passkey tile', () => {
  it('lists passkeys and removes one only after the confirmation', async () => {
    const local = mockLocalBackend({ passkeys: [key] })
    const asked: ConfirmRequest[] = []
    render(<PasskeysTile ask={(r) => asked.push(r)} local={local} setup={mockBackend(0)} />)
    expect(await screen.findByText('Passkey abc123', { exact: false })).toBeTruthy()
    fireEvent.click(screen.getByText('Entfernen'))
    expect(asked[0].title).toBe('Passkey entfernen?')
    expect((await local.snapshot()).ok && (await local.snapshot())).toBeTruthy()
    asked[0].run()
    await waitFor(() => expect(screen.queryByText('Passkey abc123', { exact: false })).toBeNull())
    expect(await screen.findByText('Der Passkey ist entfernt.')).toBeTruthy()
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
