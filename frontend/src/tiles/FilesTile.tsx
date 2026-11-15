import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import type { AskConfirm } from '../core/types'
import { Win95Modal } from '../components/Win95Window'
import {
  asText, countText, inApp, kb, local, problem, server,
  type LocalView, type Picked, type Quota, type Source,
} from './filesApi'
import { SourceView } from './SourceView'

// Files: the sources Claude may attach files from. A source is a folder or a single file on this
// computer (copied to the server only when you synchronise) or the notes folder on the server.

function Group({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-4">
      <div className="text-[11px] uppercase tracking-widest opacity-90 py-1.5 border-b border-white/30 mb-2">{title}</div>
      {children}
    </section>
  )
}

function NameDialog({
  title,
  intro,
  initial,
  confirmLabel,
  onSubmit,
  onClose,
}: {
  title: string
  intro: ReactNode
  initial: string
  confirmLabel: string
  onSubmit: (name: string) => Promise<string | null>
  onClose: () => void
}) {
  const [name, setName] = useState(initial)
  const [error, setError] = useState('')
  const [busy, setBusy] = useState(false)
  const submit = async () => {
    setBusy(true)
    setError('')
    const problemText = await onSubmit(name.trim())
    setBusy(false)
    if (problemText) setError(problemText)
  }
  return (
    <Win95Modal title={title} width={420} onClose={onClose}>
      <div className="text-xs mb-3 leading-relaxed">{intro}</div>
      <input
        className="ginput w-full mb-2"
        value={name}
        autoFocus
        maxLength={60}
        onChange={(e) => setName(e.target.value)}
        onKeyDown={(e) => e.key === 'Enter' && name.trim() && !busy && void submit()}
      />
      {error && <div className="text-[12px] mb-2" style={{ color: '#a01020' }}>{error}</div>}
      <div className="flex justify-end gap-2">
        <button className="bevel" onClick={onClose}>Abbrechen</button>
        <button className="bevel font-bold" disabled={busy || !name.trim()} onClick={() => void submit()}>{confirmLabel}</button>
      </div>
    </Win95Modal>
  )
}

type Dialog =
  | { type: 'add'; picked: Picked }
  | { type: 'rename'; source: Source }
  | null

