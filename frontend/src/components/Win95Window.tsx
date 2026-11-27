import { t } from '../i18n'
import type { ReactNode } from 'react'
import { useDraggable } from '../core/useDraggable'

const FRAME = { background: 'var(--face)', border: '2px solid', borderColor: 'var(--bevel-light) var(--bevel-dark) var(--bevel-dark) var(--bevel-light)', boxShadow: '1px 1px 0 #000, 6px 8px 18px rgba(0,0,0,0.35)' } as const

// A small classic-looking window: grey frame with raised edges, navy title bar,
// close button. Own drawing, draggable by its title bar.
export function Win95Window({
  title,
  width = 360,
  zIndex = 90,
  initial,
  onFocus,
  onClose,
  children,
}: {
  title: string
  width?: number
  zIndex?: number | 'auto'
  initial?: { x: number; y: number }
  onFocus?: () => void
  onClose: () => void
  children: ReactNode
}) {
  const start = initial ?? {
    x: Math.max(8, Math.round((typeof window === 'undefined' ? 1200 : window.innerWidth) / 2 - width / 2)),
    y: 150 + Math.round(Math.random() * 40),
  }
  const { position, dragHandlers } = useDraggable(start, undefined, { width, height: 120 })
  return (
    <div className="fixed" onPointerDownCapture={onFocus} style={{ left: position.x, top: position.y, width, zIndex, color: '#1d1d2b', ...FRAME }}>
      <div
        className="flex items-center gap-2 px-1.5 py-0.5 m-0.5 select-none cursor-default touch-none"
        style={{ background: 'linear-gradient(90deg, #0a246a, #3a6ea5)', color: '#fff' }}
        onPointerDown={dragHandlers.onPointerDown}
        onPointerMove={dragHandlers.onPointerMove}
        onPointerUp={(e) => dragHandlers.onPointerUp(e)}
      >
        <span className="font-bold text-[13px] flex-1 truncate">{t(title)}</span>
        <button className="bevel !px-1.5 !py-0 text-[11px] leading-4 font-bold" onPointerDown={(e) => e.stopPropagation()} onClick={onClose} aria-label={t('Schließen')}>✕</button>
      </div>
      <div className="p-3 text-[13px] leading-relaxed">{children}</div>
    </div>
  )
}

// Same window with a dimmed backdrop; clicking the backdrop closes it.
export function Win95Modal({ title, width, onClose, children }: { title: string; width?: number; onClose: () => void; children: ReactNode }) {
  return (
    <>
      <div className="fixed inset-0 bg-black/30" style={{ zIndex: 100 }} onClick={onClose} />
      <Win95Window title={title} width={width} zIndex={101} onClose={onClose}>
        {children}
      </Win95Window>
    </>
  )
}
