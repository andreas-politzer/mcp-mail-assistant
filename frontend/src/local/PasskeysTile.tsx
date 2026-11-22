import { useCallback, useEffect, useState } from 'react'
import type { AskConfirm } from '../core/types'
import type { SetupBackend } from '../wizard/setupApi'
import type { LocalBackend, PasskeyRow } from './localApi'

// Passkeys on the local approval page: the list, removing one, and a new enrollment link. Same glass tile as the
// others. It talks to the program on this computer (the `local` command), not to a server.

const when = (t: number | null) => (t ? new Date(t * 1000).toLocaleDateString('de-DE') : 'noch nie')
const shorten = (id: string) => id.slice(0, 6)

export function PasskeysTile({ ask, local, setup }: { ask: AskConfirm; local: LocalBackend; setup: SetupBackend }) {
  const [keys, setKeys] = useState<PasskeyRow[] | null>(null)
  const [note, setNote] = useState<{ text: string; bad: boolean } | null>(null)
  const [busy, setBusy] = useState(false)

  const load = useCallback(async () => {
    const r = await local.snapshot()
    if (r.ok) setKeys(r.data.passkeys)
    else setNote({ text: r.reason, bad: true })
  }, [local])
  useEffect(() => {
    void load()
  }, [load])

  const remove = (k: PasskeyRow) =>
    ask({
      title: 'Passkey entfernen?',
      body: <>Der Passkey {shorten(k.id)} (angelegt {when(k.created_at)}) wird gelöscht. Bleibt keiner übrig, geht an fremde Adressen nichts mehr, bis ein neuer eingerichtet ist.</>,
      confirmLabel: 'Entfernen',
      run: () =>
        void (async () => {
          const r = await local.act({ action: 'passkey-remove', reference: k.id })
          setNote(r.ok ? { text: 'Der Passkey ist entfernt.', bad: false } : { text: r.reason, bad: true })
          await load()
        })(),
    })

  const enroll = async () => {
    setBusy(true)
    const link = await setup.approvalLink()
    if (!link.ok) {
      setBusy(false)
      setNote({ text: link.reason, bad: true })
      return
    }
    const opened = await setup.openLink(link.url)
    setBusy(false)
    setNote(
      opened.ok
        ? { text: 'Die Seite zum Einrichten ist im Browser offen. Danach hier auf „Aktualisieren“.', bad: false }
        : { text: `${opened.reason} Link: ${link.url}`, bad: true },
    )
  }

  return (
    <div className="p-3 text-[13px] leading-relaxed overflow-auto h-full" style={{ color: '#1d1d2b' }}>
      <div className="mb-2 opacity-80">
        Ein Passkey bestätigt Mails an fremde Adressen auf der Freigabeseite. Das kann dein Handy (per QR-Code) oder ein Sicherheitsschlüssel sein.
        Die Freigabeseite läuft nur, solange Claude Desktop geöffnet ist.
      </div>
      {note && (
        <div role="status" className="mb-2 p-2" style={{ border: `1px solid ${note.bad ? '#a00' : '#060'}`, background: note.bad ? '#fff0f0' : '#f0fff0' }}>
          {note.text}
        </div>
      )}
      {keys && keys.length === 0 && <div className="mb-2 opacity-70">Noch kein Passkey. Ohne Passkey geht nichts an fremde Adressen.</div>}
      <div className="flex flex-col gap-1 mb-3">
        {(keys ?? []).map((k) => (
          <div key={k.id} className="flex items-center gap-2 p-1.5" style={{ border: '1px solid var(--bevel-dark)', background: '#fff' }}>
            <div className="flex-1">Passkey {shorten(k.id)} · angelegt {when(k.created_at)} · zuletzt benutzt {when(k.last_used_at)}</div>
            <button className="bevel" disabled={busy} onClick={() => remove(k)}>Entfernen</button>
          </div>
        ))}
      </div>
      <div className="flex gap-2">
        <button className="bevel font-bold" disabled={busy} onClick={() => void enroll()}>Neuen Passkey einrichten</button>
        <button className="bevel" disabled={busy} onClick={() => void load()}>Aktualisieren</button>
      </div>
    </div>
  )
}
