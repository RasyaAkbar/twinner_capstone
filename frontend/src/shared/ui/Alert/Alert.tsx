
import type { HTMLAttributes, ReactNode } from 'react'
import './Alert.css'

export type AlertVariant = 'success' | 'warning' | 'danger' | 'info'

export interface AlertProps extends HTMLAttributes<HTMLDivElement> {
  variant?: AlertVariant
  title?: string
  children: ReactNode
  onDismiss?: () => void
}

const icons: Record<AlertVariant, string> = {
  success: '✓',
  warning: '!',
  danger: '×',
  info: 'i',
}

export function Alert({
  variant = 'info',
  title,
  children,
  onDismiss,
  className = '',
  ...props
}: AlertProps) {
  return (
    <div
      role={variant === 'danger' ? 'alert' : 'status'}
      className={`dt-alert dt-alert--${variant} ${className}`.trim()}
      {...props}
    >
      <span className="dt-alert__icon" aria-hidden="true">
        {icons[variant]}
      </span>

      <div className="dt-alert__body">
        {title && <strong className="dt-alert__title">{title}</strong>}
        <div className="dt-alert__message">{children}</div>
      </div>

      {onDismiss && (
        <button
          type="button"
          className="dt-alert__dismiss"
          onClick={onDismiss}
          aria-label="Dismiss alert"
        >
          ×
        </button>
      )}
    </div>
  )
}
