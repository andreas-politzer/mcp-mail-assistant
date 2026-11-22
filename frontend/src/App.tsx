import { useEffect, useMemo, useRef, useState } from 'react'
import { Desktop, type Wallpaper } from './components/Desktop'
import { Taskbar } from './components/Taskbar'
import { ConfirmDialog } from './components/ConfirmDialog'
import { DraggableGlass } from './components/DraggableGlass'
import { DesktopIcon } from './components/DesktopIcon'
import { Assistant } from './components/Assistant'
import { Clock } from './components/Clock'
import { Sticky } from './components/Sticky'
import { DisplayProps } from './components/DisplayProps'
import { RunDialog } from './components/RunDialog'
import { Joke, type JokeKind } from './components/Jokes'
import type { StartCommand } from './components/StartMenu'
import {
  BIN, CELLS, CHAT, COMPUTER, ENVELOPE, FLOPPY, FOLDER, GLOBE, HEART_CARD, MINE, NOTEPAD, PAINT, PAWN, SHEET, SPADE_CARD, PixelSprite,
} from './components/PixelSprite'
import { HelpTile } from './tiles/HelpTile'
import { levelOf, raised } from './core/stacking'
import { RecipientsTile } from './tiles/RecipientsTile'
import { FilesTile } from './tiles/FilesTile'
import { SettingsTile } from './tiles/SettingsTile'
import { ActivityTile } from './tiles/ActivityTile'
import type { ConfirmRequest } from './core/types'
import { Boot } from './boot/Boot'
import { admin } from './api/admin'
import { Wizard } from './wizard/Wizard'
import { mockBackend, type SetupBackend } from './wizard/setupApi'
import { tauriBackend } from './wizard/tauriBackend'
import { PasskeysTile } from './local/PasskeysTile'
import { tauriTransport } from './api/localTransport'
import { mockLocalBackend, tauriLocalBackend, type LocalBackend } from './local/localApi'

type WinKind = JokeKind | 'clock' | 'sticky' | 'display' | 'run'
interface Win {
  id: number
  kind: WinKind
  initial?: { x: number; y: number }
}

type Sprite = { rows: string[]; palette: Record<string, string> }
type IconAction = 'recipients' | 'files' | 'bin' | JokeKind
const ICONS: { id: string; label: string; sprite: Sprite; action: IconAction }[] = [
  { id: 'computer', label: 'Mein Computer', sprite: COMPUTER, action: 'computer' },
  { id: 'mailbox', label: 'Briefkasten', sprite: ENVELOPE, action: 'recipients' },
  { id: 'files', label: 'Meine Dateien', sprite: FOLDER, action: 'files' },
  { id: 'internet', label: 'Internet-Erkunder', sprite: GLOBE, action: 'internet' },
  { id: 'chat', label: 'Plauderecke', sprite: CHAT, action: 'chat' },
  { id: 'typewriter', label: 'Schreibmaschine', sprite: NOTEPAD, action: 'typewriter' },
  { id: 'sheet', label: 'Zahlenwerk', sprite: SHEET, action: 'sheet' },
  { id: 'paint', label: 'Pixelmaler', sprite: PAINT, action: 'paint' },
  { id: 'solitaire', label: 'Kartensolitär', sprite: SPADE_CARD, action: 'solitaire' },
  { id: 'mines', label: 'Minenfeld', sprite: MINE, action: 'mines' },
  { id: 'freecell', label: 'Freie Zellen', sprite: CELLS, action: 'freecell' },
  { id: 'hearts', label: 'Herzblatt', sprite: HEART_CARD, action: 'hearts' },
  { id: 'chess', label: 'Schachclub', sprite: PAWN, action: 'chess' },
  { id: 'floppy', label: 'Diskettenbox', sprite: FLOPPY, action: 'floppy' },
  { id: 'bin', label: 'Papierkorb', sprite: BIN, action: 'bin' },
]

