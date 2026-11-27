import { t, useLang } from '../i18n'
import { useState } from 'react'
import type { AskConfirm } from '../core/types'
import { asText, countText, kb, local, problem, server, type ScanOut, type SentFile, type Source } from './filesApi'

// The manual synchronisation of one source from this computer, in the order that keeps the server safe:
// read everything (any problem aborts), ask the server for a plan, show it, and only after a clear
// yes send the files in passes. The server removes copies only in the last pass.

interface Plan {
  confirmation_id: string
  summary: { new: number; changed: number; removed: number; unchanged: number }
  items: { path: string; change: 'new' | 'changed' | 'removed'; size: number }[]
  items_truncated: boolean
  rejected: { path: string; reason: string }[]
  rejected_count: number
  passes: number
  next_pass: string[]
}
type Phase =
  | { name: 'idle' }
  | { name: 'scanning' }
  | { name: 'planned'; plan: Plan; scan: ScanOut }
  | { name: 'applying'; pass: number; passes: number; sent: number }
  | { name: 'done'; stored: number; removed: number; unchanged: number; skipped: { path: string; reason: string }[] }
  | { name: 'error'; text: string }

const REJECT_TEXT = (reason: string): string => {
  if (reason === 'Blocked.') return 'gesperrt'
  if (reason === 'Not in an allowed folder of this source.') return 'liegt nicht in einem freigegebenen Ordner'
  if (reason.startsWith('Too large')) return 'zu groß, höchstens 200 KB'
  if (reason === 'Listed twice.') return 'kommt doppelt vor'
  return reason
}
const CHANGE: Record<string, string> = { new: 'neu', changed: 'geändert', removed: 'wird vom Server gelöscht' }

const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))

