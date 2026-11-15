// Simulated recipients. Addresses use the reserved example domain.
// rejected and removed stay on record (hidden in the lists) so an import never brings them back.
export type RecipientStatus = 'new' | 'active' | 'blocked' | 'rejected' | 'removed'

export interface Recipient {
  id: number
  name: string
  address: string
  alias?: string
  status: RecipientStatus
  /** true if the assistant proposed it in the chat */
  proposed?: boolean
}

export const MOCK_RECIPIENTS: Recipient[] = [
  { id: 1, name: 'Ich (privat)', address: 'ich@example.org', alias: 'ich', status: 'active' },
  { id: 2, name: 'Ich (Arbeit)', address: 'ich.arbeit@example.org', alias: 'arbeit', status: 'active' },
  { id: 3, name: 'Fortinbras', address: 'fortinbras@example.org', alias: 'fortinbras', status: 'active' },
  { id: 4, name: 'Anna Beispiel', address: 'anna@example.org', status: 'new', proposed: true },
  { id: 5, name: 'Energieversorger', address: 'kundenservice@example.org', status: 'new', proposed: true },
  { id: 6, name: 'Gewinnspiel-Mail', address: 'gewinn@example.org', status: 'blocked' },
  { id: 7, name: 'Newsletter Werbung', address: 'werbung@example.org', status: 'blocked' },
]
