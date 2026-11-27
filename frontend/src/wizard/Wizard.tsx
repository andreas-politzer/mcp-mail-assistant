import { useEffect, useMemo, useRef, useState } from 'react'
import { Assistant } from '../components/Assistant'
import { locale, t, useLang } from '../i18n'
import { LangSwitch } from '../i18n/LangSwitch'
import { Postwurf } from '../components/Postwurf'
import { BLOB, PixelSprite } from '../components/PixelSprite'
import { WallpaperLayer } from '../components/Desktop'
import { HINTS, STEPS, STEPS_PHONE, type StepId } from './hints'
import { PRESETS, guessPreset, looksLikeAddress, presetFor, unsupportedReason, type Security } from './presets'
import type { RemoteStatus, SetupBackend, SetupStatus, TunnelStatus } from './setupApi'

// Setup wizard in the classic window style. It comes before the sign-in: a new installation has neither a
// server nor a passphrase yet. Whoever also wants the phone gets one more step: the tunnel (Tailscale Funnel).
//
// The wizard looks at the real state each time and only asks for what is missing, so it can be run again.
// The password lives in one input, goes through one call, and is cleared right away, win or lose.

const FRAME = {
  background: 'var(--face)',
  border: '2px solid',
  borderColor: 'var(--bevel-light) var(--bevel-dark) var(--bevel-dark) var(--bevel-light)',
  boxShadow: '1px 1px 0 #000, 6px 8px 18px rgba(0,0,0,0.45)',
  color: '#1d1d2b',
} as const
const INPUT = {
  border: '2px solid',
  borderColor: 'var(--bevel-dark) var(--bevel-light) var(--bevel-light) var(--bevel-dark)',
} as const

type Status = { kind: 'idle' } | { kind: 'busy' } | { kind: 'ok'; text: string } | { kind: 'fail'; text: string }
const IDLE: Status = { kind: 'idle' }

// Pages of Tailscale the window may open (the program refuses anything else, see setup.rs).
const TAILSCALE = {
  download: 'https://tailscale.com/download',
  dns: 'https://login.tailscale.com/admin/dns',
  funnelDocs: 'https://tailscale.com/docs/features/tailscale-funnel',
  httpsDocs: 'https://tailscale.com/docs/how-to/set-up-https-certificates',
} as const

// A link in the classic style: blue and underlined, and it says that it leaves the app. Never a plain <a>: it
// would load the page inside the app window. The address goes through the same gate as every other link.
function ExtLink({ backend, url, children }: { backend: SetupBackend; url: string; children: string }) {
  return (
    <>
      <button type="button" className="linkbtn" onClick={() => void backend.openLink(url)}>
        {children}
      </button>{' '}
      <span className="text-[11px]">{t('(öffnet im Browser)')}</span>
    </>
  )
}

const dayOf = (t: number) => new Date(t * 1000).toLocaleDateString(locale())
// "Bereits eingerichtet: MacBook (angelegt am ...)": neutral on purpose, it does not say which device holds the passkey.
export const knownPasskeys = (list: { name: string; created_at: number }[] | undefined, count: number, closing = true) => {
  if (!closing) {
    if (!list || list.length === 0) return count === 1 ? t('Bereits eingerichtet: {count} Passkey.', { count }) : t('Bereits eingerichtet: {count} Passkeys.', { count })
    return t('Bereits eingerichtet: {list}.', { list: list.map((k) => t('{name} (angelegt am {date})', { name: k.name || t('ohne Namen'), date: dayOf(k.created_at) })).join(', ') })
  }
  if (!list || list.length === 0)
    return count === 1
      ? t('Bereits eingerichtet: {count} Passkey. Du musst hier nichts mehr tun.', { count })
      : t('Bereits eingerichtet: {count} Passkeys. Du musst hier nichts mehr tun.', { count })
  const parts = list.map((k) => t('{name} (angelegt am {date})', { name: k.name || t('ohne Namen'), date: dayOf(k.created_at) }))
  return t('Bereits eingerichtet: {list}. Du musst hier nichts mehr tun.', { list: parts.join(', ') })
}

