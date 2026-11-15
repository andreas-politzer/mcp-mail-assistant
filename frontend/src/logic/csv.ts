// Small, forgiving CSV reader for contact lists. Runs in the browser only;
// nothing is uploaded. Only name and address are kept, never other columns.
export interface Candidate {
  name: string
  address: string
}

export interface ParseResult {
  rows: Candidate[]
  invalid: number
  known: number
  note?: string
}

const MAX_BYTES = 1_000_000
const MAX_ROWS = 5000
const EMAIL = /^[^\s@,;]+@[^\s@,;]+\.[^\s@,;]+$/

function splitLine(line: string, delim: string): string[] {
  const out: string[] = []
  let cur = ''
  let quoted = false
  for (let i = 0; i < line.length; i++) {
    const c = line[i]
    if (quoted) {
      if (c === '"' && line[i + 1] === '"') { cur += '"'; i++ }
      else if (c === '"') quoted = false
      else cur += c
    } else if (c === '"') quoted = true
    else if (c === delim) { out.push(cur.trim()); cur = '' }
    else cur += c
  }
  out.push(cur.trim())
  return out
}

export function parseContacts(text: string, known: Set<string>): ParseResult {
  if (text.length > MAX_BYTES) return { rows: [], invalid: 0, known: 0, note: 'Die Datei ist größer als 1 MB und wurde nicht gelesen.' }
  const lines = text.replace(/^﻿/, '').split(/\r?\n/).filter((l) => l.trim() !== '')
  if (lines.length === 0) return { rows: [], invalid: 0, known: 0, note: 'Die Datei ist leer.' }
  const delim = (lines[0].match(/;/g)?.length ?? 0) > (lines[0].match(/,/g)?.length ?? 0) ? ';' : ','
  const first = splitLine(lines[0], delim)
  const hasHeader = !first.some((c) => EMAIL.test(c))
  const header = hasHeader ? first.map((c) => c.toLowerCase()) : []
  const find = (re: RegExp) => header.findIndex((h) => re.test(h))
  const mailCol = hasHeader ? find(/mail|adresse|address/) : -1
  const nameCol = hasHeader ? find(/^name$|anzeige|display|full/) : -1
  const givenCol = hasHeader ? find(/vorname|first|given/) : -1
  const familyCol = hasHeader ? find(/nachname|last|family|surname/) : -1

  const seen = new Set(known)
  const rows: Candidate[] = []
  let invalid = 0
  let knownCount = 0
  let truncated = false
  for (const line of lines.slice(hasHeader ? 1 : 0)) {
    if (rows.length >= MAX_ROWS) { truncated = true; break }
    const cells = splitLine(line, delim)
    const address = (mailCol >= 0 ? cells[mailCol] : cells.find((c) => EMAIL.test(c))) ?? ''
    if (!EMAIL.test(address)) { invalid++; continue }
    const key = address.toLowerCase()
    if (seen.has(key)) { knownCount++; continue }
    seen.add(key)
    let name = ''
    if (nameCol >= 0) name = cells[nameCol] ?? ''
    else if (givenCol >= 0 || familyCol >= 0) name = `${cells[givenCol] ?? ''} ${cells[familyCol] ?? ''}`.trim()
    else if (!hasHeader) name = cells.find((c) => c && !EMAIL.test(c)) ?? ''
    if (!name) name = address.split('@')[0]
    rows.push({ name, address })
  }
  return { rows, invalid, known: knownCount, note: truncated ? `Es wurden nur die ersten ${MAX_ROWS} Zeilen gelesen.` : undefined }
}

const FIRST = ['Anna', 'Ben', 'Clara', 'David', 'Emma', 'Felix', 'Greta', 'Hannes', 'Ida', 'Jonas', 'Klara', 'Lukas']
const LAST = ['Beispiel', 'Muster', 'Probst', 'Nord', 'Siebert', 'Wolff', 'Kramer', 'Jansen', 'Brandt']

// Demo data for the prototype (fake people, reserved example domain).
export function sampleCsv(): string {
  const lines = ['Name,E-Mail,Telefon']
  for (let i = 0; i < 60; i++) {
    const first = FIRST[i % FIRST.length]
    const last = LAST[(i * 5) % LAST.length]
    lines.push(`${first} ${last},${first.toLowerCase()}.${last.toLowerCase()}${i}@example.org,040 123${i}`)
  }
  return lines.join('\n')
}
