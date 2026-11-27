import { t } from '../i18n'
import { useEffect, useRef, useState } from 'react'
import { admin, ADMIN_BASE, EXPECTED_SCHEMA, GENERIC_TUNNEL_COMMAND, TUNNEL_COMMAND } from '../api/admin'
import { ENVELOPE, PixelSprite } from '../components/PixelSprite'
import { LoginDialog } from './LoginDialog'

// Start sequence in three scenes. The checks are real and run from the first moment; the scenes only show them.
//   1  Power on   (about 0.6 s): black, a few lines.
//   2  System start (about 2.4 s): own sky, logo, progress bar. The bar never gets ahead of the real checks.
//   3  Welcome: sky and sign-in window. After sign-in the database is read, then the desktop appears.
// The short show is a deliberate minimum; any key or click skips it. Failures never wait: they show at once.

type Mark = 'ok' | 'warn' | 'fail'
type Scene = 'power' | 'start' | 'welcome'
type Block = null | 'tunnel' | 'status'
interface Chip {
  id: string
  label: string
  detail: string
  mark: Mark
}

const POWER_MS = 650
const START_MIN_MS = 2400
const PRE_CHECKS = 3 // window, storage, tunnel
const MONO = 'ui-monospace, "SF Mono", Menlo, Consolas, monospace'
const COLOR: Record<Mark, string> = { ok: '#7cf2a0', warn: '#ffd166', fail: '#ff6b6b' }
const TAG: Record<Mark, string> = { ok: '[ OK ]', warn: '[ HINWEIS ]', fail: '[ FEHLT ]' }
const inTauri = typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
const SHADOW = '2px 2px 0 rgba(10,36,106,0.85)'
const FRAME = {
  background: 'var(--face)',
  border: '2px solid',
  borderColor: 'var(--bevel-light) var(--bevel-dark) var(--bevel-dark) var(--bevel-light)',
  boxShadow: '1px 1px 0 #000, 6px 8px 18px rgba(0,0,0,0.45)',
  color: '#1d1d2b',
} as const

function Cloud({ top, scale, dur, delay, opacity }: { top: string; scale: number; dur: number; delay: number; opacity: number }) {
  return (
    <div className="boot-move absolute left-0" style={{ top, opacity, animation: `boot-drift ${dur}s linear ${delay}s infinite` }}>
      <svg width={260 * scale} height={110 * scale} viewBox="0 0 260 110" aria-hidden>
        <ellipse cx="130" cy="92" rx="108" ry="14" fill="#c9e2f4" />
        <g fill="#fff">
          <ellipse cx="60" cy="78" rx="52" ry="26" />
          <ellipse cx="120" cy="58" rx="60" ry="38" />
          <ellipse cx="184" cy="74" rx="56" ry="30" />
          <ellipse cx="130" cy="84" rx="108" ry="18" />
        </g>
      </svg>
    </div>
  )
}

function MiniMail({ top, dur, delay }: { top: string; dur: number; delay: number }) {
  return (
    <div className="boot-move absolute left-0" style={{ top, animation: `boot-fly ${dur}s linear ${delay}s infinite` }}>
      <div className="boot-move" style={{ animation: 'boot-bob 2.4s ease-in-out infinite' }}>
        <PixelSprite rows={ENVELOPE.rows} palette={ENVELOPE.palette} scale={2} />
      </div>
    </div>
  )
}

// Own sky: blue gradient, flat clouds drifting by, a few small envelopes flying across.
function Sky() {
  return (
    <div className="absolute inset-0 overflow-hidden" style={{ background: 'linear-gradient(#1c63c4 0%, #4f9de0 52%, #b9e3f6 100%)', animation: 'boot-fade 600ms ease-out both' }} aria-hidden>
      <Cloud top="6%" scale={1.3} dur={95} delay={-20} opacity={0.95} />
      <Cloud top="30%" scale={0.8} dur={70} delay={-55} opacity={0.8} />
      <Cloud top="52%" scale={1.6} dur={120} delay={-90} opacity={0.9} />
      <Cloud top="72%" scale={1.0} dur={80} delay={-30} opacity={0.85} />
      <Cloud top="14%" scale={0.6} dur={60} delay={-8} opacity={0.7} />
      <MiniMail top="22%" dur={17} delay={-4} />
      <MiniMail top="58%" dur={23} delay={-13} />
      <MiniMail top="40%" dur={29} delay={-21} />
    </div>
  )
}

