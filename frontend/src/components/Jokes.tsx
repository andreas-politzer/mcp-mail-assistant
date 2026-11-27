import { t, useLang } from '../i18n'
import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Win95Window } from './Win95Window'
import { invoke } from '@tauri-apps/api/core'
import { admin } from '../api/admin'

export type JokeKind =
  | 'outbox' | 'internet' | 'coffee' | 'help' | 'about' | 'shutdown'
  | 'computer' | 'chat' | 'typewriter' | 'sheet' | 'paint' | 'floppy'
  | 'solitaire' | 'mines' | 'freecell' | 'hearts' | 'chess'
  | 'chaos-text' | 'chaos-sheet' | 'chaos-net' | 'chaos-note' | 'chaos-404' | 'chaos-ask'
  | 'doc-shop' | 'doc-oma' | 'doc-ideas'

interface Common {
  onClose: () => void
  onFocus?: () => void
  initial?: { x: number; y: number }
  onOpen: (k: JokeKind) => void
}

const Row = ({ children }: { children: ReactNode }) => <div className="flex justify-end gap-2 mt-3">{children}</div>
const sunken = { border: '2px solid', borderColor: 'var(--bevel-dark) var(--bevel-light) var(--bevel-light) var(--bevel-dark)', background: '#fff' } as const

function Note({ title, text, button = 'Okay', width = 360, c }: { title: string; text: string; button?: string; width?: number; c: Common }) {
  return (
    <Win95Window title={title} width={width} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div>{t(text)}</div>
      <Row><button className="bevel font-bold" onClick={c.onClose}>{t(button)}</button></Row>
    </Win95Window>
  )
}

function Modem({ c }: { c: Common }) {
  const lines = [t('Wähle 040 123 456 …'), t('Modem meldet sich (14.400 Baud) …'), t('Benutzername und Kennwort werden geprüft …'), t('Verbindung fehlgeschlagen.')]
  const [shown, setShown] = useState(1)
  const [giveUp, setGiveUp] = useState(false)
  useEffect(() => {
    if (shown >= lines.length) return
    const t = setTimeout(() => setShown((n) => n + 1), 1000)
    return () => clearTimeout(t)
  }, [shown, lines.length])
  return (
    <Win95Window title={t('Willkommen im Internet!')} width={380} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      {!giveUp ? (
        <>
          <div className="font-mono text-[12px] min-h-[84px]">{lines.slice(0, shown).map((l) => <div key={l}>{l}</div>)}</div>
          {shown >= lines.length && <Row><button className="bevel" onClick={() => setGiveUp(true)}>{t('Erneut versuchen')}</button></Row>}
        </>
      ) : (
        <>
          <div>{t('Ach, vergiss es. Schreib lieber eine E-Mail.')}</div>
          <Row><button className="bevel font-bold" onClick={c.onClose}>{t('Mach ich')}</button></Row>
        </>
      )}
    </Win95Window>
  )
}

function Coffee({ c }: { c: Common }) {
  const [pct, setPct] = useState(0)
  useEffect(() => {
    if (pct >= 100) return
    const t = setTimeout(() => setPct((p) => Math.min(100, p + 10)), 300)
    return () => clearTimeout(t)
  }, [pct])
  return (
    <Win95Window title={t('Kaffeepause')} width={340} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div className="mb-2">{pct < 100 ? t('Kaffee wird gekocht …') : t('Fertig. Die Pause ist leider vorbei.')}</div>
      <div className="h-4" style={sunken}><div className="h-full" style={{ width: `${pct}%`, background: '#0a246a' }} /></div>
      <Row><button className="bevel" onClick={c.onClose}>{pct < 100 ? t('Abbrechen') : t('Okay')}</button></Row>
    </Win95Window>
  )
}

