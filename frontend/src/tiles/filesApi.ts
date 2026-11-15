import { invoke } from '@tauri-apps/api/core'
import { admin } from '../api/admin'

// Shared helpers of the Files area: server calls, the commands of the Rust side, small texts.
// Local paths exist only on this Mac; the window gets display texts and ids, never a path to read.

export interface Source {
  id: string
  label: string
  kind: 'upload' | 'server_folder'
  scope: 'folder' | 'file'
  file_name: string | null
  files: number
  bytes: number
}
export interface Quota {
  sources: number
  max_sources: number
}
export interface LocalView {
  server_id: string
  kind: 'folder' | 'file'
  display: string
  reachable: boolean
}
export interface Picked {
  token: string
  name: string
  kind: 'folder' | 'file'
}
export interface ManifestEntry {
  path: string
  size: number
  sha256: string
}
export interface ScanOut {
  scan_id: string
  entries: ManifestEntry[]
  skipped: { path: string; reason: string }[]
  empty: boolean
}
export interface SentFile {
  path: string
  sha256: string
  content: string
}
export interface TreeItem {
  name: string
  kind: 'dir' | 'file'
  size: number | null
}

export const inApp = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
export const NO_ANSWER = 'Der Server antwortet nicht. Läuft der Tunnel noch?'

export async function server(method: string, path: string, body?: unknown) {
  try {
    return await admin.call(method, path, body)
  } catch {
    return { status: 0, data: { message: NO_ANSWER } }
  }
}
export const problem = (r: { status: number; data: { message?: string } }) =>
  r.data?.message ?? (r.status === 0 ? NO_ANSWER : `Der Server meldet Fehler ${r.status}.`)
export const asText = (e: unknown) => (typeof e === 'string' ? e : e instanceof Error ? e.message : 'Unbekannter Fehler.')
export const kb = (b: number) => (b < 1000 ? `${b} Byte` : `${Math.round(b / 1000)} KB`)
export const countText = (n: number) => `${n} ${n === 1 ? 'Datei' : 'Dateien'}`

export const local = {
  list: () => invoke<LocalView[]>('local_list'),
  pick: (kind: 'folder' | 'file') => invoke<Picked | null>('local_pick', { kind }),
  link: (token: string, serverId: string) => invoke<void>('local_link', { token, serverId }),
  unlink: (serverId: string) => invoke<void>('local_unlink', { serverId }),
  scan: (serverId: string) => invoke<ScanOut>('local_scan', { serverId }),
  read: (serverId: string, scanId: string, paths: string[]) => invoke<SentFile[]>('local_read', { serverId, scanId, paths }),
  tree: (serverId: string, rel: string) => invoke<TreeItem[]>('local_tree', { serverId, rel }),
}
