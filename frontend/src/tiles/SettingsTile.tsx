import { LangSwitch } from '../i18n/LangSwitch'
import { locale, t, useLang } from '../i18n'
import { useCallback, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { admin } from '../api/admin'
import type { AskConfirm } from '../core/types'

// Settings: send limits, how long suggestions stay open, and a read-only security overview.
// All numbers come from the server; nothing here is a built-in default.

interface Limits {
  per_hour: number | null
  per_day: number | null
  source: string
  starting_values: { per_hour: number; per_day: number } | null
  ceiling: { per_hour: number; per_day: number }
  used: { last_hour: number; last_day: number }
}
interface Expiry {
  value: number | null
  source: string
  starting_value: number | null
  min: number
  max: number
  default: number
  open_candidates: number
}

// A sentence about where the numbers come from; empty when they were set here.
const whereFrom = (source: string): string =>
  source === 'mail_service_settings'
    ? ' ' + t('Diese Werte stammen aus der Konfiguration des Mail-Dienstes. Sobald du hier speicherst, gelten deine Werte.')
    : source === 'unknown'
      ? ' ' + t('Der aktuelle Wert ist noch nicht bekannt. Trage einen Wert ein und speichere ihn.')
      : ''
const noAnswer = () => t('Der Server antwortet nicht. Läuft der Tunnel noch?')

async function ask_server(method: string, path: string, body?: unknown) {
  try {
    return await admin.call(method, path, body)
  } catch {
    return { status: 0, data: { message: noAnswer() } }
  }
}
const problem = (r: { status: number; data: { message?: string } }) => (r.data?.message ? t(r.data.message) : r.status === 0 ? noAnswer() : t('Der Server meldet Fehler {status}.', { status: r.status }))

const hours = (s?: number) => (s ? t('{n} Stunden', { n: Math.round(s / 3600) }) : '–')
const CAN: Record<string, string> = {
  'list the allowed recipients': 'die freigegebenen Empfänger ansehen',
  'prepare a draft (nothing is sent)': 'Entwürfe vorbereiten, ohne etwas zu senden',
  'send a prepared draft after your explicit yes': 'einen vorbereiteten Entwurf senden, nachdem du im Chat ausdrücklich Ja gesagt hast',
  'cancel a draft': 'Entwürfe verwerfen',
  'suggest a recipient (you decide outside the chat)': 'neue Empfänger vorschlagen, über die du in dieser App entscheidest',
  'list the files you allowed': 'die von dir erlaubten Dateien auflisten',
}
const CANNOT: Record<string, string> = {
  'approve, reject or block a recipient': 'Empfänger freigeben, ablehnen oder sperren',
  'change folder or file rules': 'Ordner- oder Dateiregeln ändern',
  'change send limits or settings': 'Limits oder Einstellungen ändern',
  'read mail': 'Mails lesen',
  'attach a file that is blocked or not allowed': 'gesperrte oder nicht erlaubte Dateien anhängen',
}

function Info({ title, children }: { title: string; children: ReactNode }) {
  return (
    <div>
      <div className="font-semibold">{title}</div>
      <div className="opacity-85">{children}</div>
    </div>
  )
}

function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section className="mb-5">
      <div className="text-[11px] uppercase tracking-widest opacity-90 py-1.5 border-b border-white/30 mb-2">{title}</div>
      {children}
    </section>
  )
}

function Num({ label, value, set, max, min = 0 }: { label: string; value: string; set: (v: string) => void; max: number; min?: number }) {
  return (
    <label className="flex items-center gap-2 text-[12px]">
      <span className="w-40">{label}</span>
      <input className="ginput w-20" inputMode="numeric" value={value} onChange={(e) => set(e.target.value.replace(/\D/g, ''))} />
      <span className="opacity-60">{t('{min} bis {max}', { min, max })}</span>
    </label>
  )
}

