import type { FileNode, RuleKind } from '../mock/files'

export type Rules = Record<string, RuleKind>

// Name parts the server always treats as sensitive (mirrors the real policy idea).
const SENSITIVE = ['passw', 'kennwort', 'zugangsdaten', 'secret', 'token', 'apikey', 'api-key']

export function isSensitiveName(name: string): boolean {
  const n = name.toLowerCase()
  return SENSITIVE.some((part) => n.includes(part))
}

export type Effective =
  | { state: 'allowed'; source: string }
  | { state: 'denied'; reason: string; source: string | null }
  | { state: 'none' }

// Rule order: a deny always wins, then sensitive names, then an allow.
export function effective(path: string, rules: Rules): Effective {
  const parts = path.split('/')
  const prefixes = parts.map((_, i) => parts.slice(0, i + 1).join('/'))
  for (const p of prefixes) {
    if (rules[p] === 'deny') {
      return { state: 'denied', reason: p === path ? 'von dir gesperrt' : `gesperrt durch „${p}“`, source: p }
    }
  }
  if (parts.some(isSensitiveName)) {
    return { state: 'denied', reason: 'sensibler Name, immer gesperrt', source: null }
  }
  for (const p of prefixes) {
    if (rules[p] === 'allow') return { state: 'allowed', source: p }
  }
  return { state: 'none' }
}

export function listFiles(nodes: FileNode[], base = ''): string[] {
  const out: string[] = []
  for (const n of nodes) {
    const path = base ? `${base}/${n.name}` : n.name
    if (n.kind === 'file') out.push(path)
    else out.push(...listFiles(n.children ?? [], path))
  }
  return out
}

export function findNode(nodes: FileNode[], path: string): FileNode | undefined {
  const [head, ...rest] = path.split('/')
  const node = nodes.find((n) => n.name === head)
  if (!node || rest.length === 0) return node
  return findNode(node.children ?? [], rest.join('/'))
}

// How many files Claude could attach.
export function countAttachable(tree: FileNode[], rules: Rules, under?: string): number {
  return listFiles(tree)
    .filter((p) => (under ? p === under || p.startsWith(`${under}/`) : true))
    .filter((p) => effective(p, rules).state === 'allowed').length
}
