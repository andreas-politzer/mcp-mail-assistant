import { useState } from 'react'
import { MOCK_ROOTS, type FileNode, type MockRoot } from '../mock/files'
import { countAttachable, effective, isSensitiveName, type Rules } from '../logic/fileRules'
import type { AskConfirm } from '../core/types'
import { Win95Modal } from '../components/Win95Window'

export function FilesTile({ ask }: { ask: AskConfirm }) {
  const [root, setRoot] = useState<MockRoot>(MOCK_ROOTS[0])
  const [rulesByRoot, setRulesByRoot] = useState<Record<string, Rules>>(() =>
    Object.fromEntries(MOCK_ROOTS.map((r) => [r.id, { ...r.rules }])),
  )
  const [open, setOpen] = useState<Set<string>>(new Set(['Projekte', 'Backen']))
  const [picking, setPicking] = useState(false)

  const rules = rulesByRoot[root.id]
  const tree = root.tree

  const toggle = (path: string) =>
    setOpen((prev) => {
      const next = new Set(prev)
      if (next.has(path)) next.delete(path)
      else next.add(path)
      return next
    })

  const setRule = (path: string, kind?: 'allow' | 'deny') =>
    setRulesByRoot((prev) => {
      const next = { ...prev[root.id] }
      if (kind) next[path] = kind
      else delete next[path]
      return { ...prev, [root.id]: next }
    })

  const choose = (r: MockRoot) => {
    setRoot(r)
    setOpen(new Set())
    setPicking(false)
  }

  const allow = (path: string) => {
    const after = { ...rules, [path]: 'allow' as const }
    const gain = countAttachable(tree, after, path) - countAttachable(tree, rules, path)
    ask({
      title: 'Ordner freigeben?',
      body: (
        <>
          Claude darf aus <b>{path}</b> künftig <b>{gain}</b> {gain === 1 ? 'Datei' : 'Dateien'} an Mails anhängen. Bereits gesperrte Unterordner und Dateien bleiben gesperrt.
        </>
      ),
      confirmLabel: 'Freigeben',
      run: () => setRule(path, 'allow'),
    })
  }

  const unblock = (path: string) =>
    ask({
      title: 'Sperre aufheben?',
      body: <>Die Sperre auf <b>{path}</b> fällt weg. Ob Claude dort etwas anhängen darf, hängt danach von den übrigen Regeln ab.</>,
      confirmLabel: 'Sperre aufheben',
      run: () => setRule(path),
    })

  const renderNode = (node: FileNode, base: string, depth: number) => {
    const path = base ? `${base}/${node.name}` : node.name
    const isFolder = node.kind === 'folder'
    const eff = effective(path, rules)
    const own = rules[path]
    const isOpen = open.has(path)
    const hardDenied = eff.state === 'denied' && own !== 'deny'

    let badge = <span className="text-[11px] opacity-60 whitespace-nowrap">nicht freigegeben</span>
    if (eff.state === 'allowed') badge = <span className="text-[11px] whitespace-nowrap" style={{ color: '#b9ffd8' }}>✓ erlaubt{eff.source !== path ? ` (über „${eff.source.split('/').pop()}“)` : ''}</span>
    if (eff.state === 'denied') badge = <span className="text-[11px] whitespace-nowrap" style={{ color: '#ffc9cd' }}>⛔ {eff.reason}</span>

    return (
      <div key={path}>
        <div className="flex items-center gap-2 py-1 border-b border-white/10" style={{ paddingLeft: depth * 16 }}>
          {isFolder ? (
            <button className="w-4 text-xs opacity-90" onClick={() => toggle(path)} aria-label={isOpen ? 'zuklappen' : 'aufklappen'}>
              {isOpen ? '▾' : '▸'}
            </button>
          ) : (
            <span className="w-4 text-xs opacity-60">·</span>
          )}
          <span className="text-[13px] flex-1 min-w-0 truncate">{isFolder ? '📁' : '📄'} {node.name}{!isFolder && isSensitiveName(node.name) && ' 🔑'}</span>
          {badge}
          {!hardDenied && isFolder && own !== 'allow' && own !== 'deny' && <button className="gbtn primary" onClick={() => allow(path)}>Freigeben</button>}
          {!hardDenied && isFolder && own === 'allow' && <button className="gbtn" onClick={() => setRule(path)}>Zurücksetzen</button>}
          {!hardDenied && own !== 'deny' && <button className="gbtn danger" onClick={() => setRule(path, 'deny')}>Sperren</button>}
          {own === 'deny' && <button className="gbtn" onClick={() => unblock(path)}>Sperre aufheben</button>}
        </div>
        {isFolder && isOpen && node.children?.map((c) => renderNode(c, path, depth + 1))}
      </div>
    )
  }

  const total = countAttachable(tree, rules)

  return (
    <div className="tile-text flex-1 min-h-0 flex flex-col">
      <div className="px-6 pb-2">
        <div className="flex items-center gap-2">
          <span className="text-[11px] uppercase tracking-widest opacity-80">Wurzelordner</span>
          <span className="text-[13px] font-semibold truncate flex-1 min-w-0">📂 {root.path}</span>
          <button className="gbtn" onClick={() => setPicking(true)}>Ändern…</button>
        </div>
        <div className="text-[11px] mt-1.5 opacity-90">
          Claude kann aktuell <b>{total}</b> {total === 1 ? 'Datei' : 'Dateien'} anhängen. Die Regeln gelten nur für diesen Ordner und werden beim Wechsel nicht übernommen. Eine Sperre gewinnt immer. Einzelne Dateien lassen sich nur sperren, nicht separat freigeben.
        </div>
      </div>

      <div className="scroll-glass flex-1 min-h-0 px-6 pb-5 border-t border-white/30">
        {tree.map((n) => renderNode(n, '', 0))}
      </div>

      {picking && (
        <Win95Modal title="Wurzelordner wählen" width={400} onClose={() => setPicking(false)}>
          <div className="text-xs mb-3 opacity-80">Im echten Programm öffnet sich hier die Ordnerauswahl deines Betriebssystems. Im Prototyp stehen zwei Beispiele zur Wahl.</div>
          {MOCK_ROOTS.map((r) => (
            <button key={r.id} className="bevel w-full text-left mb-2 py-2" onClick={() => choose(r)}>
              <b>{r.label}</b>
              <span className="block text-[11px] opacity-70">{r.path}</span>
            </button>
          ))}
          <div className="flex justify-end"><button className="bevel" onClick={() => setPicking(false)}>Abbrechen</button></div>
        </Win95Modal>
      )}
    </div>
  )
}