// Clock and sticky note in their home spots (top right).
function defaultWins(): Win[] {
  const w = window.innerWidth
  return [
    { id: nextWinId++, kind: 'clock', initial: { x: Math.max(8, w - 170), y: 44 } },
    { id: nextWinId++, kind: 'sticky', initial: { x: Math.max(8, w - 190), y: 232 } },
  ]
}
let nextWinId = 1

// Order of the clickable desktop elements, back to front, when the desktop opens: the playful pieces
// at the back, the application tiles in front. Clicking an element brings it to the front (see core/stacking.ts).
const INITIAL_STACK = ['funki', 'clock', 'sticky', 'recipients', 'files', 'settings', 'activity', 'passkeys', 'help']

const CHAOS: JokeKind[] = ['chaos-text', 'chaos-sheet', 'chaos-net', 'chaos-note', 'chaos-404']

// Icon grid: cell size, offsets (below the top strip, above the taskbar).
const CW = 96
const CH = 88
const OX = 8
const OY = 36

type Cell = { c: number; r: number }
const key = (c: Cell) => `${c.c}:${c.r}`

function gridSize() {
  return {
    cols: Math.max(2, Math.floor((window.innerWidth - OX) / CW)),
    rows: Math.max(3, Math.floor((window.innerHeight - OY - 44) / CH)),
  }
}

// Funki stands directly left of the column with the bin and the floppy, never on top of them.
// Returns the distance of Funki's right edge from the window's right edge.
export function funkiRight(windowWidth: number): number {
  const cols = Math.max(2, Math.floor((windowWidth - OX) / CW))
  const binLeft = OX + (cols - 1) * CW
  return windowWidth - binLeft + 8
}

// Default layout: most icons fill columns from the left; floppy and bin sit at the bottom right.
function defaultCells(): Record<string, Cell> {
  const { cols, rows } = gridSize()
  const out: Record<string, Cell> = {}
  const left = ICONS.filter((i) => i.id !== 'floppy' && i.id !== 'bin')
  left.forEach((ic, n) => {
    out[ic.id] = { c: Math.floor(n / rows), r: n % rows }
  })
  out.bin = { c: cols - 1, r: rows - 1 }
  out.floppy = { c: cols - 1, r: rows - 2 }
  return out
}

// In the desktop app the desktop also gets the passkey tile and a "Einrichtung" button; `local` carries what they need.
export interface LocalParts {
  backend: LocalBackend
  setup: SetupBackend
  onRedoSetup: () => void
}