function Wordmark({ text, typed }: { text: string; typed: boolean }) {
  const [n, setN] = useState(0)
  useEffect(() => {
    if (!typed) return
    let timer: number | undefined
    let i = 0
    const start = window.setTimeout(() => {
      timer = window.setInterval(() => {
        i += 1
        setN(i)
        if (i >= text.length) window.clearInterval(timer)
      }, 55)
    }, 500)
    return () => {
      window.clearTimeout(start)
      window.clearInterval(timer)
    }
  }, [text, typed])
  return (
    <div className="font-bold italic leading-none" style={{ fontSize: typed ? 52 : 34, color: '#fff', textShadow: SHADOW, fontFamily: 'Tahoma, "Segoe UI", Verdana, sans-serif', minHeight: typed ? 56 : 38 }}>
      {typed ? text.slice(0, n) : text}
    </div>
  )
}

function Chips({ chips }: { chips: Chip[] }) {
  return (
    <div className="flex flex-wrap justify-center gap-x-4 gap-y-1 text-[12px]" style={{ color: '#fff', textShadow: '1px 1px 0 rgba(10,36,106,0.8)' }}>
      {chips.map((c) => (
        <span key={c.id} title={c.detail} style={{ color: c.mark === 'warn' ? '#fff0b3' : c.mark === 'fail' ? '#ffd0d0' : undefined }}>
          {c.mark === 'ok' ? '✓' : c.mark === 'warn' ? '⚠' : '✗'} {c.label}
          {c.mark !== 'ok' ? ` – ${c.detail}` : ''}
        </span>
      ))}
    </div>
  )
}

