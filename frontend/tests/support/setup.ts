import { afterEach, vi } from 'vitest'
import { setChosen, setLang } from '../../src/i18n'

// The existing tests look for German text; English tests switch the language themselves.
setLang('de', false)
setChosen(true)
afterEach(() => {
  setLang('de', false)
  setChosen(true)
})

// The app code checks for the Tauri bridge once, when it loads.
;(window as unknown as Record<string, unknown>).__TAURI_INTERNALS__ = {}

// Every call to the Rust side goes to the stand-in that the test file installs (support/harness.tsx).
vi.mock('@tauri-apps/api/core', () => ({
  invoke: (cmd: string, args?: Record<string, unknown>) =>
    (globalThis as unknown as { __fake: { invoke: (c: string, a?: Record<string, unknown>) => Promise<unknown> } }).__fake.invoke(cmd, args),
}))

// jsdom has no pointer capture; the desktop icons use it while dragging.
for (const name of ['setPointerCapture', 'releasePointerCapture', 'hasPointerCapture'] as const) {
  if (!(name in HTMLElement.prototype)) (HTMLElement.prototype as unknown as Record<string, unknown>)[name] = () => false
}
