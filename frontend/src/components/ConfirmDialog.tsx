import { t } from '../i18n'
import type { ReactNode } from 'react'
import { Win95Modal } from './Win95Window'

// Appears only where an action loosens a permission (or as a plain notice).
export function ConfirmDialog({
  title,
  children,
  confirmLabel,
  info = false,
  onConfirm,
  onCancel,
}: {
  title: string
  children: ReactNode
  confirmLabel: string
  info?: boolean
  onConfirm: () => void
  onCancel: () => void
}) {
  return (
    <Win95Modal title={t(title)} width={390} onClose={onCancel}>
      <div className="mb-4">{info ? 'ℹ️' : '🔓'} {children}</div>
      <div className="flex justify-end gap-2">
        {!info && <button className="bevel" onClick={onCancel}>{t('Abbrechen')}</button>}
        <button className="bevel font-bold" onClick={onConfirm}>{t(confirmLabel)}</button>
      </div>
    </Win95Modal>
  )
}