export function DesktopApp({ local }: { local?: LocalParts } = {}) {
  const [pending, setPending] = useState<ConfirmRequest | null>(null)
  const [showRecipients, setShowRecipients] = useState(true)
  const [showFiles, setShowFiles] = useState(true)
  const [showSettings, setShowSettings] = useState(false)
  const [showActivity, setShowActivity] = useState(false)
  const [showHelp, setShowHelp] = useState(false)
  const [showPasskeys, setShowPasskeys] = useState(false)
  const [selectedIcon, setSelectedIcon] = useState<string | null>(null)
  const [stack, setStack] = useState<string[]>(INITIAL_STACK)
  const front = (id: string) => setStack((prev) => raised(prev, id))
  const [cells, setCells] = useState<Record<string, Cell>>(defaultCells)
  const [wallpaper, setWallpaper] = useState<Wallpaper>({ kind: 'clouds', tiled: true })
  const [menu, setMenu] = useState<{ x: number; y: number } | null>(null)
  const recipientsRef = useRef<HTMLDivElement>(null)
  const filesRef = useRef<HTMLDivElement>(null)
  const settingsRef = useRef<HTMLDivElement>(null)
  const activityRef = useRef<HTMLDivElement>(null)
  const helpRef = useRef<HTMLDivElement>(null)
  const passkeysRef = useRef<HTMLDivElement>(null)
    const timers = useRef<number[]>([])

  const [wins, setWins] = useState<Win[]>(defaultWins)

  useEffect(() => () => timers.current.forEach(clearTimeout), [])

  const open = (kind: WinKind, initial?: { x: number; y: number }) =>
    setWins((prev) => [...prev.filter((w) => w.kind !== kind), { id: nextWinId++, kind, initial }])
  const close = (id: number) => setWins((prev) => prev.filter((w) => w.id !== id))
  const raise = (id: number) =>
    setWins((prev) => {
      const w = prev.find((x) => x.id === id)
      return !w || prev[prev.length - 1].id === id ? prev : [...prev.filter((x) => x.id !== id), w]
    })

  const startChaos = () => {
    timers.current.forEach(clearTimeout)
    timers.current = []
    CHAOS.forEach((kind, i) => {
      timers.current.push(window.setTimeout(() => open(kind, { x: 260 + i * 38, y: 90 + i * 34 }), i * 450))
    })
    timers.current.push(window.setTimeout(() => open('chaos-ask', { x: 330, y: 330 }), CHAOS.length * 450 + 300))
  }
  const tidy = () => {
    timers.current.forEach(clearTimeout)
    timers.current = []
    setWins(defaultWins())
    setCells(defaultCells())
    setSelectedIcon(null)
  }

  const runIcon = (a: IconAction) => {
    if (a === 'recipients') { setShowRecipients(true); front('recipients') }
    else if (a === 'files') { setShowFiles(true); front('files') }
    else if (a === 'bin')
      setPending({ title: 'Papierkorb', body: 'Der Papierkorb ist leer. (Später landen hier zum Beispiel gelöschte Kontakt-Kandidaten.)', confirmLabel: 'Okay', info: true, run: () => {} })
    else open(a)
  }

  const closeTile = (id: string) => {
    if (id === 'recipients') setShowRecipients(false)
    else if (id === 'files') setShowFiles(false)
    else if (id === 'settings') setShowSettings(false)
    else if (id === 'activity') setShowActivity(false)
    else if (id === 'passkeys') setShowPasskeys(false)
    else if (id === 'help') setShowHelp(false)
  }

  const runStart = (c: StartCommand) => {
    if (c === 'settings') { setShowSettings(true); front('settings') }
    else if (c === 'activity') { setShowActivity(true); front('activity') }
    else if (c === 'passkeys') { setShowPasskeys(true); front('passkeys') }
    else if (c === 'manual') { setShowHelp(true); front('help') }
    else if (c === 'setup') local?.onRedoSetup()
    else if (c === 'recipients') { setShowRecipients(true); front('recipients') }
    else if (c === 'files') { setShowFiles(true); front('files') }
    else if (c === 'chaos') startChaos()
    else if (c === 'tidy') tidy()
    else open(c)
  }

  // Drop an icon: snap to the nearest cell; if it is taken, swap places.
  const dropIcon = (id: string, x: number, y: number) => {
    const { cols, rows } = gridSize()
    const target: Cell = {
      c: Math.min(cols - 1, Math.max(0, Math.round((x - OX) / CW))),
      r: Math.min(rows - 1, Math.max(0, Math.round((y - OY) / CH))),
    }
    setCells((prev) => {
      const old = prev[id]
      const next = { ...prev, [id]: target }
      const other = Object.keys(prev).find((k) => k !== id && key(prev[k]) === key(target))
      if (other) next[other] = old
      return next
    })
  }

  const w = typeof window === 'undefined' ? 1400 : window.innerWidth
  const startX = w >= 1150 ? 232 : 40
  // The right-hand column (clock, sticky note) stays free, so everything is visible at the start.
  const tileW = Math.min(520, Math.max(380, Math.floor((w - startX - 64 - 190) / 2)))

  const renderWin = (win: Win) => {
    const stacked = win.kind === 'clock' || win.kind === 'sticky'
    const common = {
      onClose: () => close(win.id),
      onFocus: () => {
        raise(win.id)
        if (stacked) front(win.kind)
      },
      initial: win.initial,
      zIndex: stacked ? levelOf(stack, win.kind) : undefined,
    }
    switch (win.kind) {
      case 'clock':
        return <Clock key={win.id} c={common} />
      case 'sticky':
        return <Sticky key={win.id} {...common} />
      case 'display':
        return <DisplayProps key={win.id} c={common} current={wallpaper} onApply={setWallpaper} />
      case 'run':
        return <RunDialog key={win.id} c={common} onCommand={runStart} />
      default:
        return <Joke key={win.id} kind={win.kind} {...common} onOpen={open} />
    }
  }

  const MENU_ITEMS: { label: string; run: () => void }[] = [
    { label: 'Anzeige-Eigenschaften …', run: () => open('display') },
    { label: 'Symbole anordnen', run: () => setCells(defaultCells()) },
    { label: 'Neuer Notizzettel', run: () => open('sticky', menu ? { x: menu.x, y: menu.y } : undefined) },
    { label: 'Desktop aufräumen', run: tidy },
  ]

  return (
    <Desktop
      wallpaper={wallpaper}
      onBackgroundClick={() => {
        setSelectedIcon(null)
        setMenu(null)
      }}
      onBackgroundContextMenu={(x, y) => setMenu({ x, y })}
    >
      <Taskbar
        items={[
          { id: 'recipients', label: 'Empfänger', open: showRecipients },
          { id: 'files', label: 'Dateien', open: showFiles },
          { id: 'settings', label: 'Einstellungen', open: showSettings },
          { id: 'activity', label: 'Aktivität', open: showActivity },
          ...(local ? [{ id: 'passkeys', label: 'Passkeys', open: showPasskeys }] : []),
          { id: 'help', label: 'Hilfe & Anleitung', open: showHelp },
        ]}
        onToggle={(id) => {
          front(id) // a click on a button of an open window brings it to the front
        }}
        onClose={closeTile}
        local={!!local}
        onCommand={runStart}
      />

      {ICONS.map((i) => {
        const cell = cells[i.id]
        return (
          <DesktopIcon
            key={i.id}
            label={i.label}
            selected={selectedIcon === i.id}
            x={OX + cell.c * CW}
            y={OY + cell.r * CH}
            zIndex={levelOf(stack, `icon:${i.id}`)}
            onSelect={() => {
              setSelectedIcon(i.id)
              front(`icon:${i.id}`)
            }}
            onOpen={() => runIcon(i.action)}
            onDrop={(x, y) => dropIcon(i.id, x, y)}
          >
            <PixelSprite rows={i.sprite.rows} palette={i.sprite.palette} scale={3} />
          </DesktopIcon>
        )
      })}

      {showRecipients && (
        <DraggableGlass
          containerRef={recipientsRef}
          zIndex={levelOf(stack, 'recipients')}
          onFocus={() => front('recipients')}
          title="Empfänger"
          className="rounded-3xl"
          initialPosition={{ x: startX, y: 44 }}
          initialSize={{ width: tileW, height: 600 }}
          minSize={{ width: 340, height: 260 }}
          collapsible
          onClose={() => setShowRecipients(false)}
        >
          <RecipientsTile ask={setPending} />
        </DraggableGlass>
      )}

      {showFiles && (
        <DraggableGlass
          containerRef={filesRef}
          zIndex={levelOf(stack, 'files')}
          onFocus={() => front('files')}
          title="Dateien und Anhänge"
          className="rounded-3xl"
          initialPosition={{ x: startX + tileW + 24, y: 44 }}
          initialSize={{ width: tileW, height: 600 }}
          minSize={{ width: 340, height: 260 }}
          collapsible
          onClose={() => setShowFiles(false)}
        >
          <FilesTile ask={setPending} />
        </DraggableGlass>
      )}

      {showSettings && (
        <DraggableGlass
          containerRef={settingsRef}
          zIndex={levelOf(stack, 'settings')}
          onFocus={() => front('settings')}
          title="Einstellungen"
          className="rounded-3xl"
          initialPosition={{ x: startX + 40, y: 72 }}
          initialSize={{ width: tileW, height: 640 }}
          minSize={{ width: 340, height: 260 }}
          collapsible
          onClose={() => setShowSettings(false)}
        >
          <SettingsTile ask={setPending} />
        </DraggableGlass>
      )}

      {showActivity && (
        <DraggableGlass
          containerRef={activityRef}
          zIndex={levelOf(stack, 'activity')}
          onFocus={() => front('activity')}
          title="Aktivität"
          className="rounded-3xl"
          initialPosition={{ x: startX + 80, y: 100 }}
          initialSize={{ width: tileW + 40, height: 620 }}
          minSize={{ width: 360, height: 260 }}
          collapsible
          onClose={() => setShowActivity(false)}
        >
          <ActivityTile />
        </DraggableGlass>
      )}

      {local && showPasskeys && (
        <DraggableGlass
          containerRef={passkeysRef}
          zIndex={levelOf(stack, 'passkeys')}
          onFocus={() => front('passkeys')}
          title="Passkeys"
          className="rounded-3xl"
          initialPosition={{ x: startX + 100, y: 110 }}
          initialSize={{ width: tileW, height: 380 }}
          minSize={{ width: 340, height: 220 }}
          collapsible
          onClose={() => setShowPasskeys(false)}
        >
          <PasskeysTile ask={setPending} local={local.backend} setup={local.setup} />
        </DraggableGlass>
      )}

      {showHelp && (
        <DraggableGlass
          containerRef={helpRef}
          zIndex={levelOf(stack, 'help')}
          onFocus={() => front('help')}
          title="Hilfe und Anleitung"
          className="rounded-3xl"
          initialPosition={{ x: startX + 120, y: 128 }}
          initialSize={{ width: tileW + 20, height: 600 }}
          minSize={{ width: 340, height: 260 }}
          collapsible
          onClose={() => setShowHelp(false)}
        >
          <HelpTile />
        </DraggableGlass>
      )}

      <Assistant right={funkiRight(w)} zIndex={levelOf(stack, 'funki')} onFocus={() => front('funki')} />

      {wins.map(renderWin)}

      {menu && (
        <>
          <div className="fixed inset-0" style={{ zIndex: 94 }} onClick={() => setMenu(null)} onContextMenu={(e) => { e.preventDefault(); setMenu(null) }} />
          <div
            className="fixed py-1"
            style={{
              left: menu.x,
              top: menu.y,
              width: 190,
              zIndex: 95,
              background: 'var(--face)',
              border: '2px solid',
              borderColor: 'var(--bevel-light) var(--bevel-dark) var(--bevel-dark) var(--bevel-light)',
              boxShadow: '1px 1px 0 #000',
              color: '#1d1d2b',
            }}
          >
            {MENU_ITEMS.map((m) => (
              <button
                key={m.label}
                className="block w-full text-left px-3 py-1 text-[13px] hover:bg-[#0a246a] hover:text-white"
                onClick={() => {
                  setMenu(null)
                  m.run()
                }}
              >
                {m.label}
              </button>
            ))}
          </div>
        </>
      )}

      {pending && (
        <ConfirmDialog
          title={pending.title}
          confirmLabel={pending.confirmLabel}
          info={pending.info}
          onCancel={() => setPending(null)}
          onConfirm={() => {
            pending.run()
            setPending(null)
          }}
        >
          {pending.body}
        </ConfirmDialog>
      )}
    </Desktop>
  )
}