// `local`: the admin application runs inside this app. The same show runs, the check asks that application, and there
// is no tunnel and no sign-in.
export function Boot({ resume, onDone, local = false }: { resume: boolean; onDone: () => void; local?: boolean }) {
  const [scene, setScene] = useState<Scene>(resume ? 'welcome' : 'power')
  const [chips, setChips] = useState<Chip[]>([])
  const [tunnelOk, setTunnelOk] = useState(false)
  const [block, setBlock] = useState<Block>(null)
  const [blockWhy, setBlockWhy] = useState('')
  const [loginError, setLoginError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [signedIn, setSignedIn] = useState(false)
  const [greeting, setGreeting] = useState(false)
  const [leaving, setLeaving] = useState(false)
  const [copied, setCopied] = useState(false)
  const [now, setNow] = useState(0)
  const skip = useRef(false)
  const run = useRef(0)
  const sceneRef = useRef<Scene>(scene)
  const startAt = useRef(0)
  const tunnelRef = useRef(false)
  const blockRef = useRef<Block>(null)
  sceneRef.current = scene
  tunnelRef.current = tunnelOk
  blockRef.current = block

  const put = (id: number, c: Chip) => {
    if (id === run.current) setChips((l) => [...l.filter((x) => x.id !== c.id), c])
  }

  // Is the tunnel up? Sets the tunnel state and a chip.
  const checkTunnel = async (id: number) => {
    setBlock(null)
    if (local) {
      const s = await admin.status()
      if (id !== run.current) return
      if (s.status === 200) {
        put(id, { id: 'tunnel', label: t('Verbindung'), detail: t('Programm in dieser App'), mark: 'ok' })
        setTunnelOk(true)
      } else {
        const why = s.data?.message ?? `HTTP ${s.status}`
        put(id, { id: 'tunnel', label: t('Verbindung'), detail: t('keine Antwort'), mark: 'fail' })
        setBlockWhy(String(why))
        setBlock('tunnel')
      }
      return
    }
    const p = await admin.probe()
    if (id !== run.current) return
    if (p.result === 'up') {
      put(id, { id: 'tunnel', label: t('Verbindung'), detail: ADMIN_BASE.replace('http://', ''), mark: 'ok' })
      setTunnelOk(true)
    } else {
      put(id, { id: 'tunnel', label: t('Verbindung'), detail: p.result === 'unreachable' ? t('keine Antwort') : `HTTP ${p.status}`, mark: 'fail' })
      setBlockWhy(p.result === 'unreachable' ? t('keine Antwort') : `HTTP ${p.status}`)
      setBlock('tunnel')
    }
  }

  // After sign-in: read the status, show it, then leave for the desktop.
  const verify = async (id: number) => {
    setBlock(null)
    const s = await admin.status()
    if (id !== run.current) return
    if (s.status !== 200) {
      put(id, { id: 'schema', label: t('Datenbank'), detail: t('Status nicht lesbar (HTTP {status})', { status: s.status }), mark: 'fail' })
      setBlock('status')
      return
    }
    const schema = s.data?.schema_version
    const active = s.data?.recipients?.active
    put(id, {
      id: 'schema',
      label: t('Datenbank-Schema {schema}', { schema: schema ?? '?' }),
      detail: t('erwartet {schema}', { schema: EXPECTED_SCHEMA }),
      mark: schema === EXPECTED_SCHEMA ? 'ok' : 'warn',
    })
    put(id, { id: 'recipients', label: t('{n} Empfänger aktiv', { n: active ?? '?' }), detail: '', mark: 'ok' })
    setGreeting(true)
    await new Promise((r) => setTimeout(r, skip.current ? 150 : 900))
    if (id !== run.current) return
    setLeaving(true)
    await new Promise((r) => setTimeout(r, 450))
    if (id === run.current) onDone()
  }

  const retry = () => {
    const id = ++run.current
    if (blockRef.current === 'status') void verify(id)
    else void checkTunnel(id)
  }
  const retryRef = useRef(retry)
  retryRef.current = retry

  // The real checks start at once.
  useEffect(() => {
    const id = ++run.current
    if (!resume) {
      const stored = localStorage.length + sessionStorage.length
      put(id, { id: 'window', label: t('Fenster'), detail: inTauri ? `Tauri · ${window.location.origin}` : `${t('Browser ohne Dateizugriff')} · ${window.location.origin}`, mark: inTauri ? 'ok' : 'warn' })
      put(id, { id: 'storage', label: t('Speicher leer'), detail: stored === 0 ? t('es wird nichts abgelegt') : t('{n} Einträge vorhanden', { n: stored }), mark: stored === 0 ? 'ok' : 'warn' })
    }
    void checkTunnel(id)
    return () => {
      run.current++
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Scene 1 -> 2 after a short moment.
  useEffect(() => {
    if (scene !== 'power') return
    const t = window.setTimeout(() => setScene('start'), POWER_MS)
    return () => window.clearTimeout(t)
  }, [scene])

  // Scene 2 -> 3 as soon as the show has run its minimum and the tunnel answers.
  useEffect(() => {
    if (scene !== 'start') return
    startAt.current = performance.now()
    const t = window.setInterval(() => {
      const t0 = performance.now()
      setNow(t0)
      if (tunnelRef.current && (skip.current || t0 - startAt.current >= START_MIN_MS)) setScene('welcome')
    }, 60)
    return () => window.clearInterval(t)
  }, [scene])

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      skip.current = true
      if (sceneRef.current === 'power') setScene('start')
      if (blockRef.current && e.key === 'Enter') retryRef.current()
    }
    const onPointer = () => {
      skip.current = true
      if (sceneRef.current === 'power') setScene('start')
    }
    window.addEventListener('keydown', onKey)
    window.addEventListener('pointerdown', onPointer)
    return () => {
      window.removeEventListener('keydown', onKey)
      window.removeEventListener('pointerdown', onPointer)
    }
  }, [])

  const submit = async (passphrase: string) => {
    const id = run.current
    setBusy(true)
    setLoginError(null)
    try {
      const r = await admin.login(passphrase)
      if (id !== run.current) return
      if (r.status === 200) {
        if (resume) {
          onDone()
          return
        }
        const mins = Math.round((Number(r.data?.idle_seconds) || 0) / 60)
        put(id, { id: 'login', label: t('Angemeldet'), detail: t('Sitzung endet nach {mins} min Leerlauf', { mins }), mark: 'ok' })
        setSignedIn(true)
        await verify(id)
      } else if (r.status === 401) setLoginError(t('Die Passphrase stimmt nicht.'))
      else if (r.status === 429) setLoginError(t('Zu viele Versuche. Bitte einen Moment warten.'))
      else setLoginError(t('Der Server meldet Fehler {status}.', { status: r.status }))
    } catch {
      setLoginError(t('Der Server antwortet nicht mehr. Läuft der Tunnel noch?'))
    } finally {
      setBusy(false)
    }
  }

  const copy = async () => {
    try {
      await navigator.clipboard.writeText(TUNNEL_COMMAND)
      setCopied(true)
    } catch {
      /* the command is shown anyway */
    }
  }

  // Local: nothing to sign in to. Once the checks are through, the welcome scene goes on by itself.
  useEffect(() => {
    if (local && scene === 'welcome' && tunnelOk && !block && !signedIn) {
      setSignedIn(true)
      void verify(run.current)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [local, scene, tunnelOk, block, signedIn])

  const byId = (id: string) => chips.find((c) => c.id === id)
  const pre = ['window', 'storage', 'tunnel'].filter((id) => byId(id) && byId(id)!.mark !== 'fail').length
  const timeFrac = skip.current ? 1 : Math.min(1, Math.max(0, (now - startAt.current) / START_MIN_MS))
  const frac = Math.min(timeFrac, pre / PRE_CHECKS)

  const panel = block && (
    <div className="absolute left-1/2 -translate-x-1/2 p-4 text-[13px] leading-6" style={{ bottom: '7vh', width: 'min(640px, 92vw)', zIndex: 30, ...FRAME }} role="alert">
      {block === 'tunnel' && local ? (
        <>
          <div className="font-bold mb-1" style={{ color: '#a01020' }}>{t('Die Verwaltung in dieser App ist nicht angesprungen.')}</div>
          <p className="mb-3 text-[12px]" style={{ color: '#4a4a5a' }}>{blockWhy}</p>
        </>
      ) : block === 'tunnel' ? (
        <>
          <div className="font-bold mb-1" style={{ color: '#a01020' }}>{t('Die Verwaltung ist nicht erreichbar: {why}.', { why: blockWhy ?? '' })}</div>
          <p className="mb-2">{t('Meistens fehlt der SSH-Tunnel. Öffne ein Terminal und starte ihn. Das Terminal bleibt danach offen und still, das ist richtig:')}</p>
          <pre className="p-2 mb-2 overflow-x-auto select-all text-[12px]" style={{ background: '#15151c', color: '#e8e8e8', fontFamily: MONO }}>{GENERIC_TUNNEL_COMMAND}</pre>
          <p className="mb-2 text-[12px]" style={{ color: '#4a4a5a' }}>
            {t('Ersetze BENUTZER und SERVER durch deine Angaben. Der Knopf Befehl kopieren legt den Befehl in die Zwischenablage, den du in der Datei frontend/.env.local eingetragen hast.')}
          </p>
          <p className="mb-3 text-[12px]" style={{ color: '#4a4a5a' }}>
            {t('Läuft der Tunnel und es klappt trotzdem nicht, muss der Server diese Herkunft erlauben:')} <b>{window.location.origin}</b>{t('. Die Einstellung dafür heißt OUTBOX_ADMIN_ALLOWED_ORIGINS.')}
          </p>
        </>
      ) : (
        <p className="mb-3" style={{ color: '#a01020' }}>{t('Die Anmeldung hat geklappt, aber der Status der Datenbank ließ sich nicht lesen.')}</p>
      )}
      <div className="flex gap-3">
        <button className="bevel" onClick={retry}>{t('Erneut prüfen')}</button>
        {block === 'tunnel' && !local && <button className="bevel" onClick={copy}>{copied ? t('Kopiert') : t('Befehl kopieren')}</button>}
      </div>
    </div>
  )

  return (
    <div className="fixed inset-0 overflow-hidden" style={{ zIndex: 1000, background: '#000', opacity: leaving ? 0 : 1, transition: 'opacity 450ms ease-in' }}>
      {scene === 'power' && (
        <div className="absolute inset-0 px-10 pt-12 text-[14px] leading-7" style={{ color: '#c8c8c8', fontFamily: MONO }}>
          <div style={{ color: '#9ec5ff' }}>{t('MAIL ASSISTANT · Startprüfung')}</div>
          {(['window', 'storage', 'tunnel'] as const).map((id, i) => {
            const c = byId(id)
            const label = id === 'window' ? t('Fenster') : id === 'storage' ? t('Speicher') : t('Verbindung')
            return (
              <div key={id} className="flex gap-3" style={{ animation: `boot-line 220ms ease-out ${i * 140}ms both`, maxWidth: 720 }}>
                <span className="shrink-0" style={{ width: 150 }}>{label}</span>
                <span className="flex-1">{c ? c.detail : t('wird geprüft …')}</span>
                <span style={{ color: c ? COLOR[c.mark] : '#666' }}>{c ? t(TAG[c.mark]) : '[ … ]'}</span>
              </div>
            )
          })}
        </div>
      )}

      {scene !== 'power' && <Sky />}

      {scene === 'start' && (
        <div className="absolute inset-0 flex flex-col items-center" style={{ paddingTop: '15vh' }}>
          <div style={{ animation: 'boot-pop 700ms cubic-bezier(.2,.8,.3,1) 150ms both' }} className="boot-move">
            <PixelSprite rows={ENVELOPE.rows} palette={ENVELOPE.palette} scale={10} />
          </div>
          <div className="mt-6"><Wordmark text="Mail Assistant" typed /></div>
          <div className="mt-2 text-[13px]" style={{ color: '#fff', textShadow: '1px 1px 0 rgba(10,36,106,0.8)', animation: 'boot-fade 600ms ease-out 1200ms both' }}>
            {t('Verwaltung · Version 0.1')}
          </div>
          <div className="mt-12" style={{ width: 360, height: 22, padding: 3, background: '#e8e8f4', border: '2px solid', borderColor: 'var(--bevel-dark) var(--bevel-light) var(--bevel-light) var(--bevel-dark)' }}>
            <div style={{ height: '100%', width: `${frac * 100}%`, transition: 'width 240ms linear', backgroundImage: 'repeating-linear-gradient(90deg, #0a246a 0 12px, transparent 12px 15px)' }} />
          </div>
          <div className="mt-4"><Chips chips={chips} /></div>
        </div>
      )}

      {scene === 'welcome' && (
        <>
          <div className="absolute inset-x-0 flex flex-col items-center" style={{ top: '9vh', animation: 'boot-fade 500ms ease-out both' }}>
            <PixelSprite rows={ENVELOPE.rows} palette={ENVELOPE.palette} scale={5} />
            <div className="mt-3"><Wordmark text={greeting ? t('Der Briefkasten hat Dienst.') : t('Willkommen')} typed={false} /></div>
          </div>
          {!resume && (
            <div className="absolute inset-x-0 px-6" style={{ bottom: '5vh' }}><Chips chips={chips} /></div>
          )}
          {tunnelOk && !block && !signedIn && !leaving && <LoginDialog error={loginError} busy={busy} onSubmit={submit} />}
        </>
      )}

      {panel}
    </div>
  )
}
