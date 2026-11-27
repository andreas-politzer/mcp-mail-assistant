import { t } from '../i18n'
import { useRef, useState, type ReactNode } from 'react'

const DRAG_THRESHOLD = 4

// Desktop icon in the old style: click selects, double-click opens, drag moves
// it (it snaps to the grid when you let go).
export function DesktopIcon({
  label,
  selected,
  x,
  y,
  zIndex,
  onSelect,
  onOpen,
  onDrop,
  children,
}: {
  label: string
  selected: boolean
  x: number
  y: number
  zIndex?: number
  onSelect: () => void
  onOpen: () => void
  onDrop: (x: number, y: number) => void
  children: ReactNode
}) {
  const start = useRef<{ px: number; py: number } | null>(null)
  const [delta, setDelta] = useState<{ dx: number; dy: number } | null>(null)
  return (
    <button
      type="button"
      title={t('Doppelklick zum Öffnen, ziehen zum Verschieben')}
      className="absolute flex flex-col items-center gap-1 w-24 py-1 select-none outline-none touch-none"
      style={{ left: x, top: y, zIndex, transform: delta ? `translate(${delta.dx}px, ${delta.dy}px)` : undefined, cursor: delta ? 'grabbing' : 'default' }}
      onPointerDown={(e) => {
        e.stopPropagation()
        onSelect()
        start.current = { px: e.clientX, py: e.clientY }
        e.currentTarget.setPointerCapture(e.pointerId)
      }}
      onPointerMove={(e) => {
        if (!start.current) return
        const dx = e.clientX - start.current.px
        const dy = e.clientY - start.current.py
        if (delta || Math.abs(dx) > DRAG_THRESHOLD || Math.abs(dy) > DRAG_THRESHOLD) setDelta({ dx, dy })
      }}
      onPointerUp={() => {
        if (delta) onDrop(x + delta.dx, y + delta.dy)
        start.current = null
        setDelta(null)
      }}
      onClick={(e) => e.stopPropagation()}
      onDoubleClick={onOpen}
      onKeyDown={(e) => e.key === 'Enter' && onOpen()}
    >
      <span style={{ filter: selected ? 'brightness(0.7) sepia(1) hue-rotate(190deg) saturate(3)' : undefined }}>{children}</span>
      <span
        className="px-1 text-[12px] leading-tight text-center"
        style={{
          color: '#fff',
          textShadow: selected ? 'none' : '1px 1px 0 #000',
          background: selected ? '#0a246a' : 'transparent',
          outline: selected ? '1px dotted #fff' : 'none',
        }}
      >
        {label}
      </span>
    </button>
  )
}