export function SyncPanel({ source, onSynced }: { source: Source; ask: AskConfirm; onSynced: () => Promise<void> }) {
  useLang()
  const [phase, setPhase] = useState<Phase>({ name: 'idle' })
  const [ack, setAck] = useState(false)

  const start = async () => {
    setAck(false)
    setPhase({ name: 'scanning' })
    let scan: ScanOut
    try {
      scan = await local.scan(source.id)
    } catch (e) {
      return setPhase({ name: 'error', text: `${asText(e)} ${t('Auf dem Server wurde nichts verändert.')}` })
    }
    const r = await server('POST', `/admin/sources/${source.id}/sync/plan`, { manifest: scan.entries })
    if (r.status !== 200) return setPhase({ name: 'error', text: `${problem(r)} ${t('Auf dem Server wurde nichts verändert.')}` })
    setPhase({ name: 'planned', plan: r.data as Plan, scan })
  }

  // The same request is repeated when the answer got lost; the server recognises it and does nothing twice.
  const postApply = async (cid: string, files: SentFile[]) => {
    let r = await server('POST', `/admin/sources/${source.id}/sync/apply`, { confirmation_id: cid, files })
    for (let attempt = 0; r.status === 0 && attempt < 2; attempt++) {
      await sleep(1500)
      r = await server('POST', `/admin/sources/${source.id}/sync/apply`, { confirmation_id: cid, files })
    }
    return r
  }

  const apply = async (plan: Plan, scan: ScanOut) => {
    let cid = plan.confirmation_id
    let next = plan.next_pass
    let pass = 1
    const total = { stored: 0, removed: 0, unchanged: 0 }
    const skipped: { path: string; reason: string }[] = []
    const fail = (text: string) =>
      setPhase({ name: 'error', text: `${text} ${t('Was bis hierher übertragen wurde, bleibt auf dem Server. Gelöscht wird erst im letzten Durchgang, bisher wurde also nichts gelöscht. Gleiche erneut ab, um fortzufahren.')}` })
    let sent = 0
    for (;;) {
      setPhase({ name: 'applying', pass, passes: plan.passes, sent })
      let files: SentFile[] = []
      if (next.length) {
        try {
          files = await local.read(source.id, scan.scan_id, next)
        } catch (e) {
          return fail(asText(e))
        }
      }
      const r = await postApply(cid, files)
      if (r.status !== 200) return fail(problem(r))
      total.stored += Number(r.data.stored) || 0
      total.removed += Number(r.data.removed) || 0
      total.unchanged += Number(r.data.unchanged) || 0
      skipped.push(...((r.data.skipped as { path: string; reason: string }[]) ?? []))
      sent += files.length
      if (r.data.status === 'done') break
      cid = r.data.confirmation_id
      next = r.data.next_pass ?? []
      pass += 1
    }
    setPhase({ name: 'done', ...total, skipped })
    await onSynced()
  }

  if (phase.name === 'idle' || phase.name === 'done' || phase.name === 'error') {
    return (
      <div className="mb-4">
        {phase.name === 'done' && (
          <div data-testid="sync-done" className="mb-2 px-2 py-1.5 rounded text-[12px]" style={{ background: 'rgba(120,255,180,0.2)' }}>
            {t('Abgleich fertig: {stored} neu oder geändert gespeichert, {removed} gelöscht, {unchanged} unverändert.', { stored: phase.stored, removed: phase.removed, unchanged: phase.unchanged })}
            {phase.skipped.length > 0 && (
              <details className="mt-1">
                <summary className="cursor-pointer">{t('{count} übersprungen', { count: countText(phase.skipped.length) })}</summary>
                <div className="opacity-85">{t('Diese Dateien wurden nicht gespeichert und erscheinen beim nächsten Abgleich wieder als neu, bis sie zulässig sind.')}</div>
                <ul className="list-disc ml-5">{phase.skipped.map((s) => <li key={s.path}>{s.path}: {s.reason}</li>)}</ul>
              </details>
            )}
            {(phase.stored > 0 || phase.removed > 0) && <div className="mt-1 opacity-85">{t('Ein Entwurf mit einer geänderten oder gelöschten Datei muss neu vorbereitet werden.')}</div>}
          </div>
        )}
        {phase.name === 'error' && <div data-testid="sync-error" className="mb-2 px-2 py-1.5 rounded text-[12px]" style={{ background: 'rgba(255,120,130,0.3)' }}>{phase.text}</div>}
        <button className="gbtn primary" onClick={() => void start()}>{t('Mit diesem Computer abgleichen')}</button>
        <span className="text-[11px] opacity-70 ml-2">{t('Du siehst vorher, was passiert.')}</span>
      </div>
    )
  }

  if (phase.name === 'scanning') return <div className="mb-4 text-[12px] opacity-85">{t('Lese die Quelle auf diesem Computer und frage den Server nach einem Plan …')}</div>

  if (phase.name === 'applying') {
    return (
      <div className="mb-4 text-[12px]">
        <div className="mb-1">{t('Übertrage Durchgang {pass} von {passes} …', { pass: phase.pass, passes: phase.passes })}</div>
        <div className="h-3 rounded bg-white/20 overflow-hidden"><div className="h-full" style={{ width: `${Math.round(((phase.pass - 1) / Math.max(phase.passes, 1)) * 100)}%`, background: '#8fe3b0' }} /></div>
        <div className="mt-1 opacity-70">{t('Bitte das Fenster nicht schließen.')}</div>
      </div>
    )
  }

  const { plan, scan } = phase
  const s = plan.summary
  const nothing = s.new + s.changed + s.removed === 0
  // What the server holds now, from the plan itself (not from the list on screen, which may be a moment old).
  const serverFiles = s.removed + s.changed + s.unchanged
  const emptyRead = scan.empty && serverFiles > 0
  const mass = s.removed > 0 && s.removed * 2 >= serverFiles
  const needsAck = emptyRead || mass
  const notSent = plan.rejected_count

  return (
    <div className="mb-4 p-3 rounded" style={{ background: 'rgba(255,255,255,0.12)' }}>
      <div className="font-semibold text-[13px] mb-1">{t('Vorschau des Abgleichs')}</div>
      {nothing ? (
        <div className="text-[12px]">{s.unchanged === 1 ? t('Alles ist aktuell: 1 Datei ist unverändert, nichts ist zu tun.') : t('Alles ist aktuell: {n} Dateien sind unverändert, nichts ist zu tun.', { n: s.unchanged })}</div>
      ) : (
        <div className="text-[12px]" data-testid="sync-summary">
          <b>{s.new}</b> {t('neu')}, <b>{s.changed}</b> {t('geändert')}, <b>{s.removed}</b> {s.removed === 1 ? t('wird vom Server gelöscht') : t('werden vom Server gelöscht')}, {s.unchanged} {t('unverändert')}.
          {plan.passes > 1 && <> {t('Die Übertragung läuft in {n} Durchgängen.', { n: plan.passes })}</>}
        </div>
      )}

      {plan.items.length > 0 && (
        <details className="mt-2 text-[12px]" open={plan.items.length <= 8}>
          <summary className="cursor-pointer">{t('Einzelne Dateien')}</summary>
          <ul className="ml-4 mt-1">
            {plan.items.map((i) => (
              <li key={i.change + i.path} className="flex gap-2">
                <span className="flex-1 min-w-0 truncate">{i.path}</span>
                <span className="whitespace-nowrap opacity-80" style={{ color: i.change === 'removed' ? '#ffc9cd' : undefined }}>{t(CHANGE[i.change])} · {kb(i.size)}</span>
              </li>
            ))}
          </ul>
          {plan.items_truncated && <div className="opacity-70 mt-1">{t('Die Liste zeigt nicht alle Dateien.')}</div>}
        </details>
      )}

      {notSent > 0 && (
        <details className="mt-2 text-[12px]">
          <summary className="cursor-pointer">{notSent === 1 ? t('{count} wird nicht übertragen', { count: countText(notSent) }) : t('{count} werden nicht übertragen', { count: countText(notSent) })}</summary>
          <div className="opacity-80">{t('Das sind Dateien, die gesperrt oder nicht freigegeben sind oder die die Grenzen überschreiten.')}</div>
          <ul className="list-disc ml-5 mt-1">
            {plan.rejected.map((x) => <li key={x.path}>{x.path}: {t(REJECT_TEXT(x.reason))}</li>)}
          </ul>
          {notSent > plan.rejected.length && <div className="opacity-70">{t('Die Liste zeigt nicht alle.')}</div>}
        </details>
      )}
      {scan.skipped.length > 0 && (
        <div className="mt-1 text-[11px] opacity-70">{scan.skipped.length === 1 ? t('1 Eintrag auf dem Computer wurde übersprungen, zum Beispiel Verknüpfungen.') : t('{n} Einträge auf dem Computer wurden übersprungen, zum Beispiel Verknüpfungen.', { n: scan.skipped.length })}</div>
      )}

      {needsAck && (
        <label className="flex gap-2 items-start mt-3 px-2 py-2 rounded text-[12px]" style={{ background: 'rgba(255,120,130,0.3)' }}>
          <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-0.5" />
          <span>
            {emptyRead
              ? <>{t('Auf diesem Computer wurde keine einzige Textdatei gefunden, auf dem Server liegen aber')} <b>{serverFiles}</b>. {t('Dieser Abgleich würde')} <b>{t('alle')}</b> {t('Kopien löschen. Stimmt das?')}</>
              : <>{t('Dieser Abgleich löscht')} <b>{s.removed}</b> {t('von')} <b>{serverFiles}</b> {t('Kopien auf dem Server. Stimmt das?')}</>}
            {' '}{t('Ja, diese Kopien sollen gelöscht werden.')}
          </span>
        </label>
      )}

      <div className="flex gap-2 mt-3">
        {!nothing && <button className="gbtn primary" disabled={needsAck && !ack} onClick={() => void apply(plan, scan)}>{t('Abgleich ausführen')}</button>}
        <button className="gbtn" onClick={() => setPhase({ name: 'idle' })}>{nothing ? t('Schließen') : t('Abbrechen')}</button>
      </div>
    </div>
  )
}
