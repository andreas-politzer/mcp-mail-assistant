import type { ReactNode } from 'react'

export type WallpaperKind = 'teal' | 'water' | 'clouds' | 'dusk'
export interface Wallpaper {
  kind: WallpaperKind
  tiled: boolean
}

export const WALLPAPERS: { kind: WallpaperKind; label: string }[] = [
  { kind: 'teal', label: 'Türkis (ohne Bild)' },
  { kind: 'water', label: 'Wasser' },
  { kind: 'clouds', label: 'Wolken' },
  { kind: 'dusk', label: 'Abendrot' },
]

// Own generated sky tile (no image file). Without stitching, the tile edges do
// not quite match, so tiling shows seams, like the old tiled wallpapers.
function cloudsUrl(): string {
  const svg =
    "<svg xmlns='http://www.w3.org/2000/svg' width='440' height='330'>" +
    "<defs><linearGradient id='g' x1='0' y1='0' x2='0' y2='1'><stop offset='0' stop-color='#4a86cc'/><stop offset='1' stop-color='#9cc9ee'/></linearGradient>" +
    "<filter id='c' x='0' y='0' width='100%' height='100%'><feTurbulence type='fractalNoise' baseFrequency='0.007 0.011' numOctaves='5' seed='3'/>" +
    "<feColorMatrix type='matrix' values='0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 2.6 -1.0'/></filter></defs>" +
    "<rect width='100%' height='100%' fill='url(#g)'/><rect width='100%' height='100%' filter='url(#c)'/></svg>"
  return `url("data:image/svg+xml,${encodeURIComponent(svg)}")`
}
const CLOUDS = cloudsUrl()

// The wallpaper itself; also used for the small preview in the display settings.
export function WallpaperLayer({ wp, idp = 'wp', scale = 1 }: { wp: Wallpaper; idp?: string; scale?: number }) {
  if (wp.kind === 'water') {
    return (
      <svg className="absolute inset-0 w-full h-full pointer-events-none" preserveAspectRatio="none" aria-hidden>
        <defs>
          <linearGradient id={`${idp}-base`} x1="0" y1="0" x2="1" y2="1">
            <stop offset="0" stopColor="#0f7fc6" />
            <stop offset="1" stopColor="#35b2e6" />
          </linearGradient>
          <filter id={`${idp}-dark`} x="0" y="0" width="100%" height="100%">
            <feTurbulence type="fractalNoise" baseFrequency="0.02 0.035" numOctaves={2} seed={4} />
            <feColorMatrix type="matrix" values="0 0 0 0 0.02  0 0 0 0 0.25  0 0 0 0 0.5  0 0 0 2.6 -1.2" />
          </filter>
          <filter id={`${idp}-light`} x="0" y="0" width="100%" height="100%">
            <feTurbulence type="fractalNoise" baseFrequency="0.011 0.026" numOctaves={3} seed={11} />
            <feColorMatrix type="matrix" values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 3.2 -1.35" />
          </filter>
        </defs>
        <rect width="100%" height="100%" fill={`url(#${idp}-base)`} />
        <rect width="100%" height="100%" filter={`url(#${idp}-dark)`} />
        <rect width="100%" height="100%" filter={`url(#${idp}-light)`} />
      </svg>
    )
  }
  if (wp.kind === 'clouds') {
    return (
      <div
        className="absolute inset-0 pointer-events-none"
        style={{ backgroundImage: `${CLOUDS}, linear-gradient(#4a86cc, #9cc9ee)`, backgroundRepeat: wp.tiled ? 'repeat, no-repeat' : 'no-repeat, no-repeat', backgroundSize: wp.tiled ? `${440 * scale}px ${330 * scale}px, 100% 100%` : '100% 100%, 100% 100%' }}
      />
    )
  }
  if (wp.kind === 'dusk') {
    return <div className="absolute inset-0 pointer-events-none" style={{ background: 'linear-gradient(#2b1b4d 0%, #7a2a63 40%, #d9525a 70%, #f7a64a 100%)' }} />
  }
  return null
}

// Grundfarbe unter dem Hintergrund: Glas-Kacheln spiegeln sie, falls das Bild einen Moment später fertig ist.
const BASE_COLOR: Record<WallpaperKind, string> = { teal: '#008080', water: '#1f8fd0', clouds: '#6fa6dc', dusk: '#7a2a63' }

export function Desktop({
  children,
  wallpaper,
  onBackgroundClick,
  onBackgroundContextMenu,
}: {
  children: ReactNode
  wallpaper: Wallpaper
  onBackgroundClick?: () => void
  onBackgroundContextMenu?: (x: number, y: number) => void
}) {
  return (
    <div
      className="fixed inset-0 overflow-hidden"
      style={{ backgroundColor: BASE_COLOR[wallpaper.kind] }}
      onClick={onBackgroundClick}
      onContextMenu={(e) => {
        if (e.target !== e.currentTarget) return
        e.preventDefault()
        onBackgroundContextMenu?.(e.clientX, e.clientY)
      }}
    >
      <WallpaperLayer wp={wallpaper} />
      {children}
    </div>
  )
}
