import { useState } from 'react'
import {
  BIN, COFFEE, ENVELOPE, FOLDER, GLOBE, HELP, POWER, PixelSprite,
} from './PixelSprite'
import type { JokeKind } from './Jokes'

export type StartCommand = JokeKind | 'recipients' | 'files' | 'chaos' | 'tidy' | 'clock' | 'sticky' | 'display' | 'run'

const SAYINGS = [
  'Du hast Post. Vielleicht.',
  '42 ungelesene Gedanken.',
  'Bitte nicht den Stecker ziehen.',
  'Dieser Computer ist stolz auf dich.',
  'Strg+Alt+Entf löst nichts. Fühlt sich aber gut an.',
  'Zuletzt gesichert: gestern. Wahrscheinlich.',
]

type Sprite = { rows: string[]; palette: Record<string, string> }
interface Item { id: StartCommand; label: string; sprite?: Sprite }
interface Parent { key: string; label: string; children: Entry[] }
type Entry = Item | Parent | 'sep'

const ACCESSORIES: Entry[] = [
  { id: 'typewriter', label: 'Schreibmaschine' },
  { id: 'sheet', label: 'Zahlenwerk' },
  { id: 'paint', label: 'Pixelmaler' },
  { id: 'chat', label: 'Plauderecke' },
  { id: 'computer', label: 'Mein Computer' },
  { id: 'floppy', label: 'Diskettenbox' },
  { id: 'clock', label: 'Uhr' },
  { id: 'sticky', label: 'Notizzettel' },
]
const GAMES: Entry[] = [
  { id: 'solitaire', label: 'Kartensolitär' },
  { id: 'mines', label: 'Minenfeld' },
  { id: 'freecell', label: 'Freie Zellen' },
  { id: 'hearts', label: 'Herzblatt' },
  { id: 'chess', label: 'Schachclub' },
]

const MENU: Entry[] = [
  { id: 'recipients', label: 'Meine Empfänger', sprite: ENVELOPE },
  { id: 'files', label: 'Meine Dateien', sprite: FOLDER },
  { id: 'outbox', label: 'Postausgang', sprite: BIN },
  { id: 'internet', label: 'Internet-Erkunder', sprite: GLOBE },
  'sep',
  {
    key: 'programs',
    label: 'Programme',
    children: [
      { key: 'acc', label: 'Zubehör', children: ACCESSORIES },
      { key: 'games', label: 'Spiele', children: GAMES },
      'sep',
      { id: 'chaos', label: 'Fensterchaos' },
      { id: 'tidy', label: 'Desktop aufräumen' },
    ],
  },
  {
    key: 'docs',
    label: 'Dokumente',
    children: [
      { id: 'doc-shop', label: 'Einkaufszettel' },
      { id: 'doc-oma', label: 'Brief an Oma' },
      { id: 'doc-ideas', label: 'Ideen für später' },
    ],
  },
  {
    key: 'settings',
    label: 'Einstellungen',
    children: [
      { id: 'display', label: 'Anzeige-Eigenschaften' },
      { id: 'sticky', label: 'Notizzettel anheften' },
    ],
  },
  'sep',
  { id: 'coffee', label: 'Kaffeepause', sprite: COFFEE },
  { id: 'help', label: 'Hilfe? Na ja …', sprite: HELP },
  { id: 'run', label: 'Ausführen …' },
  'sep',
  { id: 'about', label: 'Über Mail Assistant' },
  { id: 'shutdown', label: 'Herunterfahren …', sprite: POWER },
]

const PANEL = {
  background: 'var(--face)',
  border: '2px solid',
  borderColor: 'var(--bevel-light) var(--bevel-dark) var(--bevel-dark) var(--bevel-light)',
  boxShadow: '1px 1px 0 #000',
  color: '#1d1d2b',
} as const
const SEP = (
  <div className="h-px mx-1 my-0.5" style={{ background: 'var(--bevel-dark)', boxShadow: '0 1px 0 #fff' }} />
)
const HOVER = 'hover:bg-[#0a246a] hover:text-white'

interface ListProps {
  entries: Entry[]
  depth: number
  open: string[]
  setOpen: (p: string[]) => void
  run: (id: StartCommand) => void
}

// One level of the menu. A plain row closes the levels to its right; a row with
// children opens its sub menu and keeps all levels to its left open, so moving the
// mouse into a sub menu never closes it.
function List({ entries, depth, open, setOpen, run }: ListProps) {
  return (
    <>
      {entries.map((e, i) => {
        if (e === 'sep') return <div key={`s${i}`}>{SEP}</div>
        if ('children' in e) {
          const isOpen = open[depth] === e.key
          return (
            <div key={e.key} className="relative" onMouseEnter={() => setOpen([...open.slice(0, depth), e.key])}>
              <button
                className={`flex items-center gap-3 w-full px-2 py-1 text-left text-[13px] ${isOpen ? 'bg-[#0a246a] text-white' : HOVER}`}
                onClick={() => setOpen(isOpen ? open.slice(0, depth) : [...open.slice(0, depth), e.key])}
              >
                <span className="w-8" />
                <span className="flex-1">{e.label}</span>
                <span>▸</span>
              </button>
              {isOpen && (
                <div className="absolute py-1" style={{ left: '100%', top: -4, width: 210, zIndex: 92, ...PANEL }}>
                  <List entries={e.children} depth={depth + 1} open={open} setOpen={setOpen} run={run} />
                </div>
              )}
            </div>
          )
        }
        return (
          <button
            key={`${e.id}${i}`}
            className={`flex items-center gap-3 w-full px-2 text-left text-[13px] ${HOVER} ${e.sprite ? 'py-1.5' : 'py-1'}`}
            onMouseEnter={() => setOpen(open.slice(0, depth))}
            onClick={() => run(e.id)}
          >
            <span className="w-8 flex justify-center">{e.sprite && <PixelSprite rows={e.sprite.rows} palette={e.sprite.palette} scale={2} />}</span>
            {e.label}
          </button>
        )
      })}
    </>
  )
}

// Start menu in the classic style: grey panel, vertical title strip, cascading sub menus.
export function StartMenu({ onCommand, onClose }: { onCommand: (c: StartCommand) => void; onClose: () => void }) {
  const [saying] = useState(() => SAYINGS[Math.floor(Math.random() * SAYINGS.length)])
  const [open, setOpen] = useState<string[]>([])
  const run = (id: StartCommand) => {
    onCommand(id)
    onClose()
  }
  return (
    <>
      <div className="fixed inset-0" style={{ zIndex: 90 }} onClick={onClose} />
      <div className="absolute flex" style={{ left: 2, bottom: 36, width: 260, zIndex: 91, ...PANEL }}>
        <div className="w-8 flex items-end justify-center pb-2" style={{ background: 'linear-gradient(0deg, #0a246a, #3a6ea5)' }}>
          <span className="text-white font-bold tracking-widest text-[15px] whitespace-nowrap" style={{ writingMode: 'vertical-rl', transform: 'rotate(180deg)' }}>
            MAIL ASSISTANT
          </span>
        </div>
        <div className="flex-1 py-1">
          <div className="px-2 py-1 text-[11px] italic opacity-80" onMouseEnter={() => setOpen([])}>Willkommen zurück, Internetreisender!</div>
          {SEP}
          <List entries={MENU} depth={0} open={open} setOpen={setOpen} run={run} />
          <div className="px-2 pt-1 pb-0.5 text-[10px] italic opacity-70 border-t border-black/20 mt-1" onMouseEnter={() => setOpen([])}>{saying}</div>
        </div>
      </div>
    </>
  )
}