// Start screen first; the desktop only appears after sign-in. If the server ends the session (idle time) or the
// person signs out, the sign-in comes back on top of the desktop, which stays as it was.
// The setup wizard comes first, before the sign-in. For now it runs on request (address ?wizard) with a stand-in
// backend; the real first-start check and the Rust side follow.
const SETUP_DONE_KEY = 'ma-setup-done'
// In the desktop app the administration runs locally by default. Whoever uses a server with a tunnel switches once
// ("Anmeldung am Server") and the choice is kept.
const MODE_KEY = 'ma-mode'
function storedMode(): 'local' | 'server' {
  try {
    return localStorage.getItem(MODE_KEY) === 'server' ? 'server' : 'local'
  } catch {
    return 'local'
  }
}
function storeMode(m: 'local' | 'server') {
  try {
    localStorage.setItem(MODE_KEY, m)
  } catch {
    /* without storage the default (local) applies at the next start */
  }
}
// Set by the stand-alone demo page, which has no server behind it.
const isDemo = () => typeof window !== 'undefined' && '__MA_WIZARD_DEMO__' in window
const inDesktopApp = () => typeof window !== 'undefined' && '__TAURI_INTERNALS__' in window
function wantsWizard(): boolean {
  if (isDemo()) return true
  // For testing: VITE_FORCE_WIZARD=1 shows the wizard at every start, in the app and in a browser.
  if (import.meta.env.VITE_FORCE_WIZARD) return true
  try {
    // In the desktop app the wizard runs on a first start; in a browser only on request.
    if (inDesktopApp()) return localStorage.getItem(SETUP_DONE_KEY) === null
    return new URLSearchParams(window.location.search).has('wizard')
  } catch {
    return false
  }
}

