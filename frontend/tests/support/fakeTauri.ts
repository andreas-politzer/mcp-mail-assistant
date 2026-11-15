import { createHash } from 'node:crypto'
import { existsSync, lstatSync, readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'

// A stand-in for the Rust side with the same rules as src-tauri/src/local.rs (which has its own
// Rust tests): complete reads or an error, no links, no hidden files, only .md and .txt, files are
// re-checked against their scanned hash. Paths stay inside this object.

type Link = { kind: 'folder' | 'file'; path: string }
interface ScanData { base: string; single: boolean; files: Map<string, string> }

const sha = (b: Buffer) => createHash('sha256').update(b).digest('hex')
const isText = (n: string) => /\.(md|txt)$/i.test(n)
const reachable = (l: Link) => {
  try {
    const m = lstatSync(l.path)
    if (m.isSymbolicLink()) return false
    return l.kind === 'folder' ? m.isDirectory() : m.isFile()
  } catch {
    return false
  }
}

export class FakeTauri {
  links = new Map<string, Link>()
  pending = new Map<string, Link>()
  scans = new Map<string, [string, ScanData]>()
  nextPick: { path: string; kind: 'folder' | 'file' } | null = null
  /** Called before a read; may throw to simulate a failure in a later pass. */
  onRead: ((call: number) => void) | null = null
  readCalls = 0
  calls: string[] = []
  private n = 0

  private id = () => `id${++this.n}`

  private scanDir(dir: string, rel: string, out: { path: string; size: number; sha256: string }[], skipped: unknown[]) {
    let names: string[]
    try {
      names = readdirSync(dir).sort()
    } catch {
      throw 'Ein Ordner ist nicht lesbar. Der Abgleich wurde abgebrochen.'
    }
    for (const name of names) {
      if (name.startsWith('.')) continue
      const full = join(dir, name)
      const m = lstatSync(full)
      const shown = `${rel}${name}`
      if (m.isSymbolicLink()) skipped.push({ path: shown, reason: 'Verknüpfung, wird nicht gefolgt' })
      else if (m.isDirectory()) this.scanDir(full, `${shown}/`, out, skipped)
      else if (m.isFile() && isText(name)) {
        const data = readFileSync(full)
        out.push({ path: shown, size: data.length, sha256: sha(data) })
        if (out.length > 2000) throw 'Mehr als 2000 Dateien. Der Abgleich wurde abgebrochen.'
      }
    }
  }

  invoke = async (cmd: string, args: Record<string, any> = {}): Promise<any> => { // eslint-disable-line @typescript-eslint/no-explicit-any
    this.calls.push(cmd)
    switch (cmd) {
      case 'local_list':
        return [...this.links].map(([server_id, l]) => ({ server_id, kind: l.kind, display: l.path, reachable: reachable(l) }))
      case 'local_pick': {
        const p = this.nextPick
        this.nextPick = null
        if (!p || p.kind !== args.kind) return null
        const token = this.id()
        this.pending.set(token, { kind: p.kind, path: p.path })
        return { token, name: p.path.split('/').pop(), kind: p.kind }
      }
      case 'local_link': {
        const l = this.pending.get(args.token)
        if (!l) throw 'Die Auswahl ist nicht mehr gültig. Bitte noch einmal wählen.'
        this.pending.delete(args.token)
        this.links.set(args.serverId, l)
        this.scans.delete(args.serverId)
        return
      }
      case 'local_unlink':
        this.links.delete(args.serverId)
        this.scans.delete(args.serverId)
        return
      case 'local_scan': {
        const l = this.links.get(args.serverId)
        if (!l) throw 'Für diese Quelle ist auf diesem Computer kein Ordner und keine Datei hinterlegt.'
        if (!reachable(l)) throw 'Die Quelle ist auf diesem Computer nicht erreichbar. Es wurde nichts verändert.'
        const entries: { path: string; size: number; sha256: string }[] = []
        const skipped: unknown[] = []
        if (l.kind === 'file') {
          const data = readFileSync(l.path)
          entries.push({ path: l.path.split('/').pop() as string, size: data.length, sha256: sha(data) })
        } else this.scanDir(l.path, '', entries, skipped)
        const scanId = this.id()
        this.scans.set(args.serverId, [scanId, { base: l.path, single: l.kind === 'file', files: new Map(entries.map((e) => [e.path, e.sha256])) }])
        return { scan_id: scanId, entries, skipped, empty: entries.length === 0 }
      }
      case 'local_read': {
        this.readCalls += 1
        this.onRead?.(this.readCalls)
        const s = this.scans.get(args.serverId)
        if (!s) throw 'Es gibt kein aktuelles Lesen. Bitte neu abgleichen.'
        if (s[0] !== args.scanId) throw 'Das Lesen ist veraltet. Bitte neu abgleichen.'
        return (args.paths as string[]).map((p) => {
          const want = s[1].files.get(p)
          if (!want) throw 'Diese Datei gehörte nicht zum Lesen vorhin.'
          const data = readFileSync(s[1].single ? s[1].base : join(s[1].base, p))
          if (sha(data) !== want) throw `'${p}' hat sich seit dem Lesen geändert. Bitte neu abgleichen.`
          return { path: p, sha256: want, content: data.toString('utf8') }
        })
      }
      case 'local_tree': {
        const l = this.links.get(args.serverId)
        if (!l || l.kind !== 'folder' || !reachable(l)) throw 'Die Quelle ist auf diesem Computer nicht erreichbar.'
        const dir = args.rel ? join(l.path, args.rel) : l.path
        if (!existsSync(dir)) throw 'Der Ordner ist nicht lesbar.'
        const out: { name: string; kind: 'dir' | 'file'; size: number | null }[] = []
        for (const name of readdirSync(dir)) {
          if (name.startsWith('.')) continue
          const m = lstatSync(join(dir, name))
          if (m.isSymbolicLink()) continue
          if (m.isDirectory()) out.push({ name, kind: 'dir', size: null })
          else if (m.isFile() && isText(name)) out.push({ name, kind: 'file', size: m.size })
        }
        return out.sort((a, b) => Number(a.kind !== 'dir') - Number(b.kind !== 'dir') || a.name.localeCompare(b.name))
      }
      case 'quit_app':
        this.quit = true
        return
      default:
        throw `unknown command ${cmd}`
    }
  }
  quit = false
}
