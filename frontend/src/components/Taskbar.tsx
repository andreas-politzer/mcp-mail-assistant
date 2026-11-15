import { useEffect, useState } from 'react'
import { StartMenu, type StartCommand } from './StartMenu'

export interface TaskbarItem {
  id: string
  label: string
  open: boolean
}

export function Taskbar({ items, onToggle, onCommand }: { items: TaskbarItem[]; onToggle: (id: string) => void; onCommand: (c: StartCommand) => void }) {
  const [now, setNow] = useState(() => new Date())
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000)
    return () => clearInterval(t)
  }, [])

  return (
    <>
      <div className="absolute top-0 left-0 right-0 h-7 flex items-center px-3 text-xs select-none" style={{ background: 'var(--face)', borderBottom: '2px solid var(--bevel-dark)' }}>
        <span className="text-[13px]">Mail Assistant</span>
        <span className="ml-3 px-2 py-0.5 rounded bg-amber-200 text-amber-900">Prototyp · simulierte Daten</span>
      </div>
      <div className="absolute bottom-0 left-0 right-0 h-9 flex items-center gap-2 px-2 select-none" style={{ background: 'var(--face)', borderTop: '2px solid var(--bevel-light)' }}>
        <button className="bevel font-bold" aria-pressed={menuOpen} onClick={() => setMenuOpen((v) => !v)}>✉ Start</button>
        {items.map((it) => (
          <button key={it.id} className="bevel" aria-pressed={it.open} onClick={() => onToggle(it.id)}>
            {it.label}
          </button>
        ))}
        <span className="ml-auto text-xs pr-2">
          {now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
      {menuOpen && <StartMenu onCommand={onCommand} onClose={() => setMenuOpen(false)} />}
    </>
  )
}