export function Wizard({ backend, onDone, onSkip }: { backend: SetupBackend; onDone: () => void; onSkip?: () => void }) {
  useLang() // redraw when the language is switched
  const [step, setStep] = useState<StepId>('where')
  const [tip, setTip] = useState(0)
  const [where, setWhere] = useState<'here' | 'phone'>('here')
  const [email, setEmail] = useState('')
  const [providerId, setProviderId] = useState('')
  const [host, setHost] = useState('')
  const [port, setPort] = useState('587')
  const [security, setSecurity] = useState<Security>('starttls')
  const [password, setPassword] = useState('')
  const [pwStatus, setPwStatus] = useState<Status>(IDLE)
  const [testTo, setTestTo] = useState('')
  const [markOwn, setMarkOwn] = useState(true)
  const [testStatus, setTestStatus] = useState<Status>(IDLE)
  const [testSkipped, setTestSkipped] = useState(false)
  const [gameOpen, setGameOpen] = useState(false)
  const [preview, setPreview] = useState<string[]>([])
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [claudeStatus, setClaudeStatus] = useState<Status>(IDLE)
  const [removeStatus, setRemoveStatus] = useState<Status>(IDLE)
  const [showPw, setShowPw] = useState(false)
  const [passkeys, setPasskeys] = useState(0)
  const [pkStatus, setPkStatus] = useState<Status>(IDLE)
  const [pkLink, setPkLink] = useState<string | null>(null)
  const [tunnel, setTunnel] = useState<TunnelStatus | null>(null)
  const [tunnelConfirmed, setTunnelConfirmed] = useState(false)
  const [tunnelResult, setTunnelResult] = useState<Status>(IDLE)
  const [tunnelAdmin, setTunnelAdmin] = useState<string | null>(null)
  const [tunnelTried, setTunnelTried] = useState(false)
  const [loginPassphrase, setLoginPassphrase] = useState<string | null>(null)
  const [loginSaved, setLoginSaved] = useState(false)
  const [loginExisting, setLoginExisting] = useState(false)
  const [loginUsed, setLoginUsed] = useState(false)
  const [loginStatus, setLoginStatus] = useState<Status>(IDLE)
  const [phoneAddress, setPhoneAddress] = useState<string | null>(null)
  const [phoneKeyLink, setPhoneKeyLink] = useState<string | null>(null)
  const [phoneKeysBefore, setPhoneKeysBefore] = useState(0)
  const [phoneKeyDone, setPhoneKeyDone] = useState(false)
  const [phoneKeyStatus, setPhoneKeyStatus] = useState<Status>(IDLE)
  const [setupInfo, setSetupInfo] = useState<SetupStatus | null>(null)
  const [againMode, setAgainMode] = useState(false)
  const [offOpen, setOffOpen] = useState(false)
  const [helpOpen, setHelpOpen] = useState(false)
  const [phoneState, setPhoneState] = useState<RemoteStatus | null>(null)
  const [offConfirmed, setOffConfirmed] = useState(false)
  const [offStatus, setOffStatus] = useState<Status>(IDLE)
  const [offNotes, setOffNotes] = useState<string[]>([])
  const [offDone, setOffDone] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [copyFailed, setCopyFailed] = useState(false)
  const pwInput = useRef<HTMLInputElement>(null)

  const connectorUrl = phoneAddress ? `${phoneAddress}/mcp` : null
  const steps = where === 'phone' ? STEPS_PHONE : STEPS
  const index = steps.indexOf(step)
  const preset = providerId ? presetFor(providerId) : null
  const hints = HINTS[step]
  const hint = t(hints[Math.min(tip, hints.length - 1)])

  const go = (to: StepId) => {
    setStep(to)
    setTip(0)
  }

  const pickProvider = (id: string) => {
    setProviderId(id)
    const p = presetFor(id)
    setHost(p.host)
    setPort(String(p.port))
    setSecurity(p.security)
  }

  // Guess the provider from the address as long as the person has not chosen one.
  const onEmail = (v: string) => {
    setEmail(v)
    const g = guessPreset(v)
    if (g && !providerId) pickProvider(g)
  }

  useEffect(() => {
    if (step === 'password') pwInput.current?.focus()
    if (step === 'test') setTestTo((t) => t || email.trim())
    if (step === 'claude' && preview.length === 0) {
      void backend.previewClaudeConnection().then((r) => (r.ok ? setPreview(r.lines) : setPreviewError(r.reason)))
    }
    if (step === 'tunnel' && tunnelResult.kind !== 'ok') void loadTunnel()
    if (step !== 'phonelogin') {
      setLoginPassphrase(null) // never kept beyond its step
      setLoginSaved(false)
    }
    setCopied(null)
    setCopyFailed(false)
    setHelpOpen(false)
    if (step === 'phonelogin' || step === 'connector') {
      void backend.remoteStatus().then((r) => {
        setLoginExisting(r.enabled && !r.passphrase_placeholder)
        if (r.address) setPhoneAddress(r.address)
        if (r.enabled && !r.passphrase_placeholder) setLoginUsed(true)
      })
    }
  }, [step]) // eslint-disable-line react-hooks/exhaustive-deps

  // Look at the real state once: what is already set up is not asked for again.
  useEffect(() => {
    let alive = true
    void backend.status().then((st) => {
      if (!alive) return
      setSetupInfo(st)
      if (!st.mailbox) return
      setEmail(st.mailbox.email)
      const known = PRESETS.find((p) => p.host === st.mailbox!.host)
      setProviderId(known ? known.id : 'other')
      setHost(st.mailbox.host)
      setPort(String(st.mailbox.port))
      setSecurity(st.mailbox.security)
      if (st.password_stored) setPwStatus({ kind: 'ok', text: t('Das Passwort liegt schon im Schlüsselbund. Du kannst weitergehen oder es neu eingeben.') })
      if (st.claude_connected) setClaudeStatus({ kind: 'ok', text: t('Claude Desktop ist schon verbunden.') })
      setPasskeys(st.passkeys)
      if (st.passkeys > 0) setPkStatus({ kind: 'ok', text: knownPasskeys(st.passkey_list, st.passkeys) })
    })
    return () => {
      alive = false
    }
  }, [backend])

  // Is something of the phone use still there? Then the way back is offered on the first page.
  useEffect(() => {
    let alive = true
    void backend.remoteStatus().then((r) => alive && setPhoneState(r))
    return () => {
      alive = false
    }
  }, [backend])

  // Page 1 always shows the real state: a running phone access is preselected. "Set up" (mailbox, password in the
  // keychain, and Claude Desktop connected or a phone access there) only changes the wording and the buttons.
  // A half-finished first setup stays a first setup. Decided once.
  useEffect(() => {
    if (againMode || !setupInfo || !phoneState) return
    const done = !!setupInfo.mailbox && setupInfo.password_stored && (setupInfo.claude_connected || phoneState.traces)
    if (phoneState.enabled) setWhere('phone')
    if (done) setAgainMode(true)
  }, [setupInfo, phoneState, againMode])

  const blocked = unsupportedReason(email)
  const mailboxReady = !blocked && looksLikeAddress(email) && providerId !== '' && host.trim() !== '' && Number(port) > 0
  const portNumber = Number(port)

  const checkPassword = async () => {
    if (!password || pwStatus.kind === 'busy') return
    const p = password
    setPassword('') // gone from the form right away, win or lose
    setPwStatus({ kind: 'busy' })
    const r = await backend.saveMailbox({ email: email.trim(), host: host.trim(), port: portNumber, security }, p)
    if (r.ok) {
      setPwStatus({ kind: 'ok', text: t('Passwort im Schlüsselbund gespeichert, Anmeldung beim Anbieter geklappt.') })
    } else {
      setPwStatus({ kind: 'fail', text: r.reason })
      pwInput.current?.focus()
    }
  }

  const sendTest = async () => {
    if (testStatus.kind === 'busy') return
    setTestStatus({ kind: 'busy' })
    const r = await backend.sendTestMail(testTo.trim(), markOwn)
    if (r.ok) setTestSkipped(false)
    setTestStatus(r.ok ? { kind: 'ok', text: t('Testmail an {to} ist raus. Schau in dein Postfach.', { to: testTo.trim() }) } : { kind: 'fail', text: r.reason })
  }

  const getPasskeyLink = async () => {
    if (pkStatus.kind === 'busy') return
    setPkStatus({ kind: 'busy' })
    const r = await backend.approvalLink()
    if (!r.ok) {
      setPkLink(null)
      setPkStatus({ kind: 'fail', text: r.reason })
      return
    }
    setPkLink(r.url)
    const opened = await backend.openLink(r.url)
    setPkStatus({
      kind: 'ok',
      text: opened.ok
        ? t('Die Seite wurde im Browser geöffnet. Lege dort den Passkey an und klicke danach hier auf „Prüfen“. Der Link gilt einmal, 10 Minuten.')
        : `${opened.reason} ${t('Der Link gilt einmal, 10 Minuten.')}`,
    })
  }

  const checkPasskey = async () => {
    if (pkStatus.kind === 'busy') return
    setPkStatus({ kind: 'busy' })
    const st = await backend.status()
    setPasskeys(st.passkeys)
    setPkStatus(
      st.passkeys > 0
        ? { kind: 'ok', text: t('Passkey ist registriert. Mails an andere kannst du ab jetzt freigeben.') }
        : { kind: 'fail', text: t('Es ist noch kein Passkey registriert. Hast du den Vorgang im Browser abgeschlossen?') },
    )
  }

  const loadTunnel = async () => {
    setTunnel(null)
    setTunnelResult(IDLE)
    setTunnelAdmin(null)
    setTunnelTried(false)
    setTunnel(await backend.tunnelStatus())
  }

  // The address is the one the person confirmed; the program refuses it if it is no longer Tailscale's own.
  const enableTunnel = async () => {
    if (!tunnel?.address || tunnelResult.kind === 'busy') return
    setTunnelTried(true)
    setTunnelAdmin(null)
    setTunnelResult({ kind: 'busy' })
    const r = await backend.tunnelEnable(tunnel.address)
    if (r.ok) {
      setTunnelResult({
        kind: 'ok',
        text: `${t('Der Tunnel steht: {address}.', { address: r.address })} ${
          r.passphrase_placeholder ? t('Anmelden vom Handy geht erst mit einer Passphrase, die kommt im nächsten Schritt.') + ' ' : ''
        }${t('Ob dein Handy durchkommt, zeigt sich erst dort; geprüft wurde bisher von diesem Computer aus.')}`,
      })
    } else {
      setTunnelResult({ kind: 'fail', text: r.reason })
      setTunnelAdmin(r.url ?? null)
    }
  }

  // The way back to "only this computer". Needs the tick; the program does the work in a safe order and can be
  // asked again after an error.
  const turnOffPhone = async () => {
    if (!offConfirmed || offStatus.kind === 'busy') return
    setOffStatus({ kind: 'busy' })
    const r = await backend.tunnelDisable()
    if (!r.ok) {
      setOffStatus({ kind: 'fail', text: r.reason })
      return
    }
    setOffNotes(r.notes)
    setOffDone(true)
    setOffConfirmed(false)
    // Whatever the phone path had set up in this window is gone now; a later new setup starts clean.
    setTunnel(null)
    setTunnelConfirmed(false)
    setTunnelResult(IDLE)
    setLoginUsed(false)
    setLoginExisting(false)
    setPhoneAddress(null)
    setPhoneKeyLink(null)
    setPhoneKeyDone(false)
    setPhoneKeyStatus(IDLE)
    setPhoneState(await backend.remoteStatus())
    setWhere('here')
    setOffOpen(false)
    setOffStatus({
      kind: 'ok',
      text: r.removed
        ? t('Der Handy-Zugang ist abgeschaltet. Dein Computer ist wieder nur für dich erreichbar.')
        : t('Es war nichts mehr eingerichtet. Du bist schon bei „Nur an diesem Computer“.'),
    })
  }

  // Copy only on a click, never on its own. A refused copy is said out loud.
  const copy = async (text: string, key: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(key)
      setCopyFailed(false)
    } catch {
      setCopied(null)
      setCopyFailed(true)
    }
  }

  // The passphrase lives in this state only while the step is open; leaving the step clears it.
  const createPassphrase = async () => {
    if (loginStatus.kind === 'busy') return
    setLoginStatus({ kind: 'busy' })
    const r = await backend.newPassphrase()
    if (r.ok) {
      setLoginPassphrase(r.passphrase)
      setLoginSaved(false)
      setLoginUsed(true)
      setPhoneAddress(r.address)
      setLoginStatus(IDLE)
    } else {
      setLoginStatus({ kind: 'fail', text: r.reason })
    }
  }

  const getPhoneKeyLink = async () => {
    if (phoneKeyStatus.kind === 'busy') return
    setPhoneKeyStatus({ kind: 'busy' })
    const before = (await backend.status()).passkeys
    const r = await backend.approvalLink(true)
    if (!r.ok) {
      setPhoneKeyStatus({ kind: 'fail', text: r.reason })
      return
    }
    setPhoneKeysBefore(before)
    setPhoneKeyLink(r.url)
    setPhoneKeyStatus({
      kind: 'ok',
      text: t('Der Link gilt einmal, 10 Minuten. Öffne ihn auf dem Handy und lege dort den Passkey an. Klicke danach hier auf „Prüfen“.'),
    })
  }

  const checkPhoneKey = async () => {
    if (phoneKeyStatus.kind === 'busy') return
    if (phoneKeyLink === null) {
      setPhoneKeyStatus({ kind: 'fail', text: t('Erzeuge zuerst den Link und öffne ihn auf dem Handy.') })
      return
    }
    setPhoneKeyStatus({ kind: 'busy' })
    const st = await backend.status()
    setPasskeys(st.passkeys)
    if (st.passkeys > phoneKeysBefore) {
      setPhoneKeyDone(true)
      setPhoneKeyStatus({ kind: 'ok', text: t('Der Passkey am Handy ist registriert. Mails an andere gibst du ab jetzt auch dort frei.') })
    } else {
      setPhoneKeyStatus({
        kind: 'fail',
        text: t('Es ist noch kein neuer Passkey registriert. Hast du den Vorgang auf dem Handy abgeschlossen? Der Link gilt 10 Minuten, danach erzeuge einen neuen.'),
      })
    }
  }

  // While a link is out, the window looks by itself every few seconds whether the passkey has appeared.
  useEffect(() => {
    const phone = step === 'phonekey' && phoneKeyLink !== null && !phoneKeyDone
    const desk = step === 'passkey' && pkLink !== null && passkeys === 0
    if (!phone && !desk) return
    let alive = true
    const timer = setInterval(() => {
      void backend.status().then((st) => {
        if (!alive) return
        if (phone && st.passkeys > phoneKeysBefore) {
          setPasskeys(st.passkeys)
          setPhoneKeyDone(true)
          setPhoneKeyStatus({ kind: 'ok', text: t('Der Passkey am Handy ist registriert. Mails an andere gibst du ab jetzt auch dort frei.') })
        } else if (desk && st.passkeys > 0) {
          setPasskeys(st.passkeys)
          setPkStatus({ kind: 'ok', text: t('Passkey ist registriert. Mails an andere kannst du ab jetzt freigeben.') })
        }
      })
    }, 3000)
    return () => {
      alive = false
      clearInterval(timer)
    }
  }, [step, phoneKeyLink, phoneKeyDone, pkLink, passkeys, phoneKeysBefore, backend])

  const connectClaude = async () => {
    if (claudeStatus.kind === 'busy') return
    setClaudeStatus({ kind: 'busy' })
    const r = await backend.connectClaude()
    setClaudeStatus(r.ok ? { kind: 'ok', text: t('Verbunden. Claude Desktop jetzt ganz beenden und neu starten.') } : { kind: 'fail', text: r.reason })
  }

  const disconnectClaude = async () => {
    if (removeStatus.kind === 'busy') return
    setRemoveStatus({ kind: 'busy' })
    const r = await backend.disconnectClaude()
    if (r.ok) {
      setClaudeStatus(IDLE)
      setRemoveStatus({ kind: 'ok', text: t('Die Verbindung zu Claude Desktop ist entfernt. Deine Empfänger und Einstellungen bleiben erhalten. Claude Desktop jetzt ganz beenden (Cmd+Q) und neu starten.') })
    } else {
      setRemoveStatus({ kind: 'fail', text: r.reason })
    }
  }

  // The warning is open while the person has chosen "only this computer" although a phone access is running, or
  // asked to remove leftovers. "Weiter" waits then, so nothing is switched off by merely moving on.
  const warningOpen = !offDone && !!phoneState?.traces && ((phoneState.enabled && where === 'here') || offOpen)
  // Desktop is set up and only the phone is missing: the main button goes straight to the tunnel card.
  const jumpToTunnel = step === 'where' && againMode && where === 'phone' && !!phoneState && !phoneState.traces

  // Two passkeys are the most there can be. Then there is nothing to create here: the phone is either among them or not.
  const phoneAtLimit = passkeys >= 2 && !phoneKeyDone

  const canNext = useMemo(() => {
    switch (step) {
      case 'where':
        return !warningOpen
      case 'mailbox':
        return mailboxReady
      case 'password':
        return pwStatus.kind === 'ok'
      case 'test':
        return testStatus.kind === 'ok'
      case 'claude':
        return claudeStatus.kind === 'ok'
      case 'passkey':
        return passkeys > 0
      case 'tunnel':
        return tunnelResult.kind === 'ok'
      case 'phonelogin':
        return loginPassphrase !== null ? loginSaved : loginExisting
      case 'connector':
        return true
      case 'phonekey':
        return phoneKeyDone || phoneAtLimit
      default:
        return true
    }
  }, [step, warningOpen, mailboxReady, pwStatus.kind, testStatus.kind, claudeStatus.kind, passkeys, tunnelResult.kind, loginPassphrase, loginSaved, loginExisting, phoneKeyDone, phoneAtLimit])

  const back = () => index > 0 && go(steps[index - 1])
  const next = () => (step === 'done' ? onDone() : jumpToTunnel ? go('tunnel') : go(steps[index + 1]))

  const statusLine = (s: Status) =>
    s.kind === 'idle' ? null : (
      <div className="mt-2 text-[12px]" role={s.kind === 'fail' ? 'alert' : 'status'} style={{ color: s.kind === 'fail' ? '#a01020' : s.kind === 'ok' ? '#0a6a2a' : '#1d1d2b' }}>
        {s.kind === 'busy' ? t('Einen Moment …') : s.text}
      </div>
    )

  return (
    <div className="fixed inset-0 flex items-center justify-center md:pr-[330px]" style={{ zIndex: 30, backgroundColor: '#7fb4e6' }} data-wizard>
      <WallpaperLayer wp={{ kind: 'clouds', tiled: false }} idp="wz" />
      <button
        type="button"
        className="absolute flex flex-col items-center gap-1 w-24 py-1 select-none outline-none"
        style={{ left: 20, bottom: 20, zIndex: 31 }}
        onClick={() => setGameOpen(true)}
        title={t('Wenn du eine Pause brauchst')}
      >
        <PixelSprite rows={BLOB.rows} palette={BLOB.palette} scale={3} />
        <span className="text-[12px] px-1.5 text-white" style={{ background: '#0a246a' }}>{t('Alienalarm')}</span>
      </button>
      {gameOpen && <Postwurf onClose={() => setGameOpen(false)} initial={{ x: Math.max(8, Math.round(window.innerWidth / 2 - 200)), y: 24 }} />}
      <div className="relative" style={{ width: 480, ...FRAME }} role="dialog" aria-label={t('Einrichtung')}>
        <div className="px-2 py-0.5 m-0.5 select-none font-bold text-[13px] flex" style={{ background: 'linear-gradient(90deg, #0a246a, #3a6ea5)', color: '#fff' }}>
          <span className="flex-1">{t('Einrichtung – Mail Assistant')}</span>
          <span className="font-normal">{t('Schritt {n} von {total}', { n: index + 1, total: steps.length })}</span>
        </div>

        <div className="p-4 text-[13px] leading-relaxed" style={{ minHeight: 300 }}>
          {step === 'where' && (
            <>
              <div className="font-bold mb-2">{againMode ? t('Mail Assistant ist eingerichtet') : t('Wo willst du Mail Assistant benutzen?')}</div>
              <label className="flex gap-2 mb-2 cursor-pointer">
                <input type="radio" name="where" checked={where === 'here'} onChange={() => setWhere('here')} />
                <span>
                  <b>{t('Nur an diesem Computer')}</b>
                  {phoneState && !phoneState.enabled ? ` ${t('(aktuell)')}` : ''}
                  <br />
                  {t('Du schreibst mit Claude Desktop auf diesem Rechner. Mehr brauchst du nicht.')}
                </span>
              </label>
              <label className="flex gap-2 mb-2 cursor-pointer">
                <input
                  type="radio"
                  name="where"
                  checked={where === 'phone'}
                  onChange={() => {
                    setWhere('phone')
                    setTip(1)
                  }}
                />
                <span>
                  <b>{t('Auch vom Handy aus')}</b>
                  {phoneState?.enabled ? ` ${t('(aktuell)')}` : ''}
                  <br />
                  {t('Für die Claude-App auf iPhone oder Android. Der Computer muss dafür eingeschaltet sein. Du kannst den Handy-Zugang auch später einrichten.')}
                </span>
              </label>
              {where === 'phone' && (
                <fieldset className="mt-2 p-2 text-[12px]" style={{ border: '2px groove #fff' }}>
                  <legend className="px-1">{t('Zugangsweg fürs Handy')}</legend>
                  <label className="flex gap-2 mb-2 cursor-pointer">
                    <input type="radio" name="via" checked readOnly />
                    <span>
                      <b>Tailscale Funnel</b>
                      {phoneState?.enabled ? ` ${t('(aktiv)')}` : ''}
                      <br />
                      {t('Kostenlos für den privaten Gebrauch. Keine Warnseite. Die Adresse enthält den Namen deines Computers.')}
                    </span>
                  </label>
                  <label className="flex gap-2 mb-2" style={{ opacity: 0.5 }}>
                    <input type="radio" name="via" disabled />
                    <span>
                      <b>ngrok</b> {t('(kommt bald)')}
                      <br />
                      {t('Kostenlos. Weniger Einrichtung, neutrale Adresse. Einmal pro Browser eine Warnseite beim Anmelden.')}
                    </span>
                  </label>
                  <label className="flex gap-2 mb-2" style={{ opacity: 0.5 }}>
                    <input type="radio" name="via" disabled />
                    <span>
                      <b>{t('Cloudflare Tunnel, eigene Domain')}</b> {t('(kommt bald)')}
                      <br />
                      {t('Der Tunnel ist kostenlos. Du brauchst eine eigene Domain, z. B. mail.deinname.de, und sie muss bei Cloudflare liegen. Hast du noch keine, kaufst du sie bei einem Anbieter.')}
                    </span>
                  </label>
                  <div>{t('Ein späterer Wechsel ändert die Adresse. Die Anmeldung am Handy muss dann neu eingerichtet werden.')}</div>
                </fieldset>
              )}
              {where === 'phone' && !phoneState?.enabled && (
                <div className="mt-2 p-2 text-[12px]" style={{ background: '#ffffe1', border: '1px solid #000' }}>
                  {jumpToTunnel
                    ? t('Die Schritte 2 bis {to} sind schon erledigt. Es geht gleich bei Schritt {next} weiter. Dafür brauchst du ein Tailscale-Konto.', { to: STEPS_PHONE.indexOf('tunnel'), next: STEPS_PHONE.indexOf('tunnel') + 1 })
                    : t('Dafür brauchst du ein Tailscale-Konto. Den Tunnel richtest du erst in Schritt {step} ein.', { step: STEPS_PHONE.indexOf('tunnel') + 1 })}
                </div>
              )}
              {phoneState?.traces && !phoneState.enabled && !offDone && !offOpen && (
                <div className="mt-2 p-2 text-[12px] flex items-center gap-2" style={{ border: '2px groove #fff' }}>
                  <span className="flex-1">{t('Es gibt noch Reste eines Handy-Zugangs.')}</span>
                  <button type="button" className="bevel" onClick={() => setOffOpen(true)}>
                    {t('Reste entfernen …')}
                  </button>
                </div>
              )}
              {warningOpen && phoneState && (
                <div className="mt-2 p-2 text-[12px]" style={{ border: '2px groove #fff' }} role="group" aria-label={t('Handy-Zugang abschalten')}>
                  <div className="font-bold mb-1">
                    {phoneState.enabled ? t('Der Handy-Zugang ist noch eingerichtet.') : t('Es gibt noch Reste eines Handy-Zugangs.')}
                  </div>
                  <div className="mb-2" style={{ color: '#a01020' }}>
                    {t('„Weiter“ geht erst, wenn du den Handy-Zugang abschaltest')}{phoneState.enabled ? ` ${t('oder oben wieder „Auch vom Handy aus“ wählst, um ihn zu behalten')}` : ''}.
                  </div>
                  <div className="mb-1">{t('Wenn du ihn abschaltest:')}</div>
                  <ul className="list-disc pl-5 mb-2">
                    <li>{t('Die öffentliche Adresse wird abgeschaltet. Die Claude-App auf dem Handy erreicht deinen Computer dann nicht mehr.')}</li>
                    <li>{t('Alle Anmeldungen am Handy enden, die Passphrase wird ungültig.')}</li>
                    <li>{t('Die Adresse ist an Passkey und Connector gebunden. Schaltest du den Handy-Zugang später wieder ein, brauchst du eine neue Passphrase, einen neuen Connector-Eintrag und einen neuen Passkey am Handy.')}</li>
                    <li>{t('Den Connector-Eintrag in claude.ai kann diese App nicht löschen. Entferne ihn dort selbst unter Einstellungen → Connectors.')}</li>
                    <li>{t('Dein alter Handy-Passkey bleibt in der Passkey-Kachel der Verwaltung gespeichert. Dort kannst du ihn bei Bedarf entfernen (es sind insgesamt höchstens 2 Passkeys möglich).')}</li>
                  </ul>
                  <div className="mb-2">{t('Postfach, Empfänger und der Passkey dieses Computers bleiben. An Tailscale selbst ändert sich nichts.')}</div>
                  <label className="flex gap-2 mb-2 cursor-pointer">
                    <input type="checkbox" checked={offConfirmed} disabled={offStatus.kind === 'busy'} onChange={(e) => setOffConfirmed(e.target.checked)} />
                    <span>{t('Ja, den Handy-Zugang abschalten.')}</span>
                  </label>
                  <div className="flex justify-end gap-2">
                    <button
                      type="button"
                      className="bevel"
                      disabled={offStatus.kind === 'busy'}
                      onClick={() => {
                        setOffOpen(false)
                        setOffConfirmed(false)
                        setOffStatus(IDLE)
                        if (phoneState.enabled) setWhere('phone')
                      }}
                    >
                      {t('Abbrechen')}
                    </button>
                    <button type="button" className="bevel font-bold" disabled={!offConfirmed || offStatus.kind === 'busy'} onClick={() => void turnOffPhone()}>
                      {t('Handy-Zugang abschalten')}
                    </button>
                  </div>
                  {!offConfirmed && offStatus.kind !== 'busy' && <div className="mt-1 text-right">{t('Setze zuerst das Häkchen.')}</div>}
                </div>
              )}
              {statusLine(offStatus)}
              {offNotes.map((n) => (
                <div key={n} className="mt-1 text-[12px]">{n}</div>
              ))}
              {offDone && (
                <div className="mt-2 text-[12px]">
                  <div className="mb-1">{t('Entferne jetzt noch den Connector „Mail Assistant“ in claude.ai, falls du ihn dort eingetragen hattest.')}</div>
                  <div className="flex justify-end">
                    <button type="button" className="bevel" onClick={() => void backend.openLink('https://claude.ai/settings/connectors')}>
                      {t('Einstellungen öffnen')}
                    </button>
                  </div>
                </div>
              )}
              {againMode && !warningOpen && !jumpToTunnel && <div className="mt-2 text-[12px]">{t('Mit „Weiter“ kannst du deine Einstellungen Schritt für Schritt prüfen oder ändern.')}</div>}
              {!againMode && <div className="mt-2 text-[12px]">{t('Du kannst das später ändern: Startmenü → „Einrichtungs-Wizard …“.')}</div>}
            </>
          )}

          {step === 'mailbox' && (
            <>
              <div className="font-bold mb-2">{t('Von welcher Adresse sollen die Mails kommen?')}</div>
              <label className="block mb-1" htmlFor="wz-email">{t('E-Mail-Adresse:')}</label>
              <input id="wz-email" type="email" autoComplete="off" spellCheck={false} value={email} onChange={(e) => onEmail(e.target.value)} className="w-full px-1.5 py-1 bg-white mb-3" style={INPUT} />
              <label className="block mb-1" htmlFor="wz-provider">{t('Anbieter:')}</label>
              <select id="wz-provider" value={providerId} onChange={(e) => pickProvider(e.target.value)} className="w-full px-1.5 py-1 bg-white mb-3" style={INPUT}>
                <option value="" disabled>{t('Bitte wählen …')}</option>
                {PRESETS.map((p) => (
                  <option key={p.id} value={p.id}>{t(p.label)}</option>
                ))}
              </select>
 {blocked && (
                <div className="mb-3 p-2 text-[12px]" role="alert" style={{ background: '#ffffe1', border: '1px solid #000' }}>
                  {t(blocked)}
                </div>
              )}
              {providerId !== '' && (
                <div className="grid gap-2" style={{ gridTemplateColumns: '1fr 80px 110px' }}>
                  <div>
                    <label className="block mb-1" htmlFor="wz-host">Server:</label>
                    <input id="wz-host" value={host} readOnly={providerId !== 'other'} onChange={(e) => setHost(e.target.value)} className="w-full px-1.5 py-1 bg-white" style={{ ...INPUT, opacity: providerId === 'other' ? 1 : 0.75 }} />
                  </div>
                  <div>
                    <label className="block mb-1" htmlFor="wz-port">Port:</label>
                    <input id="wz-port" inputMode="numeric" value={port} readOnly={providerId !== 'other'} onChange={(e) => setPort(e.target.value.replace(/\D/g, ''))} className="w-full px-1.5 py-1 bg-white" style={{ ...INPUT, opacity: providerId === 'other' ? 1 : 0.75 }} />
                  </div>
                  <div>
                    <label className="block mb-1" htmlFor="wz-sec">{t('Verschlüsselung:')}</label>
                    <select id="wz-sec" value={security} disabled={providerId !== 'other'} onChange={(e) => setSecurity(e.target.value as Security)} className="w-full px-1 py-1 bg-white" style={INPUT}>
                      <option value="starttls">STARTTLS</option>
                      <option value="ssl">SSL/TLS</option>
                    </select>
                  </div>
                </div>
              )}
            </>
          )}

          {step === 'password' && (
            <form
              onSubmit={(e) => {
                e.preventDefault()
                void checkPassword()
              }}
            >
              <div className="font-bold mb-2">{t('Passwort für {email}', { email: email.trim() })}</div>
              {preset && <div className="mb-3 text-[12px]">{t(preset.passwordNote)}</div>}
              <label className="block mb-1" htmlFor="wz-pw">{t('Passwort:')}</label>
              <div className="flex gap-1">
              <input
                id="wz-pw"
                ref={pwInput}
                type={showPw ? 'text' : 'password'}
                value={password}
                disabled={pwStatus.kind === 'busy'}
                autoComplete="off"
                autoCorrect="off"
                autoCapitalize="off"
                spellCheck={false}
                onChange={(e) => {
                  setPassword(e.target.value)
                  if (pwStatus.kind !== 'idle') setPwStatus(IDLE)
                }}
                className="w-full px-1.5 py-1 bg-white"
                style={INPUT}
              />
              <button
                type="button"
                className="bevel"
                aria-label={showPw ? t('Passwort verbergen') : t('Passwort anzeigen')}
                aria-pressed={showPw}
                title={showPw ? t('Passwort verbergen') : t('Passwort anzeigen')}
                onClick={() => setShowPw((v) => !v)}
              >
                {showPw ? '🙈' : '👁'}
              </button>
              </div>
              <div className="flex justify-end mt-2">
                <button type="submit" className="bevel font-bold" disabled={!password || pwStatus.kind === 'busy'}>
                  {t('Speichern und prüfen')}
                </button>
              </div>
              {statusLine(pwStatus)}
            </form>
          )}

          {step === 'test' && (
            <>
              <div className="font-bold mb-2">{t('Testmail an dich selbst')}</div>
              <label className="block mb-1" htmlFor="wz-to">{t('Empfänger:')}</label>
              <input id="wz-to" type="email" spellCheck={false} value={testTo} onChange={(e) => { setTestTo(e.target.value); if (testStatus.kind !== 'idle') setTestStatus(IDLE) }} className="w-full px-1.5 py-1 bg-white mb-2" style={INPUT} />
              <label className="flex gap-2 mb-3 cursor-pointer">
                <input type="checkbox" checked={markOwn} onChange={(e) => setMarkOwn(e.target.checked)} />
                <span>{t('Das ist meine eigene Adresse. Mails an sie darf Claude im Chat freigeben; an alle anderen nur ich selbst.')}</span>
              </label>
              <div className="flex justify-end gap-2">
                <button type="button" className="bevel" disabled={testStatus.kind === 'busy'} onClick={() => { setTestSkipped(true); go('claude') }}>
                  {t('Überspringen')}
                </button>
                <button type="button" className="bevel font-bold" disabled={!looksLikeAddress(testTo) || testStatus.kind === 'busy'} onClick={() => void sendTest()}>
                  {t('Testmail senden')}
                </button>
              </div>
              {statusLine(testStatus)}
            </>
          )}

          {step === 'claude' && (
            <>
              <div className="font-bold mb-2">{t('Claude Desktop verbinden')}</div>
              <div className="mb-2">{t('Das passiert beim Klick:')}</div>
              <ul className="list-disc pl-5 mb-3">
                {previewError ? <li style={{ color: '#a01020' }}>{previewError}</li> : preview.length === 0 ? <li>{t('Einen Moment …')}</li> : preview.map((l) => <li key={l}>{l}</li>)}
              </ul>
              <div className="flex justify-end gap-2">
                {claudeStatus.kind === 'ok' && (
                  <button type="button" className="bevel" disabled={removeStatus.kind === 'busy'} onClick={() => void disconnectClaude()}>
                    {t('Aus Claude Desktop entfernen')}
                  </button>
                )}
                <button type="button" className="bevel font-bold" disabled={preview.length === 0 || claudeStatus.kind === 'busy' || claudeStatus.kind === 'ok'} onClick={() => void connectClaude()}>
                  {t('Jetzt verbinden')}
                </button>
              </div>
              {statusLine(claudeStatus)}
              {statusLine(removeStatus)}
            </>
          )}

          {step === 'passkey' && (
            <>
              <div className="font-bold mb-2">{t('Freigabe für Mails an andere (Passkey)')}</div>
              <div className="mb-2">
                {t('Mails an andere Adressen verschickt Mail Assistant erst, wenn du sie auf einer kleinen Seite auf diesem Computer mit einem Passkey (zum Beispiel Touch ID) freigibst. So kann niemand ohne dich im Namen deiner Adresse schreiben, auch Claude nicht.')}
              </div>
              {passkeys === 0 && (
                <>
                  <ol className="list-decimal pl-5 mb-3">
                    <li>{t('Claude Desktop ganz beenden (Cmd+Q auf dem Mac) und neu starten. Danach einen neuen Chat öffnen.')}</li>
                    <li>{t('Hier „Passkey einrichten“ klicken. Die Seite öffnet sich im Browser.')}</li>
                    <li>{t('Dort dem Passkey einen Namen geben, zum Beispiel „MacBook“, und ihn anlegen. Danach erkennt das Fenster ihn selbst, oder du klickst „Prüfen“.')}</li>
                  </ol>
                  <div className="flex gap-2 justify-end">
                    <button type="button" className="bevel font-bold" disabled={pkStatus.kind === 'busy'} onClick={() => void getPasskeyLink()}>
                      {t('Passkey einrichten')}
                    </button>
                    <button type="button" className="bevel" disabled={pkStatus.kind === 'busy'} onClick={() => void checkPasskey()}>
                      {t('Prüfen')}
                    </button>
                  </div>
                </>
              )}
              {statusLine(pkStatus)}
              {pkLink && passkeys === 0 && (
                <div className="mt-2 text-[11px] break-all select-all" style={{ background: '#fff', ...INPUT, padding: 4 }} aria-label={t('Registrierungslink')}>
                  {pkLink}
                </div>
              )}
            </>
          )}

          {step === 'tunnel' && (
            <>
              <div className="font-bold mb-2 flex items-center gap-2">
                <span className="flex-1">{t('Tunnel für das Handy (Tailscale)')}</span>
                <button type="button" className="bevel" aria-expanded={helpOpen} onClick={() => setHelpOpen((v) => !v)}>
                  {helpOpen ? t('Schließen') : t('Wie geht das genau?')}
                </button>
              </div>
              {helpOpen && (
                <div className="mb-3 p-2 text-[12px]" style={{ background: '#ffffe1', border: '1px solid #000' }}>
                  <p className="mb-1"><b>{t('Was ist Tailscale?')}</b> {t('Ein Dienst, der deinen Computer sicher mit dem Internet verbindet. Mail Assistant nutzt davon nur „Funnel“: eine öffentliche Adresse, die zu diesem einen Dienst führt.')}</p>
                  <p className="mb-1"><b>{t('Warum kein eigenes Passwort?')}</b> {t('Tailscale hat keine Anmeldung mit E-Mail und Passwort. Du meldest dich über ein bestehendes Konto an: Apple, Google, Microsoft, GitHub oder ein Passkey.')}</p>
                  <p className="mb-1"><b>{t('Was passiert bei „Tunnel einschalten“?')}</b> {t('Tailscale öffnet bei der ersten Nutzung eine Seite und fragt, ob Funnel eingeschaltet werden soll. Du bestätigst das dort. Danach holt Tailscale ein Zertifikat für die Adresse. Das dauert ein bis zwei Minuten.')}</p>
                  <p className="mb-1">{t('Der Name deines Computers steht danach in öffentlichen Zertifikatslisten. Wenn er etwas Privates verrät, benenne ihn vorher in Tailscale um.')}</p>
                  <p>
                    {t('Mehr dazu:')} <ExtLink backend={backend} url={TAILSCALE.funnelDocs}>Tailscale Funnel</ExtLink>, <ExtLink backend={backend} url={TAILSCALE.httpsDocs}>{t('HTTPS-Zertifikate')}</ExtLink>
                  </p>
                </div>
              )}
              {tunnel === null ? (
                <div>{t('Einen Moment …')}</div>
              ) : tunnel.state === 'ready' && tunnel.address ? (
                <>
                  <div className="mb-2">{t('Dein Computer wäre dann unter dieser Adresse erreichbar:')}</div>
                  <div className="mb-2 text-[12px] break-all select-all" style={{ background: '#fff', ...INPUT, padding: 4 }} aria-label={t('Öffentliche Adresse')}>
                    {tunnel.address}
                  </div>
                  <div className="mb-2 text-[12px]">
                    {t('Die Adresse enthält den Namen deines Computers und ist danach öffentlich sichtbar. Mail Assistant startet dafür den Hintergrunddienst und schaltet bei Tailscale den „Funnel“ ein, nur für diesen einen Dienst.')}
                  </div>
                  <label className="flex gap-2 mb-3 cursor-pointer">
                    <input type="checkbox" checked={tunnelConfirmed} disabled={tunnelResult.kind === 'busy' || tunnelResult.kind === 'ok'} onChange={(e) => setTunnelConfirmed(e.target.checked)} />
                    <span>{t('Ja, diese Adresse darf öffentlich sichtbar sein.')}</span>
                  </label>
                  <div className="flex justify-end gap-2">
                    {tunnelTried && tunnelResult.kind === 'fail' && (
                      <button type="button" className="bevel" onClick={() => void enableTunnel()}>
                        {t('Erneut prüfen')}
                      </button>
                    )}
                    <button type="button" className="bevel font-bold" disabled={!tunnelConfirmed || tunnelResult.kind === 'busy' || tunnelResult.kind === 'ok'} onClick={() => void enableTunnel()}>
                      {t('Tunnel einschalten')}
                    </button>
                  </div>
                  {!tunnelConfirmed && tunnelResult.kind !== 'ok' && tunnelResult.kind !== 'busy' && (
                    <div className="mt-1 text-right text-[12px]">{t('Setze zuerst das Häkchen.')}</div>
                  )}
                </>
              ) : (
                <>
                  <div className="mb-3 p-2 text-[12px]" role="alert" style={{ background: '#ffffe1', border: '1px solid #000' }}>
                    {tunnel.reason}
                  </div>
                  {tunnel.state === 'missing' && (
                    <ol className="list-decimal pl-5 mb-3 text-[12px]">
                      <li>
                        {t('Tailscale laden und installieren:')} <ExtLink backend={backend} url={TAILSCALE.download}>tailscale.com/download</ExtLink>
                      </li>
                      <li>{t('Tailscale öffnen und ein Konto anlegen. Das geht nur über ein bestehendes Konto: Apple, Google, Microsoft, GitHub oder ein Passkey. Nimm für den privaten Gebrauch ein privates Konto. Ein Konto mit eigener Firmen-Domain startet eine 14-Tage-Testphase.')}</li>
                      <li>{t('Danach hier auf „Erneut prüfen“ klicken.')}</li>
                    </ol>
                  )}
                  {tunnel.state === 'no_magicdns' && (
                    <div className="mb-3 text-[12px]">
                      <ExtLink backend={backend} url={TAILSCALE.dns}>{t('DNS-Seite öffnen')}</ExtLink>
                    </div>
                  )}
                  <div className="flex justify-end gap-2">
                    <button type="button" className="bevel font-bold" onClick={() => void loadTunnel()}>
                      {t('Erneut prüfen')}
                    </button>
                  </div>
                </>
              )}
              {statusLine(tunnelResult)}
              {tunnelAdmin && (
                <div className="mt-2 flex justify-end">
                  <button type="button" className="bevel" onClick={() => void backend.openLink(tunnelAdmin)}>
                    {t('Seite bei Tailscale öffnen')}
                  </button>
                </div>
              )}
            </>
          )}

          {step === 'phonelogin' && (
            <>
              <div className="font-bold mb-2">{t('Anmeldung vom Handy')}</div>
              {loginPassphrase === null ? (
                <>
                  <div className="mb-2">
                    {t('Damit nur du dein Handy mit Mail Assistant verbinden kannst, braucht es eine Passphrase. Mail Assistant erzeugt sie für dich und zeigt sie dir')} <b>{t('genau einmal')}</b>{t('. Gespeichert wird nur ein Fingerabdruck davon, nicht die Passphrase selbst.')}
                  </div>
                  {loginExisting && (
                    <div className="mb-2 p-2 text-[12px]" style={{ background: '#ffffe1', border: '1px solid #000' }}>
                      {t('Es gibt schon eine gültige Passphrase. Du musst nichts tun und kannst weitergehen. Nur wenn du sie verloren hast, erzeuge eine neue. Die alte wird dann ungültig und dein Handy muss sich neu anmelden.')}
                    </div>
                  )}
                  <div className="flex justify-end gap-2">
                    <button type="button" className="bevel font-bold" disabled={loginStatus.kind === 'busy'} onClick={() => void createPassphrase()}>
                      {loginExisting ? t('Neue Passphrase erzeugen') : t('Passphrase erzeugen')}
                    </button>
                  </div>
                </>
              ) : (
                <>
                  <div className="mb-1">{t('Deine Passphrase (wird nur jetzt angezeigt):')}</div>
                  <div className="mb-2 text-[15px] break-all select-all font-bold" style={{ background: '#fff', ...INPUT, padding: 6, fontFamily: 'monospace' }} aria-label={t('Passphrase')}>
                    {loginPassphrase}
                  </div>
                  <div className="flex justify-end mb-2">
                    <button type="button" className="bevel" onClick={() => void copy(loginPassphrase, 'pass')}>
                      {copied === 'pass' ? t('Kopiert') : t('Kopieren')}
                    </button>
                  </div>
                  <div className="mb-2 text-[12px]">
                    {t('Speichere sie jetzt in deinem Passwortmanager. Am bequemsten speicherst du sie dort, wo auch dein Handy sie findet, zum Beispiel in der Apple-Passwörter-App oder in einem Passwortmanager mit Handy-App. Hast du dort schon einen Eintrag dafür, ersetze ihn, statt einen zweiten anzulegen. Danach lässt sie sich nicht mehr anzeigen. Geht sie verloren, erzeugst du hier eine neue.')}
                  </div>
                  <label className="flex gap-2 mb-3 cursor-pointer">
                    <input type="checkbox" checked={loginSaved} onChange={(e) => setLoginSaved(e.target.checked)} />
                    <span>{t('Ja, ich habe die Passphrase gespeichert.')}</span>
                  </label>
                </>
              )}
              {statusLine(loginStatus)}
              {copyFailed && (
                <div className="mt-2 text-[12px]" role="alert" style={{ color: '#a01020' }}>
                  {t('Kopieren hat nicht geklappt. Markiere den Text und kopiere ihn von Hand.')}
                </div>
              )}
            </>
          )}

          {step === 'connector' && (
            <>
              <div className="font-bold mb-2">{t('Claude mit deinem Computer verbinden')}</div>
              {connectorUrl ? (
                <>
                  <ol className="list-decimal pl-5 mb-2">
                    <li>
                      {t('In claude.ai die')} <b>{t('Einstellungen → Connectors')}</b> {t('öffnen und „Benutzerdefinierten Connector hinzufügen“ wählen.')}{' '}
                      <button type="button" className="bevel" onClick={() => void backend.openLink('https://claude.ai/settings/connectors')}>
                        {t('Einstellungen öffnen')}
                      </button>
                    </li>
                    <li>{t('Als Name zum Beispiel „Mail Mac“ eintragen (läuft Mail Assistant auf einem eigenen Server, „Mail Server“) und als Adresse diese:')}</li>
                  </ol>
                  <div className="mb-1 break-all select-all" style={{ background: '#fff', ...INPUT, padding: 4 }} aria-label={t('Adresse für den Connector')}>
                    {connectorUrl}
                  </div>
                  <div className="flex justify-end mb-2">
                    <button type="button" className="bevel" onClick={() => void copy(connectorUrl, 'addr')}>
                      {copied === 'addr' ? t('Kopiert') : t('Kopieren')}
                    </button>
                  </div>
                  <div className="mb-2">{t('Beim Anmelden fragt die Seite deines Computers nach der Passphrase. Nimm sie aus deinem Passwortmanager. Danach erscheint der Connector auch in der Claude-App auf dem Handy (dort einen neuen Chat beginnen).')}</div>
                  <div className="mb-2 text-[12px]" role="note" aria-label={t('Hinweis zu mehreren Verbindungen')}>
                    {t('Nur eine Verbindung pro Chat: Ist dieselbe Mail Assistant-Verbindung zusätzlich lokal in Claude Desktop oder ein zweiter Server eingetragen, schalte in einem Chat nur eine davon ein.')}
                  </div>
                  <div className="text-[12px]">{t('Passphrase nicht gespeichert? Mit „Zurück“ kannst du eine neue erzeugen.')}</div>
                </>
              ) : (
                <div>{t('Einen Moment …')}</div>
              )}
              {copyFailed && (
                <div className="mt-2 text-[12px]" role="alert" style={{ color: '#a01020' }}>
                  {t('Kopieren hat nicht geklappt. Markiere den Text und kopiere ihn von Hand.')}
                </div>
              )}
            </>
          )}

          {step === 'phonekey' && (
            <>
              <div className="font-bold mb-2">{t('Freigabe vom Handy (Passkey)')}</div>
              {phoneAtLimit ? (
                <div className="mb-2" role="note">
                  {knownPasskeys(setupInfo?.passkey_list, passkeys, false)} {t('Mehr als 2 gehen nicht. Ist dein Handy dabei, bist du fertig und kannst weitergehen. Wenn nicht, entfernst du zuerst einen in der Verwaltung (Kachel „Passkeys“) und kommst dann über den Wizard hierher zurück.')}
                </div>
              ) : (
                <>
                  <div className="mb-2">
                    {t('Auch am Handy gibst du Mails an andere Adressen selbst frei, mit Face ID oder Fingerabdruck. Der Passkey gehört zur Internetadresse deines Computers und wird deshalb auf dem Handy selbst angelegt.')}
                  </div>
                  <ol className="list-decimal pl-5 mb-3">
                    <li>{t('Hier „Link erzeugen“ klicken.')}</li>
                    <li>{t('Den Link auf das Handy bringen. Am sichersten geht es als Nachricht an dich selbst: Link kopieren, in Nachrichten einfügen und an dich selbst schicken (AirDrop oder eine Notiz gehen auch). Auf dem Handy auf den Link tippen. Er öffnet sich im Browser.')}</li>
                    <li>{t('Dort dem Passkey einen Namen geben, zum Beispiel „iPhone“, und ihn anlegen. Danach erkennt das Fenster ihn selbst.')}</li>
                  </ol>
                  <div className="flex gap-2 justify-end">
                    <button type="button" className="bevel font-bold" disabled={phoneKeyStatus.kind === 'busy' || phoneKeyDone} onClick={() => void getPhoneKeyLink()}>
                      {phoneKeyLink ? t('Neuen Link erzeugen') : t('Link erzeugen')}
                    </button>
                    {phoneKeyLink && !phoneKeyDone && (
                      <button type="button" className="bevel" disabled={phoneKeyStatus.kind === 'busy'} onClick={() => void checkPhoneKey()}>
                        {t('Prüfen')}
                      </button>
                    )}
                  </div>
                </>
              )}
              {statusLine(phoneKeyStatus)}
              {phoneKeyLink && !phoneKeyDone && !phoneAtLimit && (
                <>
                  <div className="mt-2 text-[11px] break-all select-all" style={{ background: '#fff', ...INPUT, padding: 4 }} aria-label={t('Link fürs Handy')}>
                    {phoneKeyLink}
                  </div>
                  <div className="flex justify-end mt-1">
                    <button type="button" className="bevel" onClick={() => void copy(phoneKeyLink, 'link')}>
                      {copied === 'link' ? t('Kopiert') : t('Kopieren')}
                    </button>
                  </div>
                </>
              )}
              {copyFailed && (
                <div className="mt-2 text-[12px]" role="alert" style={{ color: '#a01020' }}>
                  {t('Kopieren hat nicht geklappt. Markiere den Text und kopiere ihn von Hand.')}
                </div>
              )}
            </>
          )}

          {step === 'done' && (
            <>
              <div className="font-bold mb-2">{t('Fertig eingerichtet')}</div>
              <ul className="list-disc pl-5 mb-3">
                <li>{t('Postfach: {email} ({host}, Port {port})', { email: email.trim(), host, port })}</li>
                <li>{t('Passwort liegt im Schlüsselbund dieses Computers')}</li>
                {testSkipped && testStatus.kind !== 'ok' ? (
                  <li>
                    {t('Testmail übersprungen. Nachholen kannst du sie jederzeit: Startmenü → „Einrichtungs-Wizard …“, Schritt „Testmail“. Erst dann gilt deine Adresse als eigene Adresse; bis dahin brauchst du für jede Mail, auch an dich selbst, deine Freigabe auf der Freigabeseite')}
                  </li>
                ) : (
                  <li>{t('Testmail ist angekommen')}{markOwn ? t('; {to} gilt als eigene Adresse', { to: testTo.trim() }) : ''}</li>
                )}
                <li>{t('Claude Desktop ist verbunden')}</li>
                {tunnelResult.kind === 'ok' && tunnel?.address && <li>{t('Tunnel steht: {address}', { address: tunnel.address })}</li>}
                {where === 'phone' && loginUsed && <li>{t('Anmeldung vom Handy: Passphrase festgelegt, Connector in claude.ai eintragen nicht vergessen')}</li>}
                {where === 'phone' && (
                  <li>
                    {phoneKeyDone
                      ? t('Passkey am Handy eingerichtet: Mails an andere Adressen gibst du auch dort frei')
                      : t('Passkey am Handy noch nicht eingerichtet: Mails an andere Adressen lassen sich vom Handy erst danach senden')}
                  </li>
                )}
                <li>
                  {passkeys > 0
                    ? t('Passkey eingerichtet: Mails an andere Adressen gibst du auf der Freigabeseite frei')
                    : t('Passkey noch nicht eingerichtet: Mails an andere Adressen lassen sich erst danach senden')}
                </li>
              </ul>
              {where === 'phone' ? (
                <>
                  <div className="mb-2">{t('Probier es am Handy aus: Schreib Claude in der Claude-App „Schick mir eine Testmail.“')}</div>
                  <div className="mb-2 text-[12px]">{t('Dein Computer ist dein persönlicher Mail-Server. Schläft er oder ist er aus, kann die Handy-App ihn vorübergehend nicht erreichen. Es geht dabei nichts verloren und nichts wird doppelt gesendet.')}</div>
                  <div className="text-[12px]">{t('Diese App brauchst du nur zum Einrichten und Verwalten. Geschrieben und verschickt wird in Claude.')}</div>
                </>
              ) : (
                <>
                  <div className="mb-2">{t('Probier es aus: Schreib Claude in Claude Desktop „Schick mir eine Testmail.“')}</div>
                  <div className="text-[12px]">{t('Diese App brauchst du nur zum Einrichten und Verwalten. Geschrieben und verschickt wird in Claude Desktop.')}</div>
                </>
              )}
            </>
          )}
        </div>

        <div className="flex items-center gap-2 px-4 pb-4">
          <LangSwitch />
          {onSkip && step !== 'done' && (
            <button type="button" className="bevel text-[11px]" onClick={onSkip} title={t('Verlässt den Wizard. Was schon erledigt ist, bleibt erhalten.')}>{t('Zurück zur Verwaltung')}</button>
          )}
          <div className="flex-1" />
          {index > 0 && step !== 'done' && (
            <button type="button" className="bevel" onClick={back}>{t('Zurück')}</button>
          )}
          {step === 'passkey' && passkeys === 0 && (
            <button type="button" className="bevel" onClick={() => go(where === 'phone' ? 'tunnel' : 'done')} title={t('Du kannst den Passkey später nachholen')}>{t('Passkey später einrichten')}</button>
          )}
          {step === 'phonekey' && !phoneKeyDone && !phoneAtLimit && (
            <button type="button" className="bevel" onClick={() => go('done')} title={t('Du kannst den Passkey am Handy später nachholen')}>{t('Passkey am Handy überspringen')}</button>
          )}
          <button type="button" className="bevel font-bold" disabled={!canNext} onClick={next} title={!canNext && step === 'where' ? t('Zuerst den Handy-Zugang abschalten oder oben „Auch vom Handy aus“ wählen.') : undefined}>
            {step === 'done' ? t('Zur Verwaltung') : jumpToTunnel ? t('Handy-Zugang einrichten') : t('Weiter')}
          </button>
        </div>
      </div>

      <div className="pointer-events-none absolute inset-0">
        <div className="pointer-events-auto">
          <Assistant right={24} zIndex={40} hint={hint} />
        </div>
      </div>
    </div>
  )
}
