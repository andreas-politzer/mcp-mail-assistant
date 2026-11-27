import { useSyncExternalStore } from 'react'
import { EN } from './en'

// One app, two languages. The German text in the source IS the key: t('Hallo {name}', { name }) gives
// the German text as written, or its English entry from ./en when the language is English. A text
// without an English entry falls back to German, so a missing translation never breaks the screen.
export type Lang = 'de' | 'en'

const STORAGE_KEY = 'mail-assistant-lang'

function detect(): Lang {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY)
    if (saved === 'de' || saved === 'en') return saved
  } catch {
    /* storage unavailable: fall through to the system language */
  }
  return systemLang()
}

let current: Lang = detect()
// Whether the person has chosen a language yet (stored), as opposed to the one guessed from the system.
function hasStoredChoice(): boolean {
  try {
    const saved = window.localStorage.getItem(STORAGE_KEY)
    return saved === 'de' || saved === 'en'
  } catch {
    return false
  }
}
let chosen = hasStoredChoice()
const listeners = new Set<() => void>()

// The language the system suggests: German for German, English for everything else.
export function systemLang(): Lang {
  const sys = typeof navigator !== 'undefined' ? navigator.language || '' : ''
  return sys.toLowerCase().startsWith('de') ? 'de' : 'en'
}

export function isChosen(): boolean {
  return chosen
}

// Used by tests, and to ask again after the stored choice was cleared.
export function setChosen(value: boolean): void {
  if (chosen === value) return
  chosen = value
  listeners.forEach((l) => l())
}

export function getLang(): Lang {
  return current
}

export function setLang(lang: Lang, remember = true): void {
  if (remember) {
    try {
      window.localStorage.setItem(STORAGE_KEY, lang)
    } catch {
      /* not remembered, still switched */
    }
    chosen = true
  }
  if (lang === current) {
    if (remember) listeners.forEach((l) => l())
    return
  }
  current = lang
  if (typeof document !== 'undefined') document.documentElement.lang = lang
  listeners.forEach((l) => l())
}

function subscribe(l: () => void): () => void {
  listeners.add(l)
  return () => listeners.delete(l)
}

// Components that must redraw when the language changes call this once.
export function useLang(): Lang {
  return useSyncExternalStore(subscribe, getLang, getLang)
}

// True once a language has been chosen; the first-start question is shown while it is false.
export function useChosen(): boolean {
  return useSyncExternalStore(subscribe, isChosen, isChosen)
}

export type Vars = Record<string, string | number>

function fill(text: string, vars?: Vars): string {
  if (!vars) return text
  return text.replace(/\{(\w+)\}/g, (m, k: string) => (k in vars ? String(vars[k]) : m))
}

// Messages from the server and from the Rust side arrive as finished German sentences, some with a
// piece filled in (a file name, an operating-system error). Their English entries carry the same
// {name} markers; when there is no exact entry, the sentence is matched against those as a pattern.
interface Pattern { re: RegExp; names: string[]; en: string }
let patterns: Pattern[] | null = null

function buildPatterns(): Pattern[] {
  const out: Pattern[] = []
  for (const key of Object.keys(EN)) {
    if (!/\{\w+\}/.test(key)) continue
    const names: string[] = []
    let literal = 0
    const src = key
      .split(/(\{\w+\})/)
      .map((part) => {
        const m = /^\{(\w+)\}$/.exec(part)
        if (m) {
          names.push(m[1])
          return '([\\s\\S]+?)'
        }
        literal += part.trim().length
        return part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')
      })
      .join('')
    if (literal < 6) continue // a pattern made of little else than markers would match anything
    out.push({ re: new RegExp('^' + src + '$'), names, en: EN[key] })
  }
  return out
}

function viaPattern(de: string): string | undefined {
  patterns ??= buildPatterns()
  for (const p of patterns) {
    const m = p.re.exec(de)
    if (!m) continue
    const vars: Vars = {}
    p.names.forEach((n, i) => { vars[n] = t(m[i + 1]) })
    return fill(p.en, vars)
  }
  return undefined
}

export function t(de: string, vars?: Vars): string {
  if (current !== 'en') return fill(de, vars)
  const text = EN[de] ?? (vars ? undefined : viaPattern(de)) ?? de
  return fill(text, vars)
}

// The locale for dates and numbers.
export function locale(): string {
  return current === 'en' ? 'en-GB' : 'de-DE'
}

export function allEnglishKeys(): string[] {
  return Object.keys(EN)
}
