import { useEffect, useRef, useState } from 'react'

const FRAME = {
  background: 'var(--face)',
  border: '2px solid',
  borderColor: 'var(--bevel-light) var(--bevel-dark) var(--bevel-dark) var(--bevel-light)',
  boxShadow: '1px 1px 0 #000, 6px 8px 18px rgba(0,0,0,0.5)',
  color: '#1d1d2b',
  animation: 'boot-rise 450ms ease-out both',
} as const

// Sign-in window in the classic style. The passphrase only lives in this input until it is sent.
export function LoginDialog({ error, busy, onSubmit }: { error: string | null; busy: boolean; onSubmit: (passphrase: string) => void }) {
  const [value, setValue] = useState('')
  const input = useRef<HTMLInputElement>(null)

  useEffect(() => {
    if (!busy) input.current?.focus()
  }, [busy, error])

  const submit = () => {
    if (!value || busy) return
    const p = value
    setValue('') // gone from the form right away, win or lose
    onSubmit(p)
  }

  return (
    <div className="fixed inset-0 flex items-center justify-center" style={{ zIndex: 20 }}>
      <div style={{ width: 380, ...FRAME }} role="dialog" aria-label="Anmelden">
        <div className="px-2 py-0.5 m-0.5 select-none font-bold text-[13px]" style={{ background: 'linear-gradient(90deg, #0a246a, #3a6ea5)', color: '#fff' }}>
          Anmelden – Mail Assistant
        </div>
        <form
          className="p-4 text-[13px]"
          onSubmit={(e) => {
            e.preventDefault()
            submit()
          }}
        >
          <div className="flex gap-3 mb-3">
            <div className="text-3xl leading-none select-none">🔑</div>
            <div>
              Bitte die Verwaltungs-Passphrase eingeben. Sie wird nicht gespeichert; die Sitzung gilt nur, solange dieses Fenster offen bleibt.
            </div>
          </div>
          <label className="block mb-1" htmlFor="pp">Passphrase:</label>
          <input
            id="pp"
            ref={input}
            type="password"
            value={value}
            disabled={busy}
            autoComplete="off"
            autoCorrect="off"
            autoCapitalize="off"
            spellCheck={false}
            onChange={(e) => setValue(e.target.value)}
            className="w-full px-1.5 py-1 bg-white"
            style={{ border: '2px solid', borderColor: 'var(--bevel-dark) var(--bevel-light) var(--bevel-light) var(--bevel-dark)' }}
          />
          <div className="min-h-[20px] mt-2 text-[12px]" style={{ color: '#a01020' }} role="alert">
            {error}
          </div>
          <div className="flex justify-end mt-2">
            <button type="submit" className="bevel font-bold" disabled={busy || !value}>
              {busy ? 'Einen Moment …' : 'Anmelden'}
            </button>
          </div>
        </form>
      </div>
    </div>
  )
}
