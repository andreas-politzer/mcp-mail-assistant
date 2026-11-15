import { useEffect, useState } from 'react'
import { Win95Window } from './Win95Window'

interface Common {
  onClose: () => void
  onFocus?: () => void
  initial?: { x: number; y: number }
}

// Small analog clock with a ticking second hand. Own drawing.
export function Clock({ c }: { c: Common }) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(t)
  }, [])
  const s = now.getSeconds()
  const m = now.getMinutes() + s / 60
  const h = (now.getHours() % 12) + m / 60
  const hand = (deg: number, len: number, w: number, color: string) => (
    <line x1={0} y1={0} x2={0} y2={-len} stroke={color} strokeWidth={w} strokeLinecap="round" transform={`rotate(${deg})`} />
  )
  return (
    <Win95Window title="Uhr" width={136} zIndex="auto" onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <svg viewBox="-60 -60 120 120" width={104} height={104} className="block mx-auto" aria-label="Uhr">
        <circle r={56} fill="#fff" stroke="#3a3a4a" strokeWidth={3} />
        {Array.from({ length: 12 }, (_, i) => (
          <line key={i} x1={0} y1={-48} x2={0} y2={i % 3 === 0 ? -40 : -44} stroke="#3a3a4a" strokeWidth={i % 3 === 0 ? 3 : 1.5} transform={`rotate(${i * 30})`} />
        ))}
        {hand(h * 30, 28, 4, '#1d1d2b')}
        {hand(m * 6, 40, 3, '#1d1d2b')}
        {hand(s * 6, 44, 1.5, '#d6334c')}
        <circle r={3} fill="#1d1d2b" />
      </svg>
      <div className="text-center text-[11px] mt-0.5">{now.toLocaleTimeString('de-DE')}</div>
    </Win95Window>
  )
}