export function SettingsTile({ ask }: { ask: AskConfirm }) {
  useLang()
  const [limits, setLimits] = useState<Limits | null>(null)
  const [expiry, setExpiry] = useState<Expiry | null>(null)
  const [security, setSecurity] = useState<Record<string, any> | null>(null) // eslint-disable-line @typescript-eslint/no-explicit-any
  const [loadError, setLoadError] = useState('')
  const [hour, setHour] = useState('')
  const [day, setDay] = useState('')
  const [days, setDays] = useState('')
  const [limitNote, setLimitNote] = useState<{ text: string; bad: boolean } | null>(null)
  const [expiryNote, setExpiryNote] = useState<{ text: string; bad: boolean } | null>(null)

  const fillLimits = (l: Limits) => {
    setLimits(l)
    setHour(l.per_hour === null ? '' : String(l.per_hour))
    setDay(l.per_day === null ? '' : String(l.per_day))
  }
  const fillExpiry = (e: Expiry) => {
    setExpiry(e)
    setDays(e.value === null ? '' : String(e.value))
  }

  const load = useCallback(async () => {
    setLoadError('')
    const [l, e, s] = await Promise.all([ask_server('GET', '/admin/limits'), ask_server('GET', '/admin/settings'), ask_server('GET', '/admin/security')])
    if (l.status !== 200) return setLoadError(problem(l))
    fillLimits(l.data as Limits)
    if (e.status === 200) fillExpiry(e.data.candidate_expiry_days as Expiry)
    if (s.status === 200) setSecurity(s.data)
  }, [])
  useEffect(() => {
    void load()
  }, [load])

  // ---- send limits ----
  const putLimits = async (perHour: number, perDay: number, confirmation?: string) => {
    const r = await ask_server('PUT', '/admin/limits', { per_hour: perHour, per_day: perDay, ...(confirmation ? { confirmation_id: confirmation } : {}) })
    if (r.status !== 200) {
      setLimitNote({ text: problem(r), bad: true })
      if (r.status === 409) void load()
      return
    }
    if (r.data.status === 'confirmation_required') {
      const p = r.data.preview ?? {}
      ask({
        title: t('Versandlimits erhöhen?'),
        body: <>{t('Claude darf dann mehr Mails pro Zeitraum vorbereiten und senden. Gesendet wird weiterhin nur, wenn du im Chat Ja sagst.')} {t('Pro Stunde:')} <b>{p.per_hour}</b>, {t('pro Tag:')} <b>{p.per_day}</b>.</>,
        confirmLabel: t('Limits erhöhen'),
        run: () => void putLimits(perHour, perDay, r.data.confirmation_id),
      })
      return
    }
    fillLimits(r.data as Limits)
    setLimitNote({ text: r.data.status === 'unchanged' ? t('Keine Änderung.') : t('Gespeichert.'), bad: false })
  }
  const saveLimits = () => {
    if (!limits) return
    const h = Number(hour)
    const d = Number(day)
    if (hour === '' || day === '') return setLimitNote({ text: t('Bitte beide Werte angeben.'), bad: true })
    if (h > limits.ceiling.per_hour || d > limits.ceiling.per_day) return setLimitNote({ text: t('Höchstens {hour} pro Stunde und {day} pro Tag.', { hour: limits.ceiling.per_hour, day: limits.ceiling.per_day }), bad: true })
    setLimitNote(null)
    void putLimits(h, d)
  }

  // ---- candidate expiry ----
  const putExpiry = async (value: number, confirmation?: string) => {
    const r = await ask_server('PUT', '/admin/settings', { candidate_expiry_days: value, ...(confirmation ? { confirmation_id: confirmation } : {}) })
    if (r.status !== 200) {
      setExpiryNote({ text: problem(r), bad: true })
      if (r.status === 409) void load()
      return
    }
    if (r.data.status === 'confirmation_required') {
      ask({
        title: t('Frist verlängern?'),
        body: <>{t('Nicht bestätigte Empfänger bleiben dann länger offen. Tage bis zum Verfall, alt → neu:')} <b>{String(r.data.preview?.candidate_expiry_days ?? '').replace('->', '→')}</b>.</>,
        confirmLabel: t('Frist verlängern'),
        run: () => void putExpiry(value, r.data.confirmation_id),
      })
      return
    }
    fillExpiry(r.data.candidate_expiry_days as Expiry)
    const soon = Number(r.data.candidates_expiring_soon) || 0
    setExpiryNote({
      text: r.data.status === 'unchanged' ? t('Keine Änderung.') : soon > 0 ? (soon === 1 ? t('Gespeichert. {n} nicht bestätigter Empfänger verfällt dadurch bald.', { n: soon }) : t('Gespeichert. {n} nicht bestätigte Empfänger verfallen dadurch bald.', { n: soon })) : t('Gespeichert.'),
      bad: false,
    })
  }
  const saveExpiry = () => {
    if (!expiry) return
    const v = Number(days)
    if (days === '' || v < expiry.min || v > expiry.max) return setExpiryNote({ text: t('Bitte eine ganze Zahl von {min} bis {max}.', { min: expiry.min, max: expiry.max }), bad: true })
    setExpiryNote(null)
    void putExpiry(v)
  }

  const stopped = limits !== null && limits.per_hour === 0 && limits.per_day === 0
  const note = (n: { text: string; bad: boolean } | null) => n && <div className="text-[12px] mt-2" style={{ color: n.bad ? '#ffb3b3' : '#b6ffd2' }} role={n.bad ? 'alert' : 'status'}>{n.text}</div>
  const sess = admin.sessionInfo()
  const ad = security?.admin_access
  const mins = (s?: number) => (s ? `${Math.round(s / 60)} min` : '–')

  return (
    <div className="p-4 text-white overflow-y-auto h-full text-[13px]">
      {loadError && (
        <div className="mb-3 text-[12px]" style={{ color: '#ffb3b3' }} role="alert">
          {loadError} <button className="gbtn ml-2" onClick={() => void load()}>{t('Erneut laden')}</button>
        </div>
      )}

      <Section title={t('Sprache')}>
        <LangSwitch />
      </Section>

      <Section title={t('Versandlimits')}>
        {!limits ? (
          <div className="opacity-70 text-[12px]">{t('Lade …')}</div>
        ) : (
          <>
            {stopped && <div className="mb-2 px-2 py-1 rounded text-[12px]" style={{ background: 'rgba(255,120,130,0.3)' }}>{t('Der Versand ist gestoppt. Beide Werte stehen auf 0.')}</div>}
            <div className="text-[12px] opacity-85 mb-2">
              {limits.per_hour === null || limits.per_day === null ? t('Es ist noch nicht bekannt, welche Limits gelten.') : <>{t('Es gelten')} <b>{limits.per_hour}</b> {t('Mails pro Stunde und')} <b>{limits.per_day}</b> {t('Mails pro Tag.')}</>}{whereFrom(limits.source)}
              {' '}{t('Zuletzt gesendet: {hour} in der letzten Stunde und {day} in den letzten 24 Stunden.', { hour: limits.used.last_hour, day: limits.used.last_day })}
            </div>
            <div className="flex flex-col gap-1.5">
              <Num label={t('E-Mails pro Stunde')} value={hour} set={setHour} max={limits.ceiling.per_hour} />
              <Num label={t('E-Mails pro Tag')} value={day} set={setDay} max={limits.ceiling.per_day} />
            </div>
            <div className="flex gap-2 mt-3">
              <button className="gbtn primary" onClick={saveLimits}>{t('Speichern')}</button>
              <button className="gbtn danger" onClick={() => void putLimits(0, 0)} disabled={stopped}>{t('Versand stoppen')}</button>
            </div>
            <div className="text-[11px] opacity-65 mt-2">{t('0 schaltet den Versand ab. Erhöhen fragt nach, Senken gilt sofort.')}{limits.starting_values && ` ${t('Startwerte des Mail-Dienstes: {hour} pro Stunde, {day} pro Tag.', { hour: limits.starting_values.per_hour, day: limits.starting_values.per_day })}`}</div>
            {note(limitNote)}
          </>
        )}
      </Section>

      <Section title={t('Nicht bestätigte Empfänger')}>
        {!expiry ? (
          <div className="opacity-70 text-[12px]">{t('Lade …')}</div>
        ) : (
          <>
            <div className="text-[12px] opacity-85 mb-2">
              {t('Schlägt Claude einen neuen Kontakt vor oder bringt ein CSV-Import ihn mit, ist er erst einmal')} <b>{t('nicht freigegeben')}</b>{t(': Du entscheidest, ob er freigegeben oder abgelehnt wird. Entscheidest du nicht,')} <b>{t('verfällt der Vorschlag nach {days} Tagen', { days: expiry.value ?? '?' })}</b>{t('. So sammelt Claude nicht von allein immer mehr Empfänger an.')}{whereFrom(expiry.source)}
            </div>
            <div className="text-[12px] mb-2">
              {expiry.open_candidates === 0 ? t('Gerade wartet niemand auf deine Entscheidung.') : expiry.open_candidates === 1 ? <>{t('Gerade wartet')} <b>{expiry.open_candidates}</b> {t('Empfänger auf deine Entscheidung.')}</> : <>{t('Gerade warten')} <b>{expiry.open_candidates}</b> {t('Empfänger auf deine Entscheidung.')}</>}
            </div>
            <Num label={t('Tage bis zum Verfall')} value={days} set={setDays} min={expiry.min} max={expiry.max} />
            <div className="flex gap-2 mt-3">
              <button className="gbtn primary" onClick={saveExpiry}>{t('Speichern')}</button>
            </div>
            <div className="text-[11px] opacity-65 mt-2">{t('Eine längere Frist fragt nach, eine kürzere gilt sofort, und du siehst, wie viele Vorschläge dadurch bald verfallen. Vorgabe: {days} Tage.', { days: expiry.default })}</div>
            {note(expiryNote)}
          </>
        )}
      </Section>

      <details className="mb-5 settings-details">
        <summary className="cursor-pointer list-none text-[11px] uppercase tracking-widest opacity-90 py-1.5 border-b border-white/30 flex items-center gap-2">
          <span className="arrow inline-block w-3">▸</span>
          <span>{t('Systemstatus & Sicherheit')}</span>
          <span className="ml-auto normal-case tracking-normal text-[11px] opacity-70">{security ? t('angemeldet · nur lokal erreichbar') : t('lädt …')}</span>
        </summary>
        {!security ? (
          <div className="opacity-70 text-[12px] mt-2">{t('Lade …')}</div>
        ) : (
          <div className="text-[12px] leading-relaxed flex flex-col gap-3 mt-3">
            <Info title={t('Deine Anmeldung')}>
              {sess ? t('Angemeldet seit {time} Uhr.', { time: new Date(sess.since).toLocaleTimeString(locale(), { hour: '2-digit', minute: '2-digit' }) }) : t('Angemeldet.')} {t('Nach {idle} ohne Benutzung meldet der Server dich ab, spätestens nach {max}. Lädst du das Fenster neu oder schließt es, ist die Anmeldung sofort weg.', { idle: mins(ad?.idle_timeout_seconds), max: hours(ad?.max_session_seconds) })}
            </Info>
            <Info title={t('Wer kommt an die Verwaltung?')}>
              {t('Nur dieser Computer, und von anderen Geräten nur durch den SSH-Tunnel. Nach {tries} falschen Passphrasen ist die Anmeldung für {lock} gesperrt. Änderungen, die mehr erlauben, fragen immer nach; eine Bestätigung gilt {secs} Sekunden.', { tries: ad?.failed_sign_ins_before_lockout ?? '?', lock: mins(ad?.lockout_seconds), secs: ad?.confirmation_valid_seconds ?? '?' })}
            </Info>
            <Info title={t('Dateien')}>
              {t('Erlaubt sind {upload}-Dateien. Aus Serverordnern werden nur {folder}-Dateien angeboten. Bis zu {sources} Quellen mit je {files} Dateien, jede höchstens {kb} KB, zusammen {mb} MB.', { upload: (security.files?.types?.upload ?? []).join(t(' und ')), folder: (security.files?.types?.server_folder ?? []).join(', '), sources: security.files?.quotas?.max_sources ?? '?', files: security.files?.quotas?.max_files_per_source ?? '?', kb: Math.round((security.files?.quotas?.max_file_bytes ?? 0) / 1000), mb: Math.round((security.files?.quotas?.max_total_bytes ?? 0) / 1_000_000) })} {security.files?.active_sources === 1 ? t('Jetzt aktiv: {n} Quelle.', { n: 1 }) : t('Jetzt aktiv: {n} Quellen.', { n: security.files?.active_sources ?? '?' })}
            </Info>
            <Info title={t('Harte Obergrenzen')}>
              {t('Mehr als {hour} Mails pro Stunde oder {day} pro Tag sind nie möglich, egal was oben eingestellt ist.', { hour: security.mail_limits?.hard_ceiling_per_hour ?? '?', day: security.mail_limits?.hard_ceiling_per_day ?? '?' })}
            </Info>
            <details>
              <summary className="cursor-pointer opacity-90">{t('Was Claude darf und was nicht')}</summary>
              <div className="mt-1 font-semibold">{t('Darf:')}</div>
              <ul className="list-disc ml-5 opacity-85">{(security.claude_can ?? []).map((k: string) => <li key={k}>{t(CAN[k] ?? k)}</li>)}</ul>
              <div className="mt-1 font-semibold">{t('Darf nicht:')}</div>
              <ul className="list-disc ml-5 opacity-85">{(security.claude_cannot ?? []).map((k: string) => <li key={k}>{t(CANNOT[k] ?? k)}</li>)}</ul>
            </details>
            <details>
              <summary className="cursor-pointer opacity-90">{t('Technische Details')}</summary>
              <div className="mt-1 opacity-85">
                {t('Erlaubte Hosts: {hosts}. Erlaubte Herkünfte: {origins}. Datenbank: Version {version}.', { hosts: (ad?.allowed_hosts ?? []).join(', ') || '–', origins: (ad?.allowed_origins ?? []).join(', ') || '–', version: security.database?.schema_version ?? '?' })}
              </div>
            </details>
            <div className="text-[11px] opacity-60">{t('Passphrase, Sitzungsschlüssel und Hashes werden hier nie angezeigt.')}</div>
          </div>
        )}
      </details>
    </div>
  )
}
