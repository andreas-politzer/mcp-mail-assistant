import { useCallback, useEffect, useRef, useState } from 'react'
import { admin } from '../api/admin'

// Activity: the audit log, newest first. It contains actions, never mail text, subjects or file contents.
// The server groups its events into types; the labels below are only for reading.

interface Entry {
  seq: number
  ts: number
  event: string
  draft_id: string | null
  by: string | null
  address: string | null
  detail: string | null
}

const GROUPS: { id: string; label: string; dot: string }[] = [
  { id: 'recipients', label: 'Empfänger', dot: '#7cc4ff' },
  { id: 'files', label: 'Dateien', dot: '#ffd166' },
  { id: 'sources', label: 'Quellen', dot: '#f4a261' },
  { id: 'sends', label: 'Versand', dot: '#7cf2a0' },
  { id: 'limits', label: 'Limits', dot: '#ff9e9e' },
  { id: 'settings', label: 'Einstellungen', dot: '#d0a6ff' },
  { id: 'access', label: 'Anmeldung', dot: '#d0d0d0' },
]

const RANGES: { id: string; label: string; since: () => number | null }[] = [
  { id: 'all', label: 'Alles', since: () => null },
  { id: 'hour', label: 'Letzte Stunde', since: () => Date.now() / 1000 - 3600 },
  { id: 'today', label: 'Heute', since: () => startOfDay(0) },
  { id: 'yesterday', label: 'Seit gestern', since: () => startOfDay(1) },
  { id: 'week', label: 'Letzte 7 Tage', since: () => Date.now() / 1000 - 7 * 86400 },
]
function startOfDay(daysBack: number): number {
  const d = new Date()
  d.setHours(0, 0, 0, 0)
  d.setDate(d.getDate() - daysBack)
  return d.getTime() / 1000
}

const LABEL: Record<string, string> = {
  recipient_added: 'Empfänger hinzugefügt',
  recipient_approved: 'Empfänger freigegeben',
  recipient_rejected: 'Vorschlag abgelehnt',
  recipient_blocked: 'Empfänger gesperrt',
  recipient_unblocked: 'Sperre aufgehoben',
  recipient_removed: 'Empfänger entfernt',
  recipient_suggested: 'Vorschlag von Claude',
  recipient_expired: 'Vorschlag abgelaufen',
  recipient_imported: 'Kontakte importiert',
  recipient_csv_imported: 'CSV-Import durchgeführt',
  file_rule_set: 'Dateiregel geändert',
  source_rule_set: 'Quellenregel geändert',
  stored_file_stored: 'Datei auf dem Server gespeichert',
  stored_file_removed: 'Gespeicherte Datei entfernt',
  stored_files_purged: 'Gesperrte Dateien gelöscht',
  source_created: 'Dateiquelle hinzugefügt',
  source_renamed: 'Dateiquelle umbenannt',
  source_removed: 'Dateiquelle entfernt',
  source_synced: 'Dateiquelle aktualisiert',
  send_limits_set: 'Versandlimits geändert',
  send_limits_start: 'Startwerte der Limits gemeldet',
  setting_changed: 'Einstellung geändert',
  admin_login: 'Anmeldung',
  admin_login_failed: 'Anmeldung fehlgeschlagen',
  admin_login_locked: 'Anmeldung gesperrt',
  admin_logout: 'Abmeldung',
  prepared: 'Entwurf vorbereitet',
  claimed: 'Versand begonnen',
  sent: 'Mail gesendet',
  failed: 'Versand fehlgeschlagen',
  unknown: 'Versand unklar',
  expired: 'Entwurf abgelaufen',
  cancelled: 'Entwurf verworfen',
  rate_limited: 'Versandlimit erreicht',
  draft_cancelled_by_block: 'Entwurf wegen Sperre verworfen',
  draft_copies_deleted: 'Dateikopien eines Entwurfs gelöscht',
}

const SEND_EVENTS = new Set(['prepared', 'claimed', 'sent', 'failed', 'unknown', 'expired', 'cancelled', 'rate_limited'])
function groupOf(event: string): string {
  if (event.startsWith('recipient_')) return 'recipients'
  if (event === 'file_rule_set' || event === 'source_rule_set' || event.startsWith('stored_file')) return 'files'
  if (event.startsWith('source_')) return 'sources'
  if (event.startsWith('send_limits_')) return 'limits'
  if (event === 'setting_changed') return 'settings'
  if (event.startsWith('admin_')) return 'access'
  if (SEND_EVENTS.has(event) || event.startsWith('draft_')) return 'sends'
  return 'access'
}
const dotOf = (event: string) => GROUPS.find((g) => g.id === groupOf(event))?.dot ?? '#d0d0d0'

