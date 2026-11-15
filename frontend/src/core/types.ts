import type { ReactNode } from 'react'

export interface ModulePosition {
  x: number
  y: number
}

// A tile calls this before an action that loosens a permission
// (or to show a simple notice, with info: true).
export interface ConfirmRequest {
  title: string
  body: ReactNode
  confirmLabel: string
  run: () => void
  info?: boolean
}
export type AskConfirm = (request: ConfirmRequest) => void
