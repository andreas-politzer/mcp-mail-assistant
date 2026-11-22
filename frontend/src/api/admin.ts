// Client for the admin API (runs on the server, reached through the SSH tunnel on this computer).
// The session token lives in this module's memory only: never in localStorage, sessionStorage, a cookie or a file.
// Reloading the window therefore ends the session, which is intended.

export const ADMIN_BASE: string = import.meta.env.VITE_ADMIN_BASE ?? 'http://localhost:8431'
// Shown when the tunnel is missing. Put the real command into frontend/.env.local (not committed):
// VITE_TUNNEL_COMMAND=/usr/bin/ssh -N -L 8431:127.0.0.1:8431 user@server
// What windows and help texts show: always the generic form, never a person's own server address.
export const GENERIC_TUNNEL_COMMAND = '/usr/bin/ssh -N -L 8431:127.0.0.1:8431 BENUTZER@SERVER'
// What the copy button puts on the clipboard: the configured command, or the generic one if none is configured.
export const TUNNEL_COMMAND: string = import.meta.env.VITE_TUNNEL_COMMAND ?? GENERIC_TUNNEL_COMMAND
// The database schema this app was written for.
export const EXPECTED_SCHEMA = 5

let token: string | null = null
let session: { since: number; idleSeconds: number; maxSeconds: number } | null = null
const listeners = new Set<() => void>()

export interface Reply {
  status: number
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  data: any
}

// In the desktop app the admin application runs inside the bundled program on this computer. The window then
// asks through this function instead of the network, and there is no sign-in and no token on this side.
export type Transport = (method: string, path: string, body?: unknown) => Promise<Reply>
let transport: Transport | null = null

async function request(method: string, path: string, body?: unknown): Promise<Reply> {
  if (transport) return transport(method, path, body)
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }
  if (token) headers.Authorization = `Bearer ${token}`
  const res = await fetch(ADMIN_BASE + path, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) })
  let data: unknown = {}
  try {
    data = await res.json()
  } catch {
    /* empty body */
  }
  if (res.status === 401 && token) endSession() // the server dropped the session (idle or maximum time)
  return { status: res.status, data }
}

function endSession() {
  token = null
  session = null
  listeners.forEach((fn) => fn())
}

export type Probe = 'up' | 'unreachable' | 'unexpected'

export const admin = {
  // Switches to the local admin application (or back to the network with null).
  useLocal(t: Transport | null) {
    transport = t
  },
  isLocal: () => transport !== null,
  isSignedIn: () => token !== null || transport !== null,

  // Is anything answering on the tunnel? Without a token the server answers 401, which is the healthy answer here.
  // A refused connection and an origin the server does not allow look the same to the browser (a failed fetch).
  async probe(): Promise<{ result: Probe; status?: number }> {
    try {
      const res = await fetch(ADMIN_BASE + '/admin/status')
      return res.status === 401 ? { result: 'up' } : { result: 'unexpected', status: res.status }
    } catch {
      return { result: 'unreachable' }
    }
  },

  async login(passphrase: string): Promise<Reply> {
    const reply = await request('POST', '/admin/login', { passphrase })
    if (reply.status === 200 && typeof reply.data?.token === 'string') {
      token = reply.data.token
      session = { since: Date.now(), idleSeconds: Number(reply.data.idle_seconds) || 0, maxSeconds: Number(reply.data.max_seconds) || 0 }
    }
    return reply
  },

  async logout(): Promise<void> {
    try {
      await request('POST', '/admin/logout')
    } catch {
      /* the server forgets the session by itself */
    }
    token = null
    session = null
  },

  // Any call to the admin API (after sign-in). Network errors are thrown; HTTP errors come back as a Reply.
  call: (method: string, path: string, body?: unknown) => request(method, path, body),
  sessionInfo: () => session,

  status: () => request('GET', '/admin/status'),

  // Called when the session ends from the server side (not on a normal logout).
  onSessionEnd(fn: () => void): () => void {
    listeners.add(fn)
    return () => {
      listeners.delete(fn)
    }
  },
}
