// Simulated file systems. No connection to any real folder.
export interface FileNode {
  name: string
  kind: 'folder' | 'file'
  children?: FileNode[]
}

export type RuleKind = 'allow' | 'deny'

export interface MockRoot {
  id: string
  label: string
  path: string
  tree: FileNode[]
  rules: Record<string, RuleKind>
}

const f = (name: string): FileNode => ({ name, kind: 'file' })
const d = (name: string, children: FileNode[]): FileNode => ({ name, kind: 'folder', children })
const range = (n: number, make: (i: number) => string) => Array.from({ length: n }, (_, i) => f(make(i + 1)))
const pad = (i: number) => String(i).padStart(2, '0')

const vault: FileNode[] = [
  d('Projekte', [
    d('E-Mail-Versand', [f('Arbeitsstand.md'), f('Entwurf Admin-API.md'), f('Zugangsdaten.md')]),
    d('Reiseplanung', [f('Packliste.md'), f('Ideen.md')]),
    d('Garten', range(12, (i) => `Beet ${pad(i)}.md`)),
  ]),
  d('Backen', [d('Brote', [f('Dinkelbrot.md'), f('Sauerteig.md')]), f('Kuchen.md')]),
  d('Kochen', [f('Linsensuppe.md'), f('Gemuesecurry.md'), ...range(8, (i) => `Rezept ${pad(i)}.md`)]),
  d('Finanzen', [f('Haushaltsbuch.md'), f('Steuer 2026.md')]),
  d('Behoerden', [f('Bescheid.md'), f('Schriftverkehr.md')]),
  d('Gesundheit', [f('Tagebuch.md')]),
  d('Notizen', [f('Ideen.md'), f('Buecher.md'), ...range(20, (i) => `Gedanke ${pad(i)}.md`)]),
]

const documents: FileNode[] = [
  d('Rechnungen', range(36, (i) => `Rechnung 2026-${pad(i)}.md`)),
  d('Briefe', range(14, (i) => `Brief ${pad(i)}.md`)),
  d('Uni', [
    d('Semester 1', range(10, (i) => `Vorlesung ${pad(i)}.md`)),
    d('Semester 2', range(10, (i) => `Vorlesung ${pad(i)}.md`)),
    d('Abschlussarbeit', [f('Gliederung.md'), f('Literatur.md'), f('Passwoerter Bibliothek.md')]),
  ]),
  d('Reisen', range(9, (i) => `Reise ${pad(i)}.md`)),
  ...range(6, (i) => `Lose Notiz ${pad(i)}.md`),
]

export const MOCK_ROOTS: MockRoot[] = [
  {
    id: 'vault',
    label: 'Notiz-Tresor',
    path: '~/Dokumente/Notiz-Tresor',
    tree: vault,
    rules: { Projekte: 'allow', Backen: 'allow', Kochen: 'allow', Finanzen: 'deny', Behoerden: 'deny', Gesundheit: 'deny' },
  },
  { id: 'docs', label: 'Dokumente', path: '~/Dokumente', tree: documents, rules: {} },
]