function Shutdown({ c }: { c: Common }) {
  const [closing, setClosing] = useState(false)
  const quit = async () => {
    setClosing(true)
    // Sign out first so the server session ends; the app closes even if the server does not answer.
    try {
      await admin.logout()
    } catch {
      /* the session ends by itself after the idle time */
    }
    try {
      await invoke('quit_app')
    } catch {
      window.close()
    }
  }
  return (
    <Win95Window title={t('Herunterfahren')} width={350} onClose={closing ? () => {} : c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div>{closing ? t('Mail Assistant wird beendet …') : t('Mail Assistant beenden? Du wirst abgemeldet, und das Fenster schließt sich.')}</div>
      {!closing && (
        <Row>
          <button className="bevel" onClick={() => void quit()}>{t('Beenden')}</button>
          <button className="bevel font-bold" onClick={c.onClose}>{t('Abbrechen')}</button>
        </Row>
      )}
    </Win95Window>
  )
}

function Computer({ c }: { c: Common }) {
  return (
    <Win95Window title={t('Mein Computer')} width={360} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div className="p-2" style={sunken}>
        <div>{t('Prozessor: 133 MHz (tapfer)')}</div>
        <div>{t('Arbeitsspeicher: 16 MB (reicht für alles)')}</div>
        <div>{t('Festplatte: 540 MB, davon frei: 538 MB')}</div>
        <div>{t('Bildschirm: 640 × 480, 256 Farben')}</div>
        <div>{t('Stimmung: gut')}</div>
      </div>
      <Row><button className="bevel font-bold" onClick={c.onClose}>{t('Okay')}</button></Row>
    </Win95Window>
  )
}

function Chat({ c }: { c: Common }) {
  return (
    <Win95Window title={t('Plauderecke')} width={400} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div className="flex gap-2">
        <div className="w-32 p-1.5 text-[12px]" style={sunken}>
          <div>● {t('Funki (online)')}</div>
          <div>◐ {t('Der Drucker (beschäftigt)')}</div>
          <div>○ {t('Kaffeemaschine (abwesend)')}</div>
        </div>
        <div className="flex-1 p-1.5 text-[12px] min-h-[96px]" style={sunken}>
          <div><b>Funki:</b> {t('Sie haben eine Mail!')}</div>
          <div><b>{t('Sie:')}</b> {t('Von wem?')}</div>
          <div><b>Funki:</b> {t('Das wollte ich gerade fragen.')}</div>
        </div>
      </div>
      <Row><button className="bevel" onClick={c.onClose}>{t('Abmelden')}</button></Row>
    </Win95Window>
  )
}

function Typewriter({ c, title = 'Unbenannt – Schreibmaschine', text = 'Hello, World!\n\n' }: { c: Common; title?: string; text?: string }) {
  return (
    <Win95Window title={title} width={380} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <textarea defaultValue={t(text)} rows={8} className="w-full p-1.5 text-[13px] outline-none resize-none" style={{ ...sunken, fontFamily: '"Courier New", monospace' }} />
    </Win95Window>
  )
}

function Sheet({ c, title = 'Zahlenwerk – Tabelle1' }: { c: Common; title?: string }) {
  const cell = { border: '1px solid #8a8a99', padding: '1px 6px', textAlign: 'right' as const }
  const rows = [[t('Kaffee'), 3, 4, 5], [t('Mails'), 12, 7, 20], [t('Ideen'), 1, 0, 17], [t('Summe'), 16, 11, 42]]
  return (
    <Win95Window title={title} width={360} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div style={sunken}>
        <table className="w-full text-[12px]" style={{ borderCollapse: 'collapse' }}>
          <thead><tr>{['', t('Mo'), t('Di'), t('Mi')].map((h) => <th key={h} style={{ ...cell, background: 'var(--face)' }}>{h}</th>)}</tr></thead>
          <tbody>{rows.map((r) => <tr key={String(r[0])}>{r.map((v, i) => <td key={i} style={{ ...cell, textAlign: i === 0 ? 'left' : 'right', fontWeight: r[0] === t('Summe') ? 700 : 400 }}>{v}</td>)}</tr>)}</tbody>
        </table>
      </div>
      <div className="text-[11px] mt-2 opacity-80">{t('Die Summe stimmt immer. Meistens ist es 42.')}</div>
    </Win95Window>
  )
}

function Paint({ c }: { c: Common }) {
  const ref = useRef<HTMLCanvasElement>(null)
  const [color, setColor] = useState('#000000')
  const last = useRef<{ x: number; y: number } | null>(null)
  const pos = (e: React.PointerEvent) => {
    const r = ref.current!.getBoundingClientRect()
    return { x: e.clientX - r.left, y: e.clientY - r.top }
  }
  const draw = (e: React.PointerEvent) => {
    if (!last.current) return
    const ctx = ref.current!.getContext('2d')!
    const p = pos(e)
    ctx.strokeStyle = color
    ctx.lineWidth = 3
    ctx.lineCap = 'round'
    ctx.beginPath()
    ctx.moveTo(last.current.x, last.current.y)
    ctx.lineTo(p.x, p.y)
    ctx.stroke()
    last.current = p
  }
  const clear = () => {
    const cv = ref.current!
    cv.getContext('2d')!.clearRect(0, 0, cv.width, cv.height)
  }
  return (
    <Win95Window title={t('Unbenannt – Pixelmaler')} width={330} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <canvas
        ref={ref}
        width={300}
        height={170}
        className="touch-none cursor-crosshair"
        style={sunken}
        onPointerDown={(e) => { (e.target as HTMLElement).setPointerCapture(e.pointerId); last.current = pos(e) }}
        onPointerMove={draw}
        onPointerUp={() => { last.current = null }}
      />
      <div className="flex items-center gap-1 mt-2">
        {['#000000', '#d6334c', '#2f6fd0', '#3fae5f', '#f5d442'].map((col) => (
          <button key={col} aria-label={col} onClick={() => setColor(col)} className="w-5 h-5" style={{ background: col, border: color === col ? '2px solid #fff' : '2px solid var(--bevel-dark)', outline: color === col ? '1px solid #000' : 'none' }} />
        ))}
        <button className="bevel ml-auto" onClick={clear}>{t('Löschen')}</button>
      </div>
    </Win95Window>
  )
}

function Floppy({ c }: { c: Common }) {
  const [n, setN] = useState(2)
  return (
    <Win95Window title={t('Diskettenbox')} width={350} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div>{t('Bitte legen Sie Diskette {n} von 47 ein.', { n })}{n > 2 && ` ${t('Das kann dauern.')}`}</div>
      <Row><button className="bevel" onClick={() => setN((v) => v + 1)}>{t('Wiederholen')}</button><button className="bevel font-bold" onClick={c.onClose}>{t('Abbrechen')}</button></Row>
    </Win95Window>
  )
}

function Mines({ c }: { c: Common }) {
  const N = 8
  const make = () => {
    const m = new Set<number>()
    while (m.size < 10) m.add(Math.floor(Math.random() * N * N))
    return m
  }
  const [mines, setMines] = useState(make)
  const [seen, setSeen] = useState<Set<number>>(new Set())
  const [dead, setDead] = useState(false)
  const count = (i: number) => {
    const x = i % N, y = Math.floor(i / N)
    let n = 0
    for (let dx = -1; dx <= 1; dx++) for (let dy = -1; dy <= 1; dy++) {
      const nx = x + dx, ny = y + dy
      if ((dx || dy) && nx >= 0 && ny >= 0 && nx < N && ny < N && mines.has(ny * N + nx)) n++
    }
    return n
  }
  const won = !dead && seen.size === N * N - mines.size
  const click = (i: number) => {
    if (dead || won) return
    if (mines.has(i)) return setDead(true)
    setSeen((s) => new Set(s).add(i))
  }
  const reset = () => { setMines(make()); setSeen(new Set()); setDead(false) }
  const colors = ['', '#2f6fd0', '#2e8b57', '#d6334c', '#0a246a']
  return (
    <Win95Window title={t('Minenfeld')} width={250} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div className="grid mx-auto" style={{ gridTemplateColumns: `repeat(${N}, 24px)`, width: N * 24 }}>
        {Array.from({ length: N * N }, (_, i) => {
          const open = seen.has(i) || (dead && mines.has(i))
          const n = count(i)
          return (
            <button key={i} onClick={() => click(i)} className={open ? '' : 'bevel'} style={{ width: 24, height: 24, padding: 0, fontSize: 13, fontWeight: 700, color: colors[Math.min(n, 4)], background: open ? '#c8c8d4' : undefined, border: open ? '1px solid #8a8a99' : undefined }}>
              {open ? (mines.has(i) ? '✹' : n || '') : ''}
            </button>
          )
        })}
      </div>
      <div className="mt-2 min-h-[36px] text-[12px]">
        {dead && t('Sie haben eine Mine gefunden. Herzlichen Glückwunsch. Das war nicht das Ziel.')}
        {won && t('Alle sicheren Felder gefunden. Unglaublich.')}
        {!dead && !won && t('Finden Sie alle sicheren Felder. Zehn Minen sind versteckt.')}
      </div>
      <Row><button className="bevel" onClick={reset}>{t('Neu')}</button><button className="bevel font-bold" onClick={c.onClose}>{t('Schließen')}</button></Row>
    </Win95Window>
  )
}

export function Joke({ kind, ...c }: { kind: JokeKind } & Common) {
  useLang()
  switch (kind) {
    case 'internet': return <Modem c={c} />
    case 'coffee': return <Coffee c={c} />
    case 'shutdown': return <Shutdown c={c} />
    case 'computer': return <Computer c={c} />
    case 'chat': return <Chat c={c} />
    case 'typewriter': return <Typewriter c={c} />
    case 'sheet': return <Sheet c={c} />
    case 'paint': return <Paint c={c} />
    case 'floppy': return <Floppy c={c} />
    case 'mines': return <Mines c={c} />
    case 'doc-shop': return <Typewriter c={c} title="Einkaufszettel – Schreibmaschine" text={'Kaffee\nKaffee\nFilter\nNoch mehr Kaffee\nIrgendwas Gesundes (optional)'} />
    case 'doc-oma': return <Typewriter c={c} title="Brief an Oma – Schreibmaschine" text={'Liebe Oma,\n\nich habe jetzt einen Computer, der E-Mails schreibt.\nGeschickt wird aber nur, was ich erlaube.\n\nDein Enkel'} />
    case 'doc-ideas': return <Typewriter c={c} title="Ideen für später – Schreibmaschine" text={'1. Funki ein Hütchen geben\n2. Weltherrschaft (vielleicht)\n3. Backup prüfen'} />
    case 'outbox': return <Note c={c} title="Postausgang" text="Der Postausgang ist leer. Claude schreibt nur, wenn du im Chat Ja sagst." />
    case 'help': return <Note c={c} title="Hilfe" button="Danke" text="Hilfe? Na ja … Öffne über Start die Empfänger oder Dateien. Alles andere findest du selbst heraus. Oder du fragst Claude oder Funki." />
    case 'about': return <Note c={c} title="Über Mail Assistant" button="Schön" text="Mail Assistant · Version 0.1. Gebaut mit Claude und viel Geduld." />
    case 'solitaire': return <Note c={c} title="Kartensolitär" button="Ich schaffe das" text="Noch 47 Karten. Sie schaffen das." />
    case 'freecell': return <Note c={c} title="Freie Zellen" text="Alle Zellen sind frei. Nur Ihr Terminkalender nicht." />
    case 'hearts': return <Note c={c} title="Herzblatt" button="Mach ich" text="Sie haben noch keine Herzen gesammelt. Versuchen Sie es mit einer freundlichen Mail." />
    case 'chess': return <Note c={c} title="Schachclub" button="Verstanden" text="Der Springer bittet um Bedenkzeit. Der Turm ist beleidigt." />
    case 'chaos-text': return <Typewriter c={c} title="Unbenannt – Schreibmaschine" text={'Sehr geehrte Damen und Herren,\n\nhiermit'} />
    case 'chaos-sheet': return <Sheet c={c} title="Zahlenwerk – Tabelle1" />
    case 'chaos-net': return <Note c={c} title="Internet-Verbindung" button="Warten" text="Verbindung wird hergestellt … (seit 3 Stunden)" />
    case 'chaos-note': return <Note c={c} title="Wichtige Mitteilung" text="Es gibt keine wichtige Mitteilung. Bitte nicht erschrecken." />
    case 'chaos-404': return <Note c={c} title="Fehler 404 – Kaffee nicht gefunden" button="Erneut versuchen" text="Die Kaffeemaschine antwortet nicht. Bitte erneut versuchen." />
    case 'chaos-ask':
      return (
        <Win95Window title={t('Hinweis')} width={360} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
          <div>{t('Es sind möglicherweise zu viele Fenster geöffnet. Möchten Sie das wirklich?')}</div>
          <Row><button className="bevel font-bold" onClick={c.onClose}>{t('Ja, natürlich!')}</button><button className="bevel" onClick={() => { c.onClose(); c.onOpen('help') }}>{t('Hilfe!')}</button></Row>
        </Win95Window>
      )
  }
}
