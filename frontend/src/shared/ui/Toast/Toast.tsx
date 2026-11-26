
import { useEffect } from 'react'
import type { AlertVariant } from '../Alert'
import './Toast.css'

export interface ToastProps {
  open: boolean
  variant?: AlertVariant
  title: string
  message?: string
  duration?: number
  onClose: () => void
}

export function Toast({
  open,
  variant = 'info',
  title,
  message,
  duration = 4000,
  onClose,
}: ToastProps) {
  useEffect(() => {
    if (!open || duration <= 0) return

    const timer = window.setTimeout(onClose, duration)
    return () => window.clearTimeout(timer)
  }, [open, duration, onClose])

  if (!open) return null

  return (
    <div
      className={`dt-toast dt-toast--${variant}`}
      role={variant === 'danger' ? 'alert' : 'status'}
    >
      <span className="dt-toast__indicator" />

      <div className="dt-toast__body">
        <strong>{title}</strong>
        {message && <p>{message}</p>}
      </div>

      <button
        type="button"
        onClick={onClose}
        aria-label="Close notification"
      >
        ×
      </button>
    </div>
  )
}
