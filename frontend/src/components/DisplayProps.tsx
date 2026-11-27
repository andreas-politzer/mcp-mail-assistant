import { t } from '../i18n'
import { useState } from 'react'
import { Win95Window } from './Win95Window'
import { WALLPAPERS, WallpaperLayer, type Wallpaper } from './Desktop'

interface Common {
  onClose: () => void
  onFocus?: () => void
  initial?: { x: number; y: number }
}

// Display properties in the old style: list, small preview, tile or stretch.
export function DisplayProps({ c, current, onApply }: { c: Common; current: Wallpaper; onApply: (w: Wallpaper) => void }) {
  const [wp, setWp] = useState<Wallpaper>(current)
  return (
    <Win95Window title="Anzeige-Eigenschaften" width={400} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div className="font-bold mb-1">Hintergrund</div>
      <div className="flex gap-3">
        <div
          className="relative overflow-hidden shrink-0"
          style={{ width: 150, height: 112, backgroundColor: '#008080', border: '2px solid', borderColor: 'var(--bevel-dark) var(--bevel-light) var(--bevel-light) var(--bevel-dark)' }}
        >
          <WallpaperLayer wp={wp} idp="preview" scale={0.34} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="bg-white p-1 text-[12px]" style={{ border: '2px solid', borderColor: 'var(--bevel-dark) var(--bevel-light) var(--bevel-light) var(--bevel-dark)' }}>
            {WALLPAPERS.map((w) => (
              <button
                key={w.kind}
                className="block w-full text-left px-1 py-0.5"
                style={wp.kind === w.kind ? { background: '#0a246a', color: '#fff' } : undefined}
                onClick={() => setWp({ ...wp, kind: w.kind })}
              >
                {t(w.label)}
              </button>
            ))}
          </div>
          <div className="mt-2 text-[12px] flex gap-3" style={{ opacity: wp.kind === 'clouds' ? 1 : 0.45 }}>
            <label className="flex items-center gap-1">
              <input type="radio" disabled={wp.kind !== 'clouds'} checked={wp.tiled} onChange={() => setWp({ ...wp, tiled: true })} /> {t('Nebeneinander')}
            </label>
            <label className="flex items-center gap-1">
              <input type="radio" disabled={wp.kind !== 'clouds'} checked={!wp.tiled} onChange={() => setWp({ ...wp, tiled: false })} /> {t('Gestreckt')}
            </label>
          </div>
        </div>
      </div>
      <div className="flex justify-end gap-2 mt-3">
        <button className="bevel font-bold" onClick={() => { onApply(wp); c.onClose() }}>OK</button>
        <button className="bevel" onClick={c.onClose}>{t('Abbrechen')}</button>
        <button className="bevel" onClick={() => onApply(wp)}>{t('Übernehmen')}</button>
      </div>
    </Win95Window>
  )
}
