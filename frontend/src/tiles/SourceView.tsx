import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { AskConfirm } from '../core/types'
import { asText, countText, inApp, kb, local, problem, server, type LocalView, type Source } from './filesApi'
import { SyncPanel } from './SyncPanel'

// One source in detail: its folders and files with the permissions, and for a source from this computer
// the copies on the server and the synchronisation.

interface Node {
  path: string
  name: string
  kind: 'folder' | 'file'
  state: 'allowed' | 'denied' | 'none' | 'invalid'
  source: string | null
  inherited: boolean
  own: 'allow' | 'deny' | null
  sensitive: boolean
  error?: string
}
type Level = { nodes: Node[]; truncated: boolean } | { error: string } | 'loading'

interface StoredFile {
  path: string
  state: string
  reason: string | null
  version: number
  id: string | null
  size: number
  uploaded_at: number
  allowed_now: boolean
}

const REASON: Record<string, string> = {
  blocked: 'wegen einer Sperre gelöscht',
  removed_local: 'auf dem Computer nicht mehr vorhanden',
  source_removed: 'Quelle entfernt',
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-4">
      <div className="text-[11px] uppercase tracking-widest opacity-90 py-1.5 border-b border-white/30 mb-2">{title}</div>
      {children}
    </section>
  )
}

const join = (base: string, name: string) => (base ? `${base}/${name}` : name)

async function fetchLevel(src: Source, path: string): Promise<{ nodes: Node[]; truncated: boolean }> {
  if (src.kind === 'server_folder') {
    const r = await server('GET', `/admin/sources/${src.id}/folders?path=${encodeURIComponent(path)}`)
    if (r.status !== 200) throw new Error(problem(r))
    const nodes: Node[] = (r.data.entries as Record<string, any>[]).map((e) => ({ // eslint-disable-line @typescript-eslint/no-explicit-any
      path: e.path, name: e.name, kind: e.kind, state: e.state, source: e.source, inherited: !!e.inherited,
      own: e.own_rule ?? null, sensitive: !!e.sensitive,
    }))
    return { nodes, truncated: !!r.data.truncated }
  }
  const items = await local.tree(src.id, path)
  const paths = items.map((i) => join(path, i.name))
  // The server takes small bodies on this route, so the paths go in chunks of about 10 KB.
  const chunks: string[][] = []
  let cur: string[] = []
  let size = 0
  for (const p of paths) {
    if (cur.length && (size + p.length + 4 > 10_000 || cur.length >= 1000)) {
      chunks.push(cur)
      cur = []
      size = 0
    }
    cur.push(p)
    size += p.length + 4
  }
  if (cur.length) chunks.push(cur)
  const nodes: Node[] = []
  let from = 0
  for (const chunk of chunks) {
    const r = await server('POST', `/admin/sources/${src.id}/evaluate`, { paths: chunk })
    if (r.status !== 200) throw new Error(problem(r))
    const base = from
    ;(r.data.results as Record<string, any>[]).forEach((e, k) => { // eslint-disable-line @typescript-eslint/no-explicit-any
      const item = items[base + k]
      nodes.push({
        path: chunk[k], name: item.name, kind: item.kind === 'dir' ? 'folder' : 'file', state: e.state,
        source: e.source_rule ?? null, inherited: !!e.inherited, own: e.own_rule ?? null,
        sensitive: !!e.sensitive, error: e.error,
      })
    })
    from += chunk.length
  }
  return { nodes, truncated: false }
}

async function fetchSingle(src: Source): Promise<Node[]> {
  const name = src.file_name ?? ''
  const r = await server('POST', `/admin/sources/${src.id}/evaluate`, { paths: [name] })
  if (r.status !== 200) throw new Error(problem(r))
  const e = r.data.results[0]
  return [{ path: name, name, kind: 'file', state: e.state, source: e.source_rule ?? null, inherited: !!e.inherited, own: e.own_rule ?? null, sensitive: !!e.sensitive, error: e.error }]
}