export default function App() {
  const [wizard, setWizard] = useState(wantsWizard)
  const backend: SetupBackend = useMemo(() => (inDesktopApp() ? tauriBackend() : mockBackend()), [])
  const [started, setStarted] = useState(false)
  const [booting, setBooting] = useState(true)
  const [resume, setResume] = useState(false)
  const [mode, setMode] = useState<'local' | 'server'>(storedMode)
  const isLocal = inDesktopApp() && mode === 'local'
  // Set while rendering on purpose: the tiles ask the admin application as soon as they appear.
  admin.useLocal(isLocal ? tauriTransport : null)
  const localBackend: LocalBackend = useMemo(() => (inDesktopApp() ? tauriLocalBackend() : mockLocalBackend()), [])

  useEffect(
    () =>
      admin.onSessionEnd(() => {
        setResume(true)
        setBooting(true)
      }),
    [],
  )

  const finishWizard = () => {
    try {
      localStorage.setItem(SETUP_DONE_KEY, '1')
    } catch {
      /* storage can be unavailable; the wizard is then shown again at the next start */
    }
    storeMode('local')
    setMode('local')
    setWizard(false)
  }

  if (wizard) {
    return (
      <Wizard backend={backend} onDone={finishWizard} onSkip={inDesktopApp() ? finishWizard : undefined} />
    )
  }

  if (isDemo()) {
    return (
      <div className="fixed inset-0 flex items-center justify-center" style={{ background: 'linear-gradient(#3a6ea5, #9cc7e8)' }}>
        <div className="p-4 text-[13px]" style={{ width: 380, background: 'var(--face)', border: '2px solid', borderColor: 'var(--bevel-light) var(--bevel-dark) var(--bevel-dark) var(--bevel-light)', color: '#1d1d2b' }}>
          <div className="font-bold mb-2">Hier endet die Vorschau</div>
          <div className="mb-3">In der echten App folgt jetzt die Anmeldung und danach der Desktop.</div>
          <button className="bevel font-bold" onClick={() => setWizard(true)}>Nochmal von vorn</button>
        </div>
      </div>
    )
  }

  return (
    <>
      {started && (
        <DesktopApp
          local={isLocal ? { backend: localBackend, setup: backend, onRedoSetup: () => setWizard(true) } : undefined}
        />
      )}
      {booting && (
        <Boot
          local={isLocal}
          key={resume ? 'resume' : 'first'}
          resume={resume}
          onDone={() => {
            setStarted(true)
            setBooting(false)
          }}
        />
      )}
    </>
  )
}
