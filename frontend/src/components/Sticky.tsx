import { useState } from 'react'
import { useDraggable } from '../core/useDraggable'

const TIPS = [
  'Claude entwirft. Du entscheidest, was rausgeht.',
  'Eine Sperre gewinnt immer.',
  'Erst prüfen, dann freigeben.',
  'Kaffee nicht vergessen.',
  'Heute schon gesichert?',
  'Importierte Kontakte sind erst mal nur Kandidaten.',
]
const KEY = 'mail-assistant-sticky'

function load(): string {
  try {
    return localStorage.getItem(KEY) ?? TIPS[0]
  } catch {
    return TIPS[0]
  }
}

// Flat yellow sticky note in the plain style of the time: hard edge, system font,
// small title bar. Draggable, editable, remembers what you wrote.
export function Sticky({ onClose, onFocus, initial }: { onClose: () => void; onFocus?: () => void; initial?: { x: number; y: number } }) {
  const [text, setText] = useState(load)
  const [tip, setTip] = useState(0)
  const { position, dragHandlers } = useDraggable(initial ?? { x: 300, y: 300 }, undefined, { width: 200, height: 190 })
  const save = (v: string) => {
    setText(v)
    try {
      localStorage.setItem(KEY, v)
    } catch {
      /* storage may be unavailable; the note simply won't be remembered */
    }
  }
  const nextTip = () => {
    const n = (tip + 1) % TIPS.length
    setTip(n)
    save(TIPS[n])
  }
  const secret = /passwort|password|kennwort/i.test(text)
  return (
    <div
      className="fixed"
      onPointerDownCapture={onFocus}
      style={{ left: position.x, top: position.y, width: 200, zIndex: 90, background: '#ffff99', border: '1px solid #000', color: '#000', fontFamily: 'var(--retro-font)' }}
    >
      <div
        className="flex items-center px-1 text-[11px] select-none cursor-grab touch-none"
        style={{ background: '#e6e65c', borderBottom: '1px solid #000', height: 16 }}
        onPointerDown={dragHandlers.onPointerDown}
        onPointerMove={dragHandlers.onPointerMove}
        onPointerUp={(e) => dragHandlers.onPointerUp(e)}
      >
        <span className="flex-1 font-bold">Notizzettel</span>
        <button className="px-1 font-bold leading-none" onPointerDown={(e) => e.stopPropagation()} onClick={onClose} aria-label="Schließen">x</button>
      </div>
      <textarea
        value={text}
        onChange={(e) => save(e.target.value)}
        rows={6}
        className="w-full bg-transparent outline-none resize-none px-2 py-1 text-[13px] leading-snug"
        style={{ fontFamily: '"Courier New", Courier, monospace' }}
      />
      {secret && <div className="px-2 pb-1 text-[11px]" style={{ color: '#a01d2c' }}>Psst: Passwörter gehören nicht auf Zettel. Auch nicht auf digitale.</div>}
      <div className="px-2 pb-1 flex justify-end">
        <button className="text-[11px] underline" onClick={nextTip}>Neuer Tipp</button>
      </div>
    </div>
  )
}
