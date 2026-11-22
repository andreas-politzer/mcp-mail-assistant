import { useEffect, useState } from 'react'
import { StartMenu, type StartCommand } from './StartMenu'

export interface TaskbarItem {
  id: string
  label: string
  open: boolean
}

// Only open windows have a button, as in Windows 95. A click brings the window to the front; a right click offers to close it.
export function Taskbar({
  items,
  onToggle,
  onCommand,
  onClose,
  local = false,
}: {
  items: TaskbarItem[]
  onToggle: (id: string) => void
  onCommand: (c: StartCommand) => void
  onClose?: (id: string) => void
  local?: boolean
}) {
  const [closing, setClosing] = useState<{ id: string; x: number; y: number } | null>(null)
  const [now, setNow] = useState(() => new Date())
  const [menuOpen, setMenuOpen] = useState(false)
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30000)
    return () => clearInterval(t)
  }, [])

  return (
    <>
      <div className="absolute top-0 left-0 right-0 h-7 flex items-center px-3 text-xs select-none" style={{ background: 'var(--face)', borderBottom: '2px solid var(--bevel-dark)', zIndex: 70 }}>
        <span className="text-[13px]">Mail Assistant</span>
      </div>
      <div className="absolute bottom-0 left-0 right-0 h-9 flex items-center gap-2 px-2 select-none" style={{ background: 'var(--face)', borderTop: '2px solid var(--bevel-light)', zIndex: 70 }}>
        <button className="bevel font-bold" aria-pressed={menuOpen} onClick={() => setMenuOpen((v) => !v)}>✉ Start</button>
        {items.filter((it) => it.open).map((it) => (
          <button
            key={it.id}
            className="bevel"
            aria-pressed={it.open}
            onClick={() => onToggle(it.id)}
            onContextMenu={(e) => {
              e.preventDefault()
              if (onClose) setClosing({ id: it.id, x: e.clientX, y: e.clientY })
            }}
          >
            {it.label}
          </button>
        ))}
        <span className="ml-auto text-xs pr-2">
          {now.toLocaleTimeString('de-DE', { hour: '2-digit', minute: '2-digit' })}
        </span>
      </div>
      {menuOpen && <StartMenu local={local} onCommand={onCommand} onClose={() => setMenuOpen(false)} />}
      {closing && (
        <>
          <div className="fixed inset-0" style={{ zIndex: 94 }} onClick={() => setClosing(null)} onContextMenu={(e) => { e.preventDefault(); setClosing(null) }} />
          <div
            className="fixed py-1"
            style={{ left: closing.x, bottom: 40, width: 120, zIndex: 95, background: 'var(--face)', border: '2px solid', borderColor: 'var(--bevel-light) var(--bevel-dark) var(--bevel-dark) var(--bevel-light)', boxShadow: '1px 1px 0 #000', color: '#1d1d2b' }}
          >
            <button className="block w-full text-left px-3 py-1 text-[13px] hover:bg-[#0a246a] hover:text-white" onClick={() => { onClose?.(closing.id); setClosing(null) }}>
              Schließen
            </button>
          </div>
        </>
      )}
    </>
  )
}
