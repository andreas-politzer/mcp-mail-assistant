import { describe, expect, it, vi } from 'vitest'
import { Joke } from '../src/components/Jokes'
import { admin } from '../src/api/admin'
import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { api, fake, realFetch, useSignedInServer } from './support/harness'

useSignedInServer()

const common = { onClose: vi.fn(), onFocus: vi.fn(), initial: { x: 10, y: 10 }, onOpen: vi.fn() }

describe('shutting down', () => {
  it('cancelling leaves the app and the session alone', async () => {
    const onClose = vi.fn()
    render(<Joke kind="shutdown" {...common} onClose={onClose} />)
    fireEvent.click(await screen.findByText('Abbrechen'))
    expect(onClose).toHaveBeenCalled()
    expect(fake.quit).toBe(false)
    expect(admin.isSignedIn()).toBe(true)
    expect((await api('GET', '/admin/status')).status).toBe(200)
  })

  it('signs out on the server first, then ends the app', async () => {
    // remember the token of this session to prove the server ended it
    const real = realFetch
    let token = ''
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input).endsWith('/admin/logout')) token = String((init?.headers as Record<string, string>)?.Authorization ?? '')
      return real(input, init)
    })
    render(<Joke kind="shutdown" {...common} />)
    fireEvent.click(await screen.findByText('Beenden'))
    await waitFor(() => expect(fake.quit).toBe(true))
    spy.mockRestore()
    expect(fake.calls.at(-1)).toBe('quit_app')
    expect(admin.isSignedIn()).toBe(false)
    expect(token).toMatch(/^Bearer /)
    const after = await real('http://127.0.0.1:18431/admin/status', { headers: { Authorization: token } })
    expect(after.status).toBe(401)
  })

  it('ends the app even when the server does not answer', async () => {
    fake.quit = false
    await admin.login('test passphrase for the front end')
    const real = realFetch
    const spy = vi.spyOn(globalThis, 'fetch').mockImplementation(async (input, init) => {
      if (String(input).endsWith('/admin/logout')) throw new TypeError('offline')
      return real(input, init)
    })
    render(<Joke kind="shutdown" {...common} />)
    fireEvent.click(await screen.findByText('Beenden'))
    await waitFor(() => expect(fake.quit).toBe(true))
    spy.mockRestore()
  })
})