export function SourceView({
  source,
  linked,
  ask,
  onBack,
  onChanged,
}: {
  source: Source
  linked: LocalView | undefined
  ask: AskConfirm
  onBack: () => void
  onChanged: () => Promise<void>
}) {
  const isServer = source.kind === 'server_folder'
  const single = source.scope === 'file'
  const placeOk = isServer || (inApp && !!linked && linked.reachable)

  const [levels, setLevels] = useState<Record<string, Level>>({})
  const [open, setOpen] = useState<Set<string>>(new Set())
  const [note, setNote] = useState<{ text: string; bad: boolean } | null>(null)
  const [stored, setStored] = useState<StoredFile[] | null>(null)
  const [cursor, setCursor] = useState<string | null>(null)
  const [storedError, setStoredError] = useState('')

  const loadLevel = useCallback(
    async (path: string) => {
      setLevels((p) => ({ ...p, [path]: 'loading' }))
      try {
        const lvl = single ? { nodes: await fetchSingle(source), truncated: false } : await fetchLevel(source, path)
        setLevels((p) => ({ ...p, [path]: lvl }))
      } catch (e) {
        setLevels((p) => ({ ...p, [path]: { error: asText(e) } }))
      }
    },
    [source, single],
  )

  const loadStored = useCallback(
    async (after: string | null) => {
      if (isServer) return
      setStoredError('')
      const r = await server('GET', `/admin/sources/${source.id}/files${after ? `?cursor=${encodeURIComponent(after)}` : ''}`)
      if (r.status !== 200) return setStoredError(problem(r))
      setStored((prev) => (after && prev ? [...prev, ...r.data.files] : r.data.files))
      setCursor(r.data.next_cursor ?? null)
    },
    [source.id, isServer],
  )

  useEffect(() => {
    setLevels({})
    setOpen(new Set())
    if (placeOk) void loadLevel('')
    void loadStored(null)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [source.id, placeOk])

  const refreshTree = async (paths = Object.keys(levels)) => {
    await Promise.all(paths.map((p) => loadLevel(p)))
  }

  const toggle = (node: Node) => {
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(node.path)) next.delete(node.path)
      else {
        next.add(node.path)
        if (!levels[node.path]) void loadLevel(node.path)
      }
      return next
    })
  }

  // ---- rules ----
  const putRule = async (path: string, effect: 'allow' | 'deny' | 'clear', confirmation?: string) => {
    const r = await server('PUT', `/admin/sources/${source.id}/rule`, { path, effect, ...(confirmation ? { confirmation_id: confirmation } : {}) })
    if (r.status !== 200) {
      setNote({ text: problem(r), bad: true })
      if (r.status === 409) void refreshTree()
      return
    }
    if (r.data.status === 'confirmation_required') {
      const p = r.data.preview ?? {}
      const gain =
        'stored_files_allowed_after' in p ? (
          <>Danach darf Claude <b>{p.stored_files_allowed_after}</b> der gespeicherten Kopien anhängen, bisher {p.stored_files_allowed_before}. Dateien von diesem Computer kommen erst nach dem nächsten Abgleich dazu.</>
        ) : 'files_attachable_after' in p ? (
          <>Danach darf Claude <b>{p.files_attachable_after}</b> Dateien anhängen, bisher {p.files_attachable_before}.{p.count_is_partial ? ' Die Zählung ist unvollständig, es können mehr sein.' : ''}</>
        ) : null
      ask({
        title: effect === 'allow' ? (path === '' ? 'Ganzen Ordner freigeben?' : 'Ordner freigeben?') : 'Sperre aufheben?',
        body: effect === 'allow' ? (
          <>Claude darf aus <b>{path === '' ? 'dem ganzen Ordner' : path}</b> künftig Dateien an Mails anhängen. Gesperrte Unterordner und Dateien bleiben gesperrt. {gain}</>
        ) : (
          <>Die Sperre auf <b>{path === '' ? 'dem ganzen Ordner' : path}</b> fällt weg. Ob Claude dort etwas anhängen darf, hängt danach von den übrigen Regeln ab. {gain}</>
        ),
        confirmLabel: effect === 'allow' ? 'Freigeben' : 'Sperre aufheben',
        run: () => void putRule(path, effect, r.data.confirmation_id),
      })
      return
    }
    if (r.data.status === 'unchanged') {
      setNote({ text: 'Keine Änderung.', bad: false })
      return
    }
    const purged = Number(r.data.purged_files) || 0
    const drafts = Number(r.data.cancelled_drafts) || 0
    setNote({
      text: `Gespeichert.${purged ? ` ${countText(purged)} auf dem Server gelöscht.` : ''}${drafts ? ` ${drafts} ${drafts === 1 ? 'offener Entwurf' : 'offene Entwürfe'} mit diesen Dateien verworfen.` : ''}`,
      bad: false,
    })
    await refreshTree()
    await loadStored(null)
    await onChanged()
  }

  const renderNode = (node: Node, depth: number): ReactNode => {
    const isFolder = node.kind === 'folder'
    const isOpen = open.has(node.path)
    const hardDenied = node.state === 'denied' && node.own !== 'deny'
    const via = node.source ? `„${node.source.split('/').pop()}“` : ''
    let badge = <span className="text-[11px] opacity-60 whitespace-nowrap">nicht freigegeben</span>
    if (node.state === 'allowed') badge = <span className="text-[11px] whitespace-nowrap" style={{ color: '#b9ffd8' }}>✓ erlaubt{node.inherited ? ` über ${via}` : ''}</span>
    if (node.state === 'denied') badge = <span className="text-[11px] whitespace-nowrap" style={{ color: '#ffc9cd' }}>⛔ gesperrt{node.own === 'deny' ? '' : ` über ${via}`}</span>
    if (node.state === 'invalid') badge = <span className="text-[11px] whitespace-nowrap" style={{ color: '#ffc9cd' }}>nicht möglich</span>
    const level = levels[node.path]
    return (
      <div key={node.path}>
        <div data-testid={`node-${node.path}`} className="flex items-center gap-2 py-1 border-b border-white/10" style={{ paddingLeft: depth * 16 }}>
          {isFolder ? (
            <button className="w-4 text-xs opacity-90" onClick={() => toggle(node)} aria-label={isOpen ? 'zuklappen' : 'aufklappen'}>{isOpen ? '▾' : '▸'}</button>
          ) : (
            <span className="w-4 text-xs opacity-60">·</span>
          )}
          <span className="text-[13px] flex-1 min-w-0 truncate" title={node.sensitive ? 'Dateien mit Namen wie Passwort oder Schlüssel werden nie angehängt.' : node.error}>
            {isFolder ? '📁' : '📄'} {node.name}{node.sensitive && ' 🔑'}
          </span>
          {badge}
          {node.state !== 'invalid' && !hardDenied && isFolder && !single && node.own !== 'allow' && node.own !== 'deny' && <button className="gbtn primary" onClick={() => void putRule(node.path, 'allow')}>Freigeben</button>}
          {!hardDenied && isFolder && node.own === 'allow' && <button className="gbtn" onClick={() => void putRule(node.path, 'clear')}>Zurücksetzen</button>}
          {node.state !== 'invalid' && !hardDenied && node.own !== 'deny' && <button className="gbtn danger" onClick={() => void putRule(node.path, 'deny')}>Sperren</button>}
          {node.own === 'deny' && <button className="gbtn" onClick={() => void putRule(node.path, 'clear')}>Sperre aufheben</button>}
        </div>
        {isFolder && isOpen && renderLevel(node.path, depth + 1, level)}
      </div>
    )
  }

  const renderLevel = (path: string, depth: number, level: Level | undefined): ReactNode => {
    if (!level || level === 'loading') return <div className="text-[12px] opacity-70 py-1" style={{ paddingLeft: depth * 16 }}>Lade …</div>
    if ('error' in level) return <div className="text-[12px] py-1" style={{ paddingLeft: depth * 16, color: '#ffc9cd' }}>{level.error} <button className="gbtn ml-2" onClick={() => void loadLevel(path)}>Erneut versuchen</button></div>
    return (
      <>
        {level.nodes.length === 0 && <div className="text-[12px] opacity-70 py-1" style={{ paddingLeft: depth * 16 }}>Hier liegt nichts, was sich freigeben ließe.</div>}
        {level.nodes.map((n) => renderNode(n, depth))}
        {level.truncated && <div className="text-[11px] opacity-70 py-1" style={{ paddingLeft: depth * 16 }}>Es werden nicht alle Einträge angezeigt.</div>}
      </>
    )
  }

  const stateLabel = (f: StoredFile) =>
    f.state === 'current'
      ? f.allowed_now ? <span style={{ color: '#b9ffd8' }}>✓ bereit zum Anhängen</span> : <span className="opacity-70">nicht freigegeben</span>
      : <span className="opacity-60">{f.state === 'removed' ? 'entfernt' : 'gelöscht'}{f.reason ? `, ${REASON[f.reason] ?? f.reason}` : ''}</span>

  return (
    <div className="tile-text flex-1 min-h-0 flex flex-col">
      <div className="px-6 pb-2 flex items-center gap-2">
        <button className="gbtn" onClick={onBack}>← Alle Quellen</button>
        <span className="text-[13px] font-semibold flex-1 min-w-0 truncate">{isServer ? '🗄️' : single ? '📄' : '📁'} {source.label}</span>
        <span className="text-[11px] opacity-80 whitespace-nowrap">{countText(source.files)}{source.files ? ` · ${kb(source.bytes)}` : ''}</span>
      </div>
      <div className="px-6 pb-2 text-[11px] opacity-80 leading-relaxed">
        {isServer
          ? 'Dieser Ordner liegt auf dem Server. Claude liest dort direkt, es gibt nichts abzugleichen.'
          : single
            ? 'Eine einzelne Datei von diesem Computer. Sie ist mit dem Anlegen freigegeben und lässt sich nur sperren.'
            : 'Ein Ordner von diesem Computer. Kopien kommen erst beim Abgleich auf den Server. Dateien lassen sich nur über ihren Ordner freigeben, einzelne Dateien nur sperren.'}
      </div>

      <div className="scroll-glass flex-1 min-h-0 px-6 pb-5 border-t border-white/30 pt-3">
        {note && (
          <div className="mb-3 px-2 py-1.5 rounded text-[12px]" style={{ background: note.bad ? 'rgba(255,120,130,0.3)' : 'rgba(120,255,180,0.2)' }}>{note.text}</div>
        )}

        {!isServer && placeOk && <SyncPanel source={source} ask={ask} onSynced={async () => { await loadStored(null); await refreshTree(); await onChanged() }} />}
        {!isServer && !placeOk && (
          <div className="mb-4 px-2 py-1.5 rounded text-[12px]" style={{ background: 'rgba(255,120,130,0.3)' }}>
            {inApp ? 'Der Ort dieser Quelle auf diesem Computer ist nicht erreichbar. Ordne ihn in der Liste neu zu, dann sind Baum und Abgleich wieder da.' : 'Baum und Abgleich gibt es nur in der App.'}
          </div>
        )}

        {placeOk && (
          <Section title={single ? 'Datei' : 'Ordner und Freigaben'}>
            {!isServer && !single && (
              <div data-testid="root-rule" className="flex items-center gap-2 py-1.5 border-b border-white/10 text-[12px]">
                <span className="flex-1 min-w-0">
                  <b>Ganzer Ordner</b>
                  <span className="opacity-80">
                    {' '}
                    {source.root_rule === undefined
                      ? 'lässt sich erst freigeben, wenn der Server aktualisiert ist'
                      : source.root_rule === 'allow'
                      ? '✓ erlaubt, auch die Dateien direkt im Hauptordner'
                      : source.root_rule === 'deny'
                        ? '✗ gesperrt, nichts daraus darf angehängt werden'
                        : 'nicht freigegeben'}
                  </span>
                </span>
                {source.root_rule === null && <button className="gbtn primary" onClick={() => void putRule('', 'allow')}>Ganzen Ordner freigeben</button>}
                {source.root_rule === 'allow' && <button className="gbtn" onClick={() => void putRule('', 'clear')}>Zurücksetzen</button>}
                {(source.root_rule === null || source.root_rule === 'allow') && <button className="gbtn danger" onClick={() => void putRule('', 'deny')}>Alles sperren</button>}
                {source.root_rule === 'deny' && <button className="gbtn" onClick={() => void putRule('', 'clear')}>Sperre aufheben</button>}
              </div>
            )}
            {renderLevel('', 0, levels[''])}
          </Section>
        )}

        {!isServer && (
          <Section title="Kopien auf dem Server">
            {storedError && <div className="text-[12px] mb-2" style={{ color: '#ffc9cd' }}>{storedError}</div>}
            {stored === null && !storedError && <div className="text-[12px] opacity-70">Lade …</div>}
            {stored && stored.length === 0 && <div className="text-[12px] opacity-80">Noch nichts auf dem Server. Gib einen Ordner frei und gleiche dann ab.</div>}
            {stored?.map((f) => (
              <div key={f.path + f.version} className="py-1 border-b border-white/10 text-[12px]">
                <div className="flex gap-2">
                  <span className="flex-1 min-w-0 truncate">📄 {f.path}</span>
                  <span className="whitespace-nowrap text-[11px]">{stateLabel(f)}</span>
                </div>
                <div className="text-[11px] opacity-65">
                  Version {f.version}{f.id ? ` · Kennung ${f.id}` : ''} · {kb(f.size)} · {new Date(f.uploaded_at * 1000).toLocaleString('de-DE', { dateStyle: 'short', timeStyle: 'short' })}
                </div>
              </div>
            ))}
            {cursor && <button className="gbtn mt-2" onClick={() => void loadStored(cursor)}>Weitere laden</button>}
          </Section>
        )}
      </div>
    </div>
  )
}
