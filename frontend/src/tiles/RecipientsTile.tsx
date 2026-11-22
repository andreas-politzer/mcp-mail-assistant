import { useCallback, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import { parseContacts, type Candidate } from '../logic/csv'
import type { AskConfirm } from '../core/types'
import { problem, server } from './filesApi'

// Recipients: who Claude may prepare mails for. Everything here is read from and written to the admin API.
// Candidates from a contact file are never approved automatically.

export interface Recipient {
  id: number
  name: string
  address: string
  aliases: string[]
  status: 'suggested' | 'active' | 'rejected' | 'removed' | 'expired' | 'blocked'
  note: string
  suggested_by: string | null
}

interface ImportRow {
  row: number
  name: string
  address: string
  category: 'new' | 'known' | 'candidate' | 'excluded' | 'invalid' | 'duplicate'
  reason: string
}
interface ImportPlan {
  confirmation_id: string
  summary: Record<ImportRow['category'], number>
  rows: ImportRow[]
  rows_truncated: boolean
  passes: number
}

const ADMIN_SOURCE = 'admin:web'
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const SHOW_LIMIT = 100
export const IMPORT_MAX_ROWS = 2000

const CATEGORY_TEXT: Record<ImportRow['category'], string> = {
  new: 'neu',
  known: 'schon freigegeben',
  candidate: 'wartet schon auf deine Entscheidung',
  excluded: 'früher abgelehnt, entfernt oder gesperrt, kommt nie wieder als Kandidat',
  invalid: 'ungültig',
  duplicate: 'steht doppelt in der Datei',
}

export function RecipientsTile({ ask }: { ask: AskConfirm }) {
  const [items, setItems] = useState<Recipient[] | null>(null)
  const [loadError, setLoadError] = useState('')
  const [note, setNote] = useState<{ text: string; bad: boolean } | null>(null)
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [name, setName] = useState('')
  const [address, setAddress] = useState('')
  const [error, setError] = useState('')
  const [dragging, setDragging] = useState(false)
  const [busy, setBusy] = useState(false)
  const [plan, setPlan] = useState<{ file: string; rows: Candidate[]; plan: ImportPlan } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  const load = useCallback(async () => {
    const r = await server('GET', '/admin/recipients')
    if (r.status !== 200) {
      setLoadError(problem(r))
      return
    }
    setLoadError('')
    setItems(r.data.recipients as Recipient[])
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  const all = items ?? []
  const isCandidate = (r: Recipient) => r.status === 'suggested' && r.suggested_by === ADMIN_SOURCE
  const proposals = all.filter((r) => r.status === 'suggested' && !isCandidate(r))
  const active = all.filter((r) => r.status === 'active')
  const blocked = all.filter((r) => r.status === 'blocked')
  const cands = all.filter(isCandidate)

  const q = query.trim().toLowerCase()
  const match = (r: Recipient) =>
    !q || r.name.toLowerCase().includes(q) || r.address.toLowerCase().includes(q) || r.aliases.some((a) => a.toLowerCase().includes(q))

  const toggleSection = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  // One change on one recipient. Returns the server's text on failure.
  const act = async (r: Recipient, action: 'approve' | 'reject' | 'block' | 'remove' | 'unblock'): Promise<string | null> => {
    let res = await server('POST', `/admin/recipients/${r.id}/${action}`, action === 'approve' || action === 'unblock' ? {} : undefined)
    // Lifting a block has a second step on the server. The person has already confirmed in the window.
    if (action === 'unblock' && res.status === 200 && res.data.status === 'confirmation_required') {
      res = await server('POST', `/admin/recipients/${r.id}/unblock`, { confirmation_id: res.data.confirmation_id })
    }
    // Lifting a block brings the address back to the approved ones, as an external address (mail to it still needs
    // the passkey). The server itself only removes the block; the window adds the address again, which the server
    // records as two separate steps.
    if (action === 'unblock' && res.status === 200) {
      const added = await server('POST', '/admin/recipients', { name: r.name, address: r.address, aliases: r.aliases })
      return added.status === 201 || added.status === 200 ? null : problem(added)
    }
    return res.status === 200 ? null : problem(res)
  }
  const change = async (r: Recipient, action: 'approve' | 'reject' | 'block' | 'remove' | 'unblock', done: string) => {
    const failed = await act(r, action)
    setNote(failed ? { text: failed, bad: true } : { text: done, bad: false })
    await load()
  }

  const approve = (r: Recipient) =>
    ask({
      title: 'Empfänger freigeben?',
      body: <>Claude darf dann Mails an <b>{r.name}</b> mit der Adresse {r.address} vorbereiten. Gesendet wird weiterhin nur, wenn du im Chat zustimmst.</>,
      confirmLabel: 'Freigeben',
      run: () => void change(r, 'approve', `${r.name} ist freigegeben.`),
    })
  const unblock = (r: Recipient) =>
    ask({
      title: 'Sperre aufheben?',
      body: <>Die Adresse <b>{r.address}</b> ist danach nicht mehr gesperrt und steht wieder bei den Freigegebenen, als fremde Adresse (Mails dorthin brauchen deinen Passkey).</>,
      confirmLabel: 'Sperre aufheben',
      run: () => void change(r, 'unblock', 'Die Sperre ist aufgehoben, die Adresse ist wieder freigegeben.'),
    })
  const block = (r: Recipient) =>
    ask({
      title: 'Adresse sperren?',
      body: <>Die Adresse <b>{r.address}</b> wird für immer gesperrt. Claude kann sie nicht mehr vorschlagen, und sie kommt auch über eine Kontaktdatei nicht wieder.</>,
      confirmLabel: 'Sperren',
      run: () => void change(r, 'block', 'Die Adresse ist gesperrt.'),
    })
  const remove = (r: Recipient) =>
    ask({
      title: 'Empfänger entfernen?',
      body: <>Claude darf danach keine Mails mehr an <b>{r.name}</b> vorbereiten. Die Adresse kommt nicht wieder als Kandidat. Du kannst sie später selbst wieder hinzufügen.</>,
      confirmLabel: 'Entfernen',
      run: () => void change(r, 'remove', `${r.name} ist entfernt.`),
    })

  const add = () => {
    if (!name.trim()) return setError('Ein Empfänger braucht einen Namen.')
    if (!EMAIL.test(address.trim())) return setError('Das ist keine gültige Adresse.')
    const n = name.trim()
    const a = address.trim()
    const existing = all.find((r) => r.address.toLowerCase() === a.toLowerCase())
    if (existing?.status === 'blocked') return setError('Diese Adresse ist gesperrt. Hebe zuerst die Sperre auf.')
    if (existing?.status === 'active') return setError('Diese Adresse gibt es schon.')
    setError('')
    ask({
      title: 'Empfänger hinzufügen?',
      body: <>Du gibst <b>{n}</b> mit der Adresse {a} direkt frei. Claude darf dann Mails dorthin vorbereiten.</>,
      confirmLabel: 'Hinzufügen',
      run: () =>
        void (async () => {
          const r = await server('POST', '/admin/recipients', { name: n, address: a, aliases: [] })
          if (r.status !== 201) return setNote({ text: problem(r), bad: true })
          setName('')
          setAddress('')
          setNote({ text: `${n} ist freigegeben.`, bad: false })
          await load()
        })(),
    })
  }

  // ---- contact file: read here, checked by the server, candidates only ----
  const readFile = async (file: File | undefined) => {
    if (!file || busy) return
    const parsed = parseContacts(await file.text(), new Set(), IMPORT_MAX_ROWS)
    if (parsed.note && parsed.rows.length === 0) return setNote({ text: parsed.note, bad: true })
    if (parsed.rows.length === 0) return setNote({ text: 'In der Datei steht keine gültige Adresse.', bad: true })
    setBusy(true)
    const r = await server('POST', '/admin/recipients/import/plan', { rows: parsed.rows })
    setBusy(false)
    if (r.status !== 200) return setNote({ text: problem(r), bad: true })
    setNote(parsed.note ? { text: parsed.note, bad: false } : null)
    setPlan({ file: file.name, rows: parsed.rows, plan: r.data as ImportPlan })
  }

  const runImport = async () => {
    if (!plan) return
    setBusy(true)
    let cid: string | undefined = plan.plan.confirmation_id
    let total = 0
    let pass = 0
    for (;;) {
      pass += 1
      let r = await server('POST', '/admin/recipients/import/apply', { confirmation_id: cid })
      if (r.status === 0) r = await server('POST', '/admin/recipients/import/apply', { confirmation_id: cid }) // a lost answer is repeated once, the server does not store twice
      if (r.status !== 200) {
        setBusy(false)
        setPlan(null)
        setNote({
          text: `${problem(r)} Bisher wurden ${total} angelegt. Beim nächsten Import werden schon angelegte Adressen übersprungen.`,
          bad: true,
        })
        await load()
        return
      }
      total = Number(r.data.total_added) || total
      if (r.data.status === 'done') break
      cid = r.data.confirmation_id
    }
    setBusy(false)
    setPlan(null)
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.delete('cand')
      return next
    })
    setNote({ text: `${total} ${total === 1 ? 'Kandidat' : 'Kandidaten'} angelegt. Noch nichts davon ist freigegeben. Entscheide unten in der Liste.`, bad: false })
    await load()
  }

  // ---- decisions on candidates: one request each, so every decision is logged by the server ----
  const decide = async (list: Recipient[], action: 'approve' | 'reject') => {
    setBusy(true)
    let ok = 0
    const failures: string[] = []
    for (const r of list) {
      const failed = await act(r, action)
      if (failed) failures.push(`${r.address}: ${failed}`)
      else ok += 1
    }
    setBusy(false)
    setPicked(new Set())
    const verb = action === 'approve' ? 'freigegeben' : 'abgelehnt'
    setNote({
      text: `${ok} ${ok === 1 ? 'Adresse' : 'Adressen'} ${verb}.${failures.length ? ` ${failures.length} nicht: ${failures.slice(0, 3).join(' ')}${failures.length > 3 ? ' …' : ''}` : ''}`,
      bad: failures.length > 0,
    })
    await load()
  }
  const approveCands = (list: Recipient[]) =>
    ask({
      title: 'Importierte Kontakte freigeben?',
      body: <>Claude darf dann Mails an <b>{list.length}</b> {list.length === 1 ? 'Adresse' : 'Adressen'} vorbereiten. Gesendet wird weiterhin nur nach deiner Zustimmung im Chat.</>,
      confirmLabel: `${list.length} freigeben`,
      run: () => void decide(list, 'approve'),
    })
  const rejectCands = (list: Recipient[]) =>
    ask({
      title: 'Kontakte ablehnen?',
      body: <>{list.length === 1 ? 'Diese Adresse wird' : `Diese ${list.length} Adressen werden`} abgelehnt und kommen über eine Kontaktdatei nie wieder als Kandidat.</>,
      confirmLabel: `${list.length} ablehnen`,
      run: () => void decide(list, 'reject'),
    })

  const visibleCands = cands.filter(match)
  const pickedList = cands.filter((c) => picked.has(c.id))

  const header = (key: string, title: string, count: number) => (
    <button className="w-full flex items-center gap-2 text-left text-[11px] uppercase tracking-widest opacity-90 py-1.5 border-b border-white/30" onClick={() => toggleSection(key)}>
      <span className="w-3">{collapsed.has(key) ? '▸' : '▾'}</span>
      <span>{title}</span>
      <span className="ml-auto opacity-80">{count}</span>
    </button>
  )

  const row = (r: Recipient, buttons: ReactNode) => (
    <div key={r.id} data-testid={`recipient-${r.address}`} className="flex items-center gap-2 py-1 border-b border-white/10">
      <div className="flex-1 min-w-0 leading-tight">
        <div className="text-[13px] font-semibold truncate">
          {r.name}
          {r.aliases.length > 0 && <span className="ml-1.5 text-[11px] font-normal opacity-70">{r.aliases.map((a) => `„${a}“`).join(' ')}</span>}
        </div>
        <div className="text-[11px] opacity-75 truncate">{r.address}</div>
        {r.note && <div className="text-[11px] opacity-70 truncate">{r.note}</div>}
      </div>
      {buttons}
    </div>
  )
  const limited = (rows: Recipient[], buttons: (r: Recipient) => ReactNode) => (
    <>
      {rows.slice(0, SHOW_LIMIT).map((r) => row(r, buttons(r)))}
      {rows.length > SHOW_LIMIT && <div className="text-xs opacity-70 py-1">… und {rows.length - SHOW_LIMIT} weitere. Nutze die Suche.</div>}
    </>
  )

  if (items === null) {
    return (
      <div className="tile-text flex-1 min-h-0 px-6 text-[13px]">
        {loadError ? (
          <>
            <div className="mb-2" style={{ color: '#ffc9cd' }}>{loadError}</div>
            <button className="gbtn" onClick={() => void load()}>Erneut laden</button>
          </>
        ) : (
          'Lade …'
        )}
      </div>
    )
  }

  const empty = (text: string) => <div className="text-xs opacity-60 italic py-1.5">{text}</div>

  return (
    <div
      className="tile-text flex-1 min-h-0 flex flex-col relative"
      onDragOver={(e) => {
        if (e.dataTransfer.types.includes('Files')) {
          e.preventDefault()
          setDragging(true)
        }
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        void readFile(e.dataTransfer.files[0])
      }}
    >
      <div className="px-6 pb-2 flex gap-2 items-center">
        <input className="field flex-1 min-w-0" placeholder="🔍 Suchen nach Name oder Adresse" value={query} onChange={(e) => setQuery(e.target.value)} />
        <button className="gbtn" disabled={busy} onClick={() => fileRef.current?.click()}>CSV importieren…</button>
        <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" className="hidden" onChange={(e) => { void readFile(e.target.files?.[0]); e.target.value = '' }} />
      </div>
      {note && (
        <div className="mx-6 mb-2 px-2 py-1.5 rounded text-[12px]" style={{ background: note.bad ? 'rgba(255,120,130,0.3)' : 'rgba(120,255,180,0.2)' }}>{note.text}</div>
      )}

      {plan && (
        <div data-testid="import-plan" className="mx-6 mb-2 p-3 rounded" style={{ background: 'rgba(255,255,255,0.12)' }}>
          <div className="font-semibold text-[13px] mb-1">Vorschau des Imports aus {plan.file}</div>
          <div className="text-[12px]" data-testid="import-summary">
            <b>{plan.plan.summary.new}</b> neu als Kandidat, {plan.plan.summary.known} schon freigegeben, {plan.plan.summary.candidate} warten schon,{' '}
            {plan.plan.summary.excluded} kommen nie wieder, {plan.plan.summary.invalid} ungültig, {plan.plan.summary.duplicate} doppelt.
          </div>
          {plan.plan.rows.some((r) => r.category !== 'new') && (
            <details className="text-[11px] mt-1.5">
              <summary className="cursor-pointer">Was nicht übernommen wird</summary>
              <div className="mt-1 max-h-32 overflow-y-auto">
                {plan.plan.rows.filter((r) => r.category !== 'new').map((r) => (
                  <div key={r.row} className="py-px">Zeile {r.row}: {r.address || 'ohne Adresse'}, {r.category === 'invalid' ? r.reason : CATEGORY_TEXT[r.category]}</div>
                ))}
                {plan.plan.rows_truncated && <div className="opacity-70">Weitere Zeilen werden hier nicht gezeigt.</div>}
              </div>
            </details>
          )}
          <div className="text-[11px] opacity-85 mt-1.5">
            Die neuen Adressen werden nur Kandidaten und sind noch nicht freigegeben. Solange Kandidaten offen sind, pausieren Claudes eigene Vorschläge.
          </div>
          <div className="flex gap-2 mt-2">
            <button className="gbtn primary" disabled={busy || plan.plan.summary.new === 0} onClick={() => void runImport()}>
              {busy ? 'Läuft …' : `${plan.plan.summary.new} als Kandidaten anlegen`}
            </button>
            <button className="gbtn" disabled={busy} onClick={() => setPlan(null)}>Abbrechen</button>
          </div>
        </div>
      )}

      <div className="scroll-glass flex-1 min-h-0 px-6">
        {header('new', 'Neu, noch nicht freigegeben', proposals.length)}
        {!collapsed.has('new') &&
          (proposals.filter(match).length
            ? limited(proposals.filter(match), (r) => (
                <>
                  <button className="gbtn primary" onClick={() => approve(r)}>Freigeben</button>
                  <button className="gbtn" onClick={() => void change(r, 'reject', 'Der Vorschlag ist abgelehnt.')}>Ablehnen</button>
                  <button className="gbtn danger" onClick={() => block(r)}>Sperren</button>
                </>
              ))
            : empty('Nichts offen.'))}

        {header('active', 'Freigegeben', active.length)}
        {!collapsed.has('active') &&
          (active.filter(match).length
            ? limited(active.filter(match), (r) => (
                <>
                  <button className="gbtn danger" onClick={() => block(r)}>Sperren</button>
                  <button className="gbtn" onClick={() => remove(r)}>Entfernen</button>
                </>
              ))
            : empty('Nichts gefunden.'))}

        {header('blocked', 'Gesperrt', blocked.length)}
        {!collapsed.has('blocked') &&
          (blocked.filter(match).length
            ? limited(blocked.filter(match), (r) => <button className="gbtn" onClick={() => unblock(r)}>Sperre aufheben</button>)
            : empty('Nichts gesperrt.'))}

        {header('cand', 'Importierte Kontakte, Kandidaten', cands.length)}
        {!collapsed.has('cand') && (
          <div className="pb-2">
            {cands.length === 0 ? (
              <div className="text-xs opacity-70 py-1.5">Noch nichts importiert. Ziehe eine CSV-Datei auf diese Kachel oder wähle sie oben aus.</div>
            ) : (
              <>
                <div className="text-[11px] opacity-80 py-1.5">
                  Kandidaten sind noch nicht freigegeben. Was du nicht entscheidest, verfällt nach 30 Tagen von selbst.
                </div>
                <div className="flex flex-wrap gap-2 pb-1.5">
                  <button className="gbtn" onClick={() => setPicked(new Set(visibleCands.map((c) => c.id)))}>Alle wählen</button>
                  <button className="gbtn" onClick={() => setPicked(new Set())}>Keine</button>
                  <button className="gbtn primary" disabled={busy || !pickedList.length} onClick={() => approveCands(pickedList)}>Ausgewählte freigeben {pickedList.length}</button>
                  <button className="gbtn danger" disabled={busy || !pickedList.length} onClick={() => rejectCands(pickedList)}>Ausgewählte ablehnen</button>
                </div>
                {visibleCands.slice(0, SHOW_LIMIT).map((c) => (
                  <label key={c.id} data-testid={`candidate-${c.address}`} className="flex items-center gap-2 py-1 border-b border-white/10 cursor-pointer">
                    <input
                      type="checkbox"
                      checked={picked.has(c.id)}
                      onChange={() =>
                        setPicked((prev) => {
                          const next = new Set(prev)
                          if (next.has(c.id)) next.delete(c.id)
                          else next.add(c.id)
                          return next
                        })
                      }
                    />
                    <span className="flex-1 min-w-0 leading-tight">
                      <span className="block text-[13px] truncate">{c.name}</span>
                      <span className="block text-[11px] opacity-75 truncate">{c.address}</span>
                    </span>
                  </label>
                ))}
                {visibleCands.length > SHOW_LIMIT && <div className="text-xs opacity-70 py-1">… und {visibleCands.length - SHOW_LIMIT} weitere. Nutze die Suche.</div>}
              </>
            )}
          </div>
        )}
      </div>

      <div className="px-6 pt-2 pb-5 border-t border-white/30">
        <div className="flex gap-2">
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" className="field flex-1 min-w-0" />
          <input value={address} onChange={(e) => setAddress(e.target.value)} placeholder="adresse@beispiel.de" className="field flex-[1.4] min-w-0" />
          <button className="gbtn primary" onClick={add}>＋ Hinzufügen</button>
        </div>
        {error && <div className="text-xs mt-1 text-red-100">{error}</div>}
      </div>

      {dragging && (
        <div className="absolute inset-3 rounded-2xl border-2 border-dashed border-white flex items-center justify-center text-lg font-bold bg-black/30 pointer-events-none">
          CSV-Datei hier ablegen
        </div>
      )}
    </div>
  )
}