const PAGE = 50
const NO_ANSWER = 'Der Server antwortet nicht. Läuft der Tunnel noch?'

export function ActivityTile() {
  const [picked, setPicked] = useState<Set<string>>(new Set())
  const [range, setRange] = useState('all')
  const [entries, setEntries] = useState<Entry[]>([])
  const [more, setMore] = useState<number | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [loaded, setLoaded] = useState(false)
  const run = useRef(0)

  const fetchPage = useCallback(
    async (before: number | null) => {
      const id = ++run.current
      setLoading(true)
      setError('')
      const params = new URLSearchParams({ limit: String(PAGE) })
      if (picked.size) params.set('type', [...picked].join(','))
      const since = RANGES.find((r) => r.id === range)?.since()
      if (since !== null && since !== undefined) params.set('since', String(Math.floor(since)))
      if (before !== null) params.set('before', String(before))
      try {
        const r = await admin.call('GET', `/admin/audit?${params}`)
        if (id !== run.current) return
        if (r.status !== 200) {
          setError(r.data?.message ?? `Der Server meldet Fehler ${r.status}.`)
        } else {
          const got: Entry[] = r.data.entries ?? []
          setEntries((old) => (before === null ? got : [...old, ...got]))
          setMore(r.data.next_before ?? null)
        }
      } catch {
        if (id === run.current) setError(NO_ANSWER)
      } finally {
        if (id === run.current) {
          setLoading(false)
          setLoaded(true)
        }
      }
    },
    [picked, range],
  )

  // Every change of a filter reads the list again from the top.
  useEffect(() => {
    void fetchPage(null)
  }, [fetchPage])

  const toggle = (id: string) =>
    setPicked((old) => {
      const next = new Set(old)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })

  const when = (ts: number) =>
    new Date(ts * 1000).toLocaleString('de-DE', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit' })

  return (
    <div className="p-4 text-white h-full flex flex-col text-[13px]">
      <div className="flex flex-wrap items-center gap-1.5 mb-2">
        <button className={`gbtn ${picked.size === 0 ? 'on' : ''}`} onClick={() => setPicked(new Set())}>Alle</button>
        {GROUPS.map((g) => (
          <button key={g.id} className={`gbtn ${picked.has(g.id) ? 'on' : ''}`} onClick={() => toggle(g.id)}>
            <span style={{ color: g.dot }}>●</span> {g.label}
          </button>
        ))}
      </div>
      <div className="flex items-center gap-2 mb-2">
        <select className="ginput" value={range} onChange={(e) => setRange(e.target.value)} aria-label="Zeitraum">
          {RANGES.map((r) => (
            <option key={r.id} value={r.id} style={{ color: '#000' }}>{r.label}</option>
          ))}
        </select>
        <button className="gbtn" onClick={() => void fetchPage(null)} disabled={loading}>Aktualisieren</button>
        <span className="ml-auto text-[11px] opacity-65">Nur Aktionen, keine Mailtexte, keine Betreffzeilen.</span>
      </div>

      {error && (
        <div className="mb-2 text-[12px]" style={{ color: '#ffb3b3' }} role="alert">{error}</div>
      )}

      <div className="flex-1 min-h-0 overflow-y-auto">
        {loaded && !error && entries.length === 0 && <div className="opacity-70 text-[12px] py-3">Keine Einträge für diese Auswahl.</div>}
        {entries.map((e) => (
          <div key={e.seq} className="flex gap-2 py-1.5 border-b border-white/10 leading-tight">
            <span className="mt-0.5" style={{ color: dotOf(e.event) }} aria-hidden>●</span>
            <div className="flex-1 min-w-0">
              <div className="flex gap-2 items-baseline">
                <span className="font-semibold">{LABEL[e.event] ?? e.event.replace(/_/g, ' ')}</span>
                <span className="ml-auto text-[11px] opacity-65 shrink-0">{when(e.ts)}</span>
              </div>
              {(e.address || e.detail) && (
                <div className="text-[11px] opacity-75 truncate" title={[e.address, e.detail].filter(Boolean).join(' · ')}>
                  {[e.address, e.detail].filter(Boolean).join(' · ')}
                </div>
              )}
            </div>
          </div>
        ))}
        {more !== null && (
          <div className="py-2 text-center">
            <button className="gbtn" onClick={() => void fetchPage(more)} disabled={loading}>{loading ? 'Lade …' : 'Ältere laden'}</button>
          </div>
        )}
      </div>
    </div>
  )
}
