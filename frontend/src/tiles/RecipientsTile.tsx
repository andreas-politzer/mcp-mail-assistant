import { useRef, useState } from 'react'
import { MOCK_RECIPIENTS, type Recipient, type RecipientStatus } from '../mock/recipients'
import { parseContacts, sampleCsv, type Candidate } from '../logic/csv'
import type { AskConfirm } from '../core/types'

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/
const SHOW_LIMIT = 100

interface Cand extends Candidate {
  id: number
}

export function RecipientsTile({ ask }: { ask: AskConfirm }) {
  const [items, setItems] = useState<Recipient[]>(MOCK_RECIPIENTS)
  const [cands, setCands] = useState<Cand[]>([])
  const [picked, setPicked] = useState<Set<number>>(new Set())
  const [query, setQuery] = useState('')
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set())
  const [name, setName] = useState('')
  const [address, setAddress] = useState('')
  const [error, setError] = useState('')
  const [importNote, setImportNote] = useState('')
  const [dragging, setDragging] = useState(false)
  const fileRef = useRef<HTMLInputElement>(null)

  const q = query.trim().toLowerCase()
  const match = (r: { name: string; address: string; alias?: string }) =>
    !q || r.name.toLowerCase().includes(q) || r.address.toLowerCase().includes(q) || (r.alias ?? '').toLowerCase().includes(q)

  const patch = (id: number, p: Partial<Recipient>) => setItems((prev) => prev.map((r) => (r.id === id ? { ...r, ...p } : r)))
  const drop = (id: number) => setItems((prev) => prev.filter((r) => r.id !== id))
  const reject = (id: number) => patch(id, { status: 'rejected' })
  const remove = (id: number) => patch(id, { status: 'removed' })
  const toggleSection = (key: string) =>
    setCollapsed((prev) => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })

  const approve = (r: Recipient) =>
    ask({
      title: 'Empfänger freigeben?',
      body: <>Claude darf dann Mails an <b>{r.name}</b> ({r.address}) vorbereiten. Gesendet wird weiterhin nur, wenn du im Chat zustimmst.</>,
      confirmLabel: 'Freigeben',
      run: () => patch(r.id, { status: 'active' }),
    })

  const unblock = (r: Recipient) =>
    ask({
      title: 'Sperre aufheben?',
      body: <>Claude darf <b>{r.address}</b> danach wieder als Empfänger vorschlagen. Freigegeben ist die Adresse damit noch nicht.</>,
      confirmLabel: 'Sperre aufheben',
      run: () => drop(r.id),
    })

  const add = () => {
    if (!name.trim()) return setError('Ein Empfänger braucht einen Namen.')
    if (!EMAIL.test(address.trim())) return setError('Das ist keine gültige Adresse.')
    const existing = items.find((r) => r.address.toLowerCase() === address.trim().toLowerCase())
    if (existing?.status === 'blocked') return setError('Diese Adresse ist gesperrt. Hebe zuerst die Sperre auf.')
    if (existing && (existing.status === 'active' || existing.status === 'new')) return setError('Diese Adresse gibt es schon.')
    setError('')
    ask({
      title: 'Empfänger hinzufügen?',
      body: <>Du gibst <b>{name.trim()}</b> ({address.trim()}) direkt frei. Claude darf dann Mails dorthin vorbereiten.</>,
      confirmLabel: 'Hinzufügen',
      run: () => {
        setItems((prev) => [...prev.filter((r) => r.id !== existing?.id), { id: Date.now(), name: name.trim(), address: address.trim(), status: 'active' }])
        setName('')
        setAddress('')
      },
    })
  }

  // ---- CSV import: candidates are never approved automatically ----
  const loadCsv = (text: string) => {
    const known = new Set([...items.map((r) => r.address.toLowerCase()), ...cands.map((c) => c.address.toLowerCase())])
    const res = parseContacts(text, known)
    const base = Date.now()
    setCands((prev) => [...prev, ...res.rows.map((r, i) => ({ ...r, id: base + i }))])
    setCollapsed((prev) => {
      const next = new Set(prev)
      next.delete('cand')
      return next
    })
    setImportNote(
      res.note ??
        `${res.rows.length} Kontakte gelesen${res.invalid ? `, ${res.invalid} mit ungültiger Adresse übersprungen` : ''}${res.known ? `, ${res.known} schon bekannt, gesperrt oder doppelt übersprungen` : ''}. Noch nichts ist freigegeben.`,
    )
  }
  const readFile = (file: File | undefined) => {
    if (!file) return
    file.text().then(loadCsv)
  }

  const approveCands = (list: Cand[]) =>
    ask({
      title: 'Importierte Kontakte freigeben?',
      body: <>Claude darf dann Mails an <b>{list.length}</b> {list.length === 1 ? 'Adresse' : 'Adressen'} vorbereiten{list.length === 1 ? `: ${list[0].name} (${list[0].address})` : '.'} Gesendet wird weiterhin nur nach deiner Zustimmung im Chat.</>,
      confirmLabel: `${list.length} freigeben`,
      run: () => {
        const ids = new Set(list.map((c) => c.id))
        setItems((prev) => [...prev, ...list.map((c, i) => ({ id: Date.now() + i, name: c.name, address: c.address, status: 'active' as RecipientStatus }))])
        setCands((prev) => prev.filter((c) => !ids.has(c.id)))
        setPicked((prev) => new Set([...prev].filter((id) => !ids.has(id))))
      },
    })

  const visibleCands = cands.filter(match)
  const pickedList = cands.filter((c) => picked.has(c.id))
  const deleteUnpicked = () => {
    setCands((prev) => prev.filter((c) => picked.has(c.id)))
    setImportNote('Nicht gewählte Kontakte wurden gelöscht.')
  }

  const header = (key: string, title: string, count: number) => (
    <button className="w-full flex items-center gap-2 text-left text-[11px] uppercase tracking-widest opacity-90 py-1.5 border-b border-white/30" onClick={() => toggleSection(key)}>
      <span className="w-3">{collapsed.has(key) ? '▸' : '▾'}</span>
      <span>{title}</span>
      <span className="ml-auto opacity-80">{count}</span>
    </button>
  )

  const limited = (rows: Recipient[]) => (
    <>
      {rows.slice(0, SHOW_LIMIT).map(row)}
      {rows.length > SHOW_LIMIT && <div className="text-xs opacity-70 py-1">… und {rows.length - SHOW_LIMIT} weitere. Nutze die Suche.</div>}
    </>
  )

  const row = (r: Recipient) => (
    <div key={r.id} className="flex items-center gap-2 py-1 border-b border-white/10">
      <div className="flex-1 min-w-0 leading-tight">
        <div className="text-[13px] font-semibold truncate">
          {r.name}
          {r.alias && <span className="ml-1.5 text-[11px] font-normal opacity-70">„{r.alias}“</span>}
          {r.proposed && <span className="ml-1.5 text-[10px] font-normal px-1.5 py-px rounded bg-white/20">Vorschlag</span>}
        </div>
        <div className="text-[11px] opacity-75 truncate">{r.address}</div>
      </div>
      {r.status === 'new' && (
        <>
          <button className="gbtn primary" onClick={() => approve(r)}>Freigeben</button>
          <button className="gbtn" onClick={() => reject(r.id)}>Ablehnen</button>
          <button className="gbtn danger" onClick={() => patch(r.id, { status: 'blocked' })}>Sperren</button>
        </>
      )}
      {r.status === 'active' && (
        <>
          <button className="gbtn danger" onClick={() => patch(r.id, { status: 'blocked' })}>Sperren</button>
          <button className="gbtn" onClick={() => remove(r.id)}>Entfernen</button>
        </>
      )}
      {r.status === 'blocked' && <button className="gbtn" onClick={() => unblock(r)}>Sperre aufheben</button>}
    </div>
  )

  const by = (s: RecipientStatus) => items.filter((r) => r.status === s && match(r))
  const count = (s: RecipientStatus) => items.filter((r) => r.status === s).length

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
        readFile(e.dataTransfer.files[0])
      }}
    >
      <div className="px-6 pb-2 flex gap-2 items-center">
        <input className="field flex-1 min-w-0" placeholder="🔍 Suchen nach Name oder Adresse" value={query} onChange={(e) => setQuery(e.target.value)} />
        <button className="gbtn" onClick={() => fileRef.current?.click()}>CSV importieren…</button>
        <input ref={fileRef} type="file" accept=".csv,text/csv,text/plain" className="hidden" onChange={(e) => { readFile(e.target.files?.[0]); e.target.value = '' }} />
      </div>
      {importNote && <div className="px-6 pb-2 text-[11px] opacity-90">{importNote}</div>}

      <div className="scroll-glass flex-1 min-h-0 px-6">
        {header('new', 'Neu, noch nicht freigegeben', count('new'))}
        {!collapsed.has('new') && (by('new').length ? limited(by('new')) : <div className="text-xs opacity-60 italic py-1.5">Nichts offen.</div>)}

        {header('active', 'Freigegeben', count('active'))}
        {!collapsed.has('active') && (by('active').length ? limited(by('active')) : <div className="text-xs opacity-60 italic py-1.5">Nichts gefunden.</div>)}

        {header('blocked', 'Gesperrt', count('blocked'))}
        {!collapsed.has('blocked') && (by('blocked').length ? limited(by('blocked')) : <div className="text-xs opacity-60 italic py-1.5">Nichts gesperrt.</div>)}

        {header('cand', 'Importierte Kontakte (Kandidaten)', cands.length)}
        {!collapsed.has('cand') && (
          <div className="pb-2">
            {cands.length === 0 ? (
              <div className="text-xs opacity-70 py-1.5">
                Noch nichts importiert. Ziehe eine CSV-Datei auf diese Kachel oder{' '}
                <button className="underline" onClick={() => loadCsv(sampleCsv())}>lade eine Beispieldatei</button>.
              </div>
            ) : (
              <>
                <div className="text-[11px] opacity-80 py-1.5">
                  Kandidaten sind für Claude unsichtbar und noch nicht freigegeben. Nicht gewählte werden gelöscht, spätestens nach 30 Tagen automatisch.
                </div>
                <div className="flex flex-wrap gap-2 pb-1.5">
                  <button className="gbtn" onClick={() => setPicked(new Set(visibleCands.map((c) => c.id)))}>Alle wählen</button>
                  <button className="gbtn" onClick={() => setPicked(new Set())}>Keine</button>
                  <button className="gbtn primary" disabled={!pickedList.length} onClick={() => approveCands(pickedList)}>Ausgewählte freigeben ({pickedList.length})</button>
                  <button className="gbtn danger" onClick={deleteUnpicked}>Nicht gewählte löschen</button>
                </div>
                {visibleCands.slice(0, SHOW_LIMIT).map((c) => (
                  <label key={c.id} className="flex items-center gap-2 py-1 border-b border-white/10 cursor-pointer">
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
