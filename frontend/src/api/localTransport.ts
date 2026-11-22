import { invoke } from '@tauri-apps/api/core'
import type { Reply, Transport } from './admin'

// The window's way to the admin application of the bundled program (Rust command `local_api`).
// The answer has the same shape as an answer from the network: a status and the JSON data.
export const tauriTransport: Transport = async (method, path, body) => {
  try {
    const r = await invoke<{ status: number; data: unknown }>('local_api', { method, path, body: body ?? null })
    return { status: r.status, data: r.data ?? {} } as Reply
  } catch (e) {
    const message = typeof e === 'string' ? e : 'Die lokale Verwaltung antwortet nicht.'
    return { status: 0, data: { message } }
  }
}
