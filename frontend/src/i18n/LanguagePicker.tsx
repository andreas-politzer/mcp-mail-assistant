import { useEffect, useRef } from 'react'
import { setLang, systemLang, type Lang } from './index'

// The very first thing at the very first start, before the fake boot: which language? Both languages at once,
// since nobody can be expected to read either. The system language is only the preselection.
export function LanguagePicker() {
  const suggested = systemLang()
  const first = useRef<HTMLButtonElement>(null)
  useEffect(() => first.current?.focus(), [])
  const choices: { lang: Lang; label: string }[] = [
    { lang: 'de', label: 'Deutsch' },
    { lang: 'en', label: 'English' },
  ]
  return (
    <div className="fixed inset-0 flex items-center justify-center" style={{ background: 'linear-gradient(#3a6ea5, #9cc7e8)' }} data-testid="language-picker">
      <div role="dialog" aria-label="Sprache / Language" className="text-[13px]" style={{ width: 340, background: 'var(--face)', border: '2px solid', borderColor: 'var(--bevel-light) var(--bevel-dark) var(--bevel-dark) var(--bevel-light)', boxShadow: '2px 2px 0 #000', color: '#1d1d2b' }}>
        <div className="px-2 py-1 font-bold text-white" style={{ background: 'linear-gradient(90deg, #0a246a, #3a6ea5)' }}>Sprache / Language</div>
        <div className="p-4">
          <p className="mb-1">Welche Sprache möchtest du?</p>
          <p className="mb-4 opacity-80">Which language would you like?</p>
          <div className="flex gap-3 justify-center">
            {choices.map((c) => (
              <button
                key={c.lang}
                ref={c.lang === suggested ? first : undefined}
                type="button"
                className={'bevel px-4 py-1' + (c.lang === suggested ? ' font-bold' : '')}
                lang={c.lang}
                onClick={() => setLang(c.lang)}
              >
                {c.label}
              </button>
            ))}
          </div>
          <p className="mt-4 text-[11px] opacity-70">Du kannst das später ändern / You can change this later: DE | EN</p>
        </div>
      </div>
    </div>
  )
}
