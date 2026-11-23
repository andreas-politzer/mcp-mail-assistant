import { useEffect, useMemo, useRef, useState } from 'react'
import { Assistant } from '../components/Assistant'
import { HINTS, STEPS, type StepId } from './hints'
import { PRESETS, guessPreset, looksLikeAddress, presetFor, unsupportedReason, type Security } from './presets'
import type { SetupBackend } from './setupApi'

// Setup wizard in the classic window style. It comes before the sign-in: a new installation has neither a
// server nor a passphrase yet. Desktop-only path; the phone path (tunnel) is a later step.
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

export function Wizard({ backend, onDone, onSkip }: { backend: SetupBackend; onDone: () => void; onSkip?: () => void }) {
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
  const [preview, setPreview] = useState<string[]>([])
  const [previewError, setPreviewError] = useState<string | null>(null)
  const [claudeStatus, setClaudeStatus] = useState<Status>(IDLE)
  const [removeStatus, setRemoveStatus] = useState<Status>(IDLE)
  const [showPw, setShowPw] = useState(false)
  const [passkeys, setPasskeys] = useState(0)
  const [pkStatus, setPkStatus] = useState<Status>(IDLE)
  const [pkLink, setPkLink] = useState<string | null>(null)
  const pwInput = useRef<HTMLInputElement>(null)

  const index = STEPS.indexOf(step)
  const preset = providerId ? presetFor(providerId) : null
  const hints = HINTS[step]
  const hint = hints[Math.min(tip, hints.length - 1)]

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
  }, [step]) // eslint-disable-line react-hooks/exhaustive-deps

  // Look at the real state once: what is already set up is not asked for again.
  useEffect(() => {
    let alive = true
    void backend.status().then((st) => {
      if (!alive || !st.mailbox) return
      setEmail(st.mailbox.email)
      const known = PRESETS.find((p) => p.host === st.mailbox!.host)
      setProviderId(known ? known.id : 'other')
      setHost(st.mailbox.host)
      setPort(String(st.mailbox.port))
      setSecurity(st.mailbox.security)
      if (st.password_stored) setPwStatus({ kind: 'ok', text: 'Das Passwort liegt schon im Schlüsselbund. Du kannst weitergehen oder es neu eingeben.' })
      if (st.claude_connected) setClaudeStatus({ kind: 'ok', text: 'Claude Desktop ist schon verbunden.' })
      setPasskeys(st.passkeys)
      if (st.passkeys > 0) setPkStatus({ kind: 'ok', text: 'Ein Passkey ist schon registriert.' })
    })
    return () => {
      alive = false
    }
  }, [backend])

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
      setPwStatus({ kind: 'ok', text: 'Passwort im Schlüsselbund gespeichert, Anmeldung beim Anbieter geklappt.' })
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
    setTestStatus(r.ok ? { kind: 'ok', text: `Testmail an ${testTo.trim()} ist raus. Schau in dein Postfach.` } : { kind: 'fail', text: r.reason })
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
        ? 'Die Seite wurde im Browser geöffnet. Lege dort den Passkey an und klicke danach hier auf „Prüfen“. Der Link gilt einmal, 10 Minuten.'
        : `${opened.reason} Der Link gilt einmal, 10 Minuten.`,
    })
  }

  const checkPasskey = async () => {
    if (pkStatus.kind === 'busy') return
    setPkStatus({ kind: 'busy' })
    const st = await backend.status()
    setPasskeys(st.passkeys)
    setPkStatus(
      st.passkeys > 0
        ? { kind: 'ok', text: 'Passkey ist registriert. Mails an andere kannst du ab jetzt freigeben.' }
        : { kind: 'fail', text: 'Es ist noch kein Passkey registriert. Hast du den Vorgang im Browser abgeschlossen?' },
    )
  }

  const connectClaude = async () => {
    if (claudeStatus.kind === 'busy') return
    setClaudeStatus({ kind: 'busy' })
    const r = await backend.connectClaude()
    setClaudeStatus(r.ok ? { kind: 'ok', text: 'Verbunden. Claude Desktop jetzt ganz beenden und neu starten.' } : { kind: 'fail', text: r.reason })
  }

  const disconnectClaude = async () => {
    if (removeStatus.kind === 'busy') return
    setRemoveStatus({ kind: 'busy' })
    const r = await backend.disconnectClaude()
    if (r.ok) {
      setClaudeStatus(IDLE)
      setRemoveStatus({ kind: 'ok', text: 'Die Verbindung zu Claude Desktop ist entfernt. Deine Empfänger und Einstellungen bleiben erhalten. Claude Desktop jetzt ganz beenden (Cmd+Q) und neu starten.' })
    } else {
      setRemoveStatus({ kind: 'fail', text: r.reason })
    }
  }

  const canNext = useMemo(() => {
    switch (step) {
      case 'where':
        return where === 'here'
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
      default:
        return true
    }
  }, [step, where, mailboxReady, pwStatus.kind, testStatus.kind, claudeStatus.kind, passkeys])

  const back = () => index > 0 && go(STEPS[index - 1])
  const next = () => (step === 'done' ? onDone() : go(STEPS[index + 1]))

  const statusLine = (s: Status) =>
    s.kind === 'idle' ? null : (
      <div className="mt-2 text-[12px]" role={s.kind === 'fail' ? 'alert' : 'status'} style={{ color: s.kind === 'fail' ? '#a01020' : s.kind === 'ok' ? '#0a6a2a' : '#1d1d2b' }}>
        {s.kind === 'busy' ? 'Einen Moment …' : s.text}
      </div>
    )

  return (
    <div className="fixed inset-0 flex items-center justify-center md:pr-[330px]" style={{ zIndex: 30, background: 'linear-gradient(#3a6ea5, #9cc7e8)' }} data-wizard>
      <div style={{ width: 480, ...FRAME }} role="dialog" aria-label="Einrichtung">
        <div className="px-2 py-0.5 m-0.5 select-none font-bold text-[13px] flex" style={{ background: 'linear-gradient(90deg, #0a246a, #3a6ea5)', color: '#fff' }}>
          <span className="flex-1">Einrichtung – Mail Assistant</span>
          <span className="font-normal">Schritt {index + 1} von {STEPS.length}</span>
        </div>

        <div className="p-4 text-[13px] leading-relaxed" style={{ minHeight: 300 }}>
          {step === 'where' && (
            <>
              <div className="font-bold mb-2">Wo willst du Mail Assistant benutzen?</div>
              <label className="flex gap-2 mb-2 cursor-pointer">
                <input type="radio" name="where" checked={where === 'here'} onChange={() => setWhere('here')} />
                <span>
                  <b>Nur an diesem Computer</b>
                  <br />
                  Du schreibst mit Claude Desktop auf diesem Rechner. Mehr brauchst du nicht.
                </span>
              </label>
              <label className="flex gap-2 mb-2 cursor-pointer" style={{ opacity: 0.6 }}>
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
                  <b>Auch vom Handy aus</b> (kommt später)
                  <br />
                  Dafür richten wir einen Tunnel ein. Das geht jederzeit nachträglich.
                </span>
              </label>
              {where === 'phone' && (
                <div className="mt-2 p-2 text-[12px]" style={{ background: '#ffffe1', border: '1px solid #000' }}>
                  Die Handy-Einrichtung ist noch nicht freigeschaltet. Fang mit „Nur an diesem Computer“ an; später kannst du hier einfach wechseln.
                </div>
              )}
            </>
          )}

          {step === 'mailbox' && (
            <>
              <div className="font-bold mb-2">Von welcher Adresse sollen die Mails kommen?</div>
              <label className="block mb-1" htmlFor="wz-email">E-Mail-Adresse:</label>
              <input id="wz-email" type="email" autoComplete="off" spellCheck={false} value={email} onChange={(e) => onEmail(e.target.value)} className="w-full px-1.5 py-1 bg-white mb-3" style={INPUT} />
              <label className="block mb-1" htmlFor="wz-provider">Anbieter:</label>
              <select id="wz-provider" value={providerId} onChange={(e) => pickProvider(e.target.value)} className="w-full px-1.5 py-1 bg-white mb-3" style={INPUT}>
                <option value="" disabled>Bitte wählen …</option>
                {PRESETS.map((p) => (
                  <option key={p.id} value={p.id}>{p.label}</option>
                ))}
              </select>
 {blocked && (
                <div className="mb-3 p-2 text-[12px]" role="alert" style={{ background: '#ffffe1', border: '1px solid #000' }}>
                  {blocked}
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
                    <label className="block mb-1" htmlFor="wz-sec">Verschlüsselung:</label>
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
              <div className="font-bold mb-2">Passwort für {email.trim()}</div>
              {preset && <div className="mb-3 text-[12px]">{preset.passwordNote}</div>}
              <label className="block mb-1" htmlFor="wz-pw">Passwort:</label>
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
                aria-label={showPw ? 'Passwort verbergen' : 'Passwort anzeigen'}
                aria-pressed={showPw}
                title={showPw ? 'Passwort verbergen' : 'Passwort anzeigen'}
                onClick={() => setShowPw((v) => !v)}
              >
                {showPw ? '🙈' : '👁'}
              </button>
              </div>
              <div className="flex justify-end mt-2">
                <button type="submit" className="bevel font-bold" disabled={!password || pwStatus.kind === 'busy'}>
                  Speichern und prüfen
                </button>
              </div>
              {statusLine(pwStatus)}
            </form>
          )}

          {step === 'test' && (
            <>
              <div className="font-bold mb-2">Testmail an dich selbst</div>
              <label className="block mb-1" htmlFor="wz-to">Empfänger:</label>
              <input id="wz-to" type="email" spellCheck={false} value={testTo} onChange={(e) => { setTestTo(e.target.value); if (testStatus.kind !== 'idle') setTestStatus(IDLE) }} className="w-full px-1.5 py-1 bg-white mb-2" style={INPUT} />
              <label className="flex gap-2 mb-3 cursor-pointer">
                <input type="checkbox" checked={markOwn} onChange={(e) => setMarkOwn(e.target.checked)} />
                <span>Das ist meine eigene Adresse. Mails an sie darf Claude im Chat freigeben; an alle anderen nur ich selbst.</span>
              </label>
              <div className="flex justify-end gap-2">
                <button type="button" className="bevel" disabled={testStatus.kind === 'busy'} onClick={() => { setTestSkipped(true); go('claude') }}>
                  Überspringen
                </button>
                <button type="button" className="bevel font-bold" disabled={!looksLikeAddress(testTo) || testStatus.kind === 'busy'} onClick={() => void sendTest()}>
                  Testmail senden
                </button>
              </div>
              {statusLine(testStatus)}
            </>
          )}

          {step === 'claude' && (
            <>
              <div className="font-bold mb-2">Claude Desktop verbinden</div>
              <div className="mb-2">Das passiert beim Klick:</div>
              <ul className="list-disc pl-5 mb-3">
                {previewError ? <li style={{ color: '#a01020' }}>{previewError}</li> : preview.length === 0 ? <li>Einen Moment …</li> : preview.map((l) => <li key={l}>{l}</li>)}
              </ul>
              <div className="flex justify-end gap-2">
                {claudeStatus.kind === 'ok' && (
                  <button type="button" className="bevel" disabled={removeStatus.kind === 'busy'} onClick={() => void disconnectClaude()}>
                    Aus Claude Desktop entfernen
                  </button>
                )}
                <button type="button" className="bevel font-bold" disabled={preview.length === 0 || claudeStatus.kind === 'busy' || claudeStatus.kind === 'ok'} onClick={() => void connectClaude()}>
                  Jetzt verbinden
                </button>
              </div>
              {statusLine(claudeStatus)}
              {statusLine(removeStatus)}
            </>
          )}

          {step === 'passkey' && (
            <>
              <div className="font-bold mb-2">Freigabe für Mails an andere (Passkey)</div>
              <div className="mb-2">
                Mails an andere Adressen verschickt Mail Assistant erst, wenn du sie auf einer kleinen Seite auf diesem Computer mit einem Passkey (zum Beispiel Touch ID) freigibst. So kann niemand ohne dich im Namen deiner Adresse schreiben, auch Claude nicht.
              </div>
              <ol className="list-decimal pl-5 mb-3">
                <li>Claude Desktop ganz beenden (Cmd+Q auf dem Mac) und neu starten. Danach einen neuen Chat öffnen.</li>
                <li>Hier „Passkey einrichten“ klicken. Die Seite öffnet sich im Browser.</li>
                <li>Dort den Passkey anlegen, dann hierher zurückkommen und „Prüfen“ klicken.</li>
              </ol>
              <div className="flex gap-2 justify-end">
                <button type="button" className="bevel font-bold" disabled={pkStatus.kind === 'busy'} onClick={() => void getPasskeyLink()}>
                  Passkey einrichten
                </button>
                <button type="button" className="bevel" disabled={pkStatus.kind === 'busy'} onClick={() => void checkPasskey()}>
                  Prüfen
                </button>
              </div>
              {statusLine(pkStatus)}
              {pkLink && (
                <div className="mt-2 text-[11px] break-all select-all" style={{ background: '#fff', ...INPUT, padding: 4 }} aria-label="Registrierungslink">
                  {pkLink}
                </div>
              )}
            </>
          )}

          {step === 'done' && (
            <>
              <div className="font-bold mb-2">Fertig eingerichtet</div>
              <ul className="list-disc pl-5 mb-3">
                <li>Postfach: {email.trim()} ({host}, Port {port})</li>
                <li>Passwort liegt im Schlüsselbund dieses Computers</li>
                {testSkipped && testStatus.kind !== 'ok' ? (
                  <li>
                    Testmail übersprungen. Nachholen kannst du sie jederzeit: Startmenü → „Einrichtungs-Wizard …“, Schritt „Testmail“. Erst dann gilt deine
                    Adresse als eigene Adresse; bis dahin brauchst du für jede Mail, auch an dich selbst, deine Freigabe auf der Freigabeseite
                  </li>
                ) : (
                  <li>Testmail ist angekommen{markOwn ? `; ${testTo.trim()} gilt als eigene Adresse` : ''}</li>
                )}
                <li>Claude Desktop ist verbunden</li>
                <li>
                  {passkeys > 0
                    ? 'Passkey eingerichtet: Mails an andere Adressen gibst du auf der Freigabeseite frei'
                    : 'Passkey noch nicht eingerichtet: Mails an andere Adressen lassen sich erst danach senden'}
                </li>
              </ul>
              <div className="mb-2">Probier es aus: Schreib Claude in Claude Desktop „Schick mir eine Testmail.“</div>
              <div className="text-[12px]">Diese App brauchst du nur zum Einrichten und Verwalten. Geschrieben und verschickt wird in Claude Desktop.</div>
            </>
          )}
        </div>

        <div className="flex items-center gap-2 px-4 pb-4">
          {onSkip && step !== 'done' && (
            <button type="button" className="bevel" onClick={onSkip} title="Verlässt den Wizard. Was schon erledigt ist, bleibt erhalten.">Zurück zur Verwaltung</button>
          )}
          {hints.length > 1 && (
            <button type="button" className="bevel" onClick={() => setTip((t) => (t + 1) % hints.length)}>Noch ein Tipp</button>
          )}
          <div className="flex-1" />
          {index > 0 && step !== 'done' && (
            <button type="button" className="bevel" onClick={back}>Zurück</button>
          )}
          {step === 'passkey' && passkeys === 0 && (
            <button type="button" className="bevel" onClick={() => go('done')} title="Du kannst den Passkey später nachholen">Später</button>
          )}
          <button type="button" className="bevel font-bold" disabled={!canNext} onClick={next}>
            {step === 'done' ? 'Zur Verwaltung' : 'Weiter'}
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