export function FilesTile({ ask }: { ask: AskConfirm }) {
  const [sources, setSources] = useState<Source[] | null>(null)
  const [quota, setQuota] = useState<Quota | null>(null)
  const [locals, setLocals] = useState<LocalView[]>([])
  const [error, setError] = useState('')
  const [note, setNote] = useState<{ text: string; bad: boolean } | null>(null)
  const [dialog, setDialog] = useState<Dialog>(null)
  const [openId, setOpenId] = useState<string | null>(null)

  const load = useCallback(async () => {
    setError('')
    const r = await server('GET', '/admin/sources')
    if (r.status !== 200) return setError(problem(r))
    setSources(r.data.sources as Source[])
    setQuota(r.data.quota as Quota)
    if (inApp) {
      try {
        setLocals(await local.list())
      } catch (e) {
        setNote({ text: asText(e), bad: true })
      }
    }
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const localOf = (id: string) => locals.find((l) => l.server_id === id)

  const pick = async (kind: 'folder' | 'file'): Promise<Picked | null> => {
    try {
      return await local.pick(kind)
    } catch (e) {
      setNote({ text: asText(e), bad: true })
      return null
    }
  }

  const startAdd = async (kind: 'folder' | 'file') => {
    setNote(null)
    const picked = await pick(kind)
    if (picked) setDialog({ type: 'add', picked })
  }

  // Creates the source on the server, then links this computer's place to it. If linking fails the new,
  // empty source is removed again, so nothing is left half-done.
  const create = async (picked: Picked, label: string): Promise<string | null> => {
    const r = await server('POST', '/admin/sources', {
      kind: 'upload',
      scope: picked.kind,
      label,
      ...(picked.kind === 'file' ? { file_name: picked.name } : {}),
    })
    if (r.status !== 201) return problem(r)
    const id = r.data.source.id as string
    try {
      await local.link(picked.token, id)
    } catch (e) {
      await server('POST', `/admin/sources/${id}/remove`, {})
      return asText(e)
    }
    setDialog(null)
    setNote({ text: `Die Quelle „${label}“ ist angelegt. Es ist noch nichts freigegeben und nichts auf den Server kopiert.`, bad: false })
    await load()
    return null
  }

  const rename = async (source: Source, label: string): Promise<string | null> => {
    const r = await server('POST', `/admin/sources/${source.id}/rename`, { label })
    if (r.status !== 200) return problem(r)
    setDialog(null)
    await load()
    return null
  }

  const relink = async (source: Source) => {
    setNote(null)
    const picked = await pick(source.scope)
    if (!picked) return
    try {
      await local.link(picked.token, source.id)
      setNote({ text: `„${source.label}“ zeigt jetzt auf den neu gewählten Ort. Die Kopien auf dem Server bleiben, bis du abgleichst.`, bad: false })
      await load()
    } catch (e) {
      setNote({ text: asText(e), bad: true })
    }
  }

  const remove = (source: Source) =>
    ask({
      title: 'Quelle entfernen?',
      body: (
        <>
          Die Quelle <b>{source.label}</b> wird entfernt. Alle Kopien davon auf dem Server werden gelöscht, offene Entwürfe mit Dateien aus dieser Quelle werden verworfen. Dateien auf diesem Computer bleiben unberührt.
        </>
      ),
      confirmLabel: 'Quelle entfernen',
      run: () =>
        void (async () => {
          const r = await server('POST', `/admin/sources/${source.id}/remove`, {})
          if (r.status !== 200) return setNote({ text: problem(r), bad: true })
          if (inApp) {
            try {
              await local.unlink(source.id)
            } catch {
              /* the server source is gone; a stale local entry is harmless and listed nowhere */
            }
          }
          const files = Number(r.data.purged_files) || 0
          const drafts = Number(r.data.cancelled_drafts) || 0
          setNote({
            text: `Entfernt. ${countText(files)} auf dem Server gelöscht${drafts ? `, ${drafts} ${drafts === 1 ? 'offener Entwurf' : 'offene Entwürfe'} verworfen` : ''}.`,
            bad: false,
          })
          await load()
        })(),
    })

  const serverFolders = (sources ?? []).filter((s) => s.kind === 'server_folder')
  const macSources = (sources ?? []).filter((s) => s.kind === 'upload')
  const full = quota ? quota.sources >= quota.max_sources : false

  const card = (s: Source) => {
    const place = localOf(s.id)
    const isMac = s.kind === 'upload'
    return (
      <div key={s.id} className="py-2 border-b border-white/10">
        <div className="flex items-center gap-2">
          <span className="text-[13px] font-semibold flex-1 min-w-0 truncate">
            {isMac ? (s.scope === 'file' ? '📄' : '📁') : '🗄️'} {s.label}
            {s.scope === 'file' && s.file_name ? <span className="font-normal opacity-70"> · {s.file_name}</span> : null}
          </span>
          <span className="text-[11px] opacity-80 whitespace-nowrap">{countText(s.files)}{s.files ? ` · ${kb(s.bytes)}` : ''}</span>
        </div>
        {isMac && inApp && (
          <div className="text-[11px] mt-0.5" style={{ color: place && place.reachable ? undefined : '#ffc9cd' }}>
            {!place
              ? 'Auf diesem Computer ist kein Ort für diese Quelle hinterlegt.'
              : !place.reachable
                ? `Nicht erreichbar: ${place.display}`
                : <span className="opacity-80">{place.display}</span>}
          </div>
        )}
        <div className="flex gap-2 mt-1.5">
          <button className="gbtn primary" onClick={() => setOpenId(s.id)}>Öffnen</button>
          {isMac && <>
            {inApp && (!place || !place.reachable) && <button className="gbtn" onClick={() => void relink(s)}>Neu zuordnen</button>}
            <button className="gbtn" onClick={() => setDialog({ type: 'rename', source: s })}>Umbenennen</button>
            <button className="gbtn danger" onClick={() => remove(s)}>Entfernen</button>
          </>}
        </div>
      </div>
    )
  }

  const opened = sources?.find((x) => x.id === openId)
  if (opened) {
    return (
      <SourceView
        source={opened}
        linked={localOf(opened.id)}
        ask={ask}
        onBack={() => setOpenId(null)}
        onChanged={load}
      />
    )
  }

  return (
    <div className="tile-text flex-1 min-h-0 flex flex-col">
      <div className="px-6 pb-2 text-[12px] opacity-90 leading-relaxed">
        Hier legst du fest, aus welchen Ordnern und Dateien Claude etwas an Mails anhängen darf. Claude sieht nur, was du ausdrücklich freigibst. Eine Sperre gewinnt immer.
      </div>

      <div className="scroll-glass flex-1 min-h-0 px-6 pb-5 border-t border-white/30 pt-3">
        {error && (
          <div className="mb-3 px-2 py-1.5 rounded text-[12px]" style={{ background: 'rgba(255,120,130,0.3)' }}>
            {error} <button className="gbtn ml-2" onClick={() => void load()}>Erneut laden</button>
          </div>
        )}
        {note && (
          <div className="mb-3 px-2 py-1.5 rounded text-[12px]" style={{ background: note.bad ? 'rgba(255,120,130,0.3)' : 'rgba(120,255,180,0.2)' }}>
            {note.text}
          </div>
        )}
        {!sources && !error && <div className="opacity-70 text-[12px]">Lade …</div>}

        {sources && (
          <>
            {serverFolders.length > 0 && (
              <Group title="Ordner auf dem Server">{serverFolders.map(card)}</Group>
            )}
            <Group title="Von diesem Computer">
              {macSources.length === 0 && (
                <div className="text-[12px] opacity-80 mb-2">Noch keine Quelle von diesem Computer. Wähle einen Ordner oder eine einzelne Datei.</div>
              )}
              {macSources.map(card)}
              {inApp ? (
                <div className="flex gap-2 mt-3">
                  <button className="gbtn primary" disabled={full} onClick={() => void startAdd('folder')}>Ordner hinzufügen</button>
                  <button className="gbtn" disabled={full} onClick={() => void startAdd('file')}>Einzelne Datei hinzufügen</button>
                </div>
              ) : (
                <div className="text-[12px] opacity-80 mt-2">Ordner und Dateien von diesem Computer lassen sich nur in der App auswählen, nicht im Browser.</div>
              )}
              {quota && (
                <div className="text-[11px] opacity-65 mt-2">
                  {quota.sources} von höchstens {quota.max_sources} Quellen angelegt.{full ? ' Entferne eine Quelle, um eine neue anzulegen.' : ''}
                </div>
              )}
            </Group>
          </>
        )}
      </div>

      {dialog?.type === 'add' && (
        <NameDialog
          title={dialog.picked.kind === 'folder' ? 'Ordner als Quelle anlegen' : 'Datei als Quelle anlegen'}
          intro={
            <>
              Unter diesem Namen sieht Claude die Quelle. Gewählt: <b>{dialog.picked.name}</b>. Der Ort auf deinem Computer bleibt hier gespeichert und geht nie an den Server. Angelegt wird nur der Name, freigegeben wird noch nichts.
            </>
          }
          initial={dialog.picked.name.replace(/\.(md|txt)$/i, '')}
          confirmLabel="Quelle anlegen"
          onSubmit={(name) => create(dialog.picked, name)}
          onClose={() => setDialog(null)}
        />
      )}
      {dialog?.type === 'rename' && (
        <NameDialog
          title="Quelle umbenennen"
          intro="Unter dem neuen Namen sieht Claude die Quelle."
          initial={dialog.source.label}
          confirmLabel="Umbenennen"
          onSubmit={(name) => rename(dialog.source, name)}
          onClose={() => setDialog(null)}
        />
      )}
    </div>
  )
}
