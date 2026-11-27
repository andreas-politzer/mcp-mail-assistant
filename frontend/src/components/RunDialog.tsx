import { t } from '../i18n'
import { useState } from 'react'
import { Win95Window } from './Win95Window'
import type { StartCommand } from './StartMenu'

interface Common {
  onClose: () => void
  onFocus?: () => void
  initial?: { x: number; y: number }
}

const WORDS: [RegExp, StartCommand][] = [
  [/kaffee|coffee/, 'coffee'],
  [/funki|plauder|chat/, 'chat'],
  [/uhr|zeit/, 'clock'],
  [/zettel|notiz/, 'sticky'],
  [/mail|empf|brief/, 'recipients'],
  [/datei|ordner/, 'files'],
  [/mine/, 'mines'],
  [/paint|maler|mal/, 'paint'],
  [/chaos/, 'chaos'],
  [/schreib|text/, 'typewriter'],
  [/zahl|tabelle/, 'sheet'],
  [/computer|pc/, 'computer'],
  [/disk/, 'floppy'],
  [/internet|netz|web/, 'internet'],
  [/hilfe|help/, 'help'],
  [/schach/, 'chess'],
  [/solit|karte/, 'solitaire'],
]

// "Ausführen …": type a word, something opens (or a polite error appears).
export function RunDialog({ c, onCommand }: { c: Common; onCommand: (k: StartCommand) => void }) {
  const [text, setText] = useState('')
  const [err, setErr] = useState<string | null>(null)
  const go = () => {
    const word = text.trim().toLowerCase()
    if (!word) return
    if (/^(cmd|command|terminal|sudo|rm)/.test(word)) return setErr(t('Das hier ist kein Terminal. Zum Glück.'))
    const hit = WORDS.find(([re]) => re.test(word))
    if (!hit) return setErr(t('Der Name „{name}“ wurde nicht gefunden. Prüfen Sie die Schreibweise und versuchen Sie es erneut.', { name: text.trim() }))
    c.onClose()
    onCommand(hit[1])
  }
  return (
    <Win95Window title={t('Ausführen')} width={380} onClose={c.onClose} onFocus={c.onFocus} initial={c.initial}>
      <div className="mb-2">{t('Geben Sie den Namen eines Programms ein, zum Beispiel „kaffee“ oder „funki“.')}</div>
      <div className="flex items-center gap-2">
        <span>{t('Öffnen:')}</span>
        <input
          autoFocus
          value={text}
          onChange={(e) => { setText(e.target.value); setErr(null) }}
          onKeyDown={(e) => e.key === 'Enter' && go()}
          className="flex-1 px-1 py-0.5 outline-none bg-white"
          style={{ border: '2px solid', borderColor: 'var(--bevel-dark) var(--bevel-light) var(--bevel-light) var(--bevel-dark)' }}
        />
      </div>
      {err && <div className="mt-2 text-[12px]" style={{ color: '#a01d2c' }}>{err}</div>}
      <div className="flex justify-end gap-2 mt-3">
        <button className="bevel font-bold" onClick={go}>OK</button>
        <button className="bevel" onClick={c.onClose}>{t('Abbrechen')}</button>
      </div>
    </Win95Window>
  )
}
