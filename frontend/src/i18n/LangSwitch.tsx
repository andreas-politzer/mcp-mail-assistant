import { getLang, setLang, useLang, type Lang } from './index'

// Two small buttons, DE and EN. The choice is remembered. Not translated on purpose: it must be findable in either language.
export function LangSwitch({ className }: { className?: string }) {
  useLang()
  const current = getLang()
  return (
    <span className={className} role="group" aria-label="Sprache / Language">
      {(['de', 'en'] as Lang[]).map((l) => (
        <button
          key={l}
          type="button"
          className="bevel text-[11px]"
          aria-pressed={current === l}
          title={l === 'de' ? 'Deutsch' : 'English'}
          style={{ fontWeight: current === l ? 700 : 400, marginRight: l === 'de' ? 2 : 0 }}
          onClick={() => setLang(l)}
        >
          {l.toUpperCase()}
        </button>
      ))}
    </span>
  )
}
