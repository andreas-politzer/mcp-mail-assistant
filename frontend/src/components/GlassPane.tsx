import type { ReactNode } from 'react'

// Glass surface. Important lesson from the Playlist Generator: never give a
// tile an explicit z-index. That creates a new stacking context and breaks
// backdrop-filter. Tiles stack by their order in the DOM.
export function GlassPane({ children, className }: { children: ReactNode; className: string }) {
  return (
    <div className={`relative border border-white/70 ${className}`}>
      <div
        className="absolute inset-0 rounded-[inherit] pointer-events-none"
        style={{
          background: 'rgba(14,30,78,0.42)',
          backdropFilter: 'blur(14px) saturate(160%)',
          WebkitBackdropFilter: 'blur(14px) saturate(160%)',
        }}
      />
      <div
        className="absolute inset-0 rounded-[inherit] pointer-events-none"
        style={{
          boxShadow:
            'inset 1.5px 1.5px 1px rgba(255,255,255,1), inset -1px -1px 1px rgba(29,29,31,0.06), 0 24px 60px rgba(29,29,31,0.25)',
        }}
      />
      <div className="relative h-full flex flex-col overflow-hidden">{children}</div>
    </div>
  )
}
