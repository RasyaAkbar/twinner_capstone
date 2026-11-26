
import type { HTMLAttributes, ReactNode } from 'react'
import './Badge.css'

export type BadgeVariant =
  | 'neutral'
  | 'primary'
  | 'success'
  | 'warning'
  | 'danger'
  | 'info'

export interface BadgeProps extends HTMLAttributes<HTMLSpanElement> {
  variant?: BadgeVariant
  size?: 'sm' | 'md'
  dot?: boolean
  children: ReactNode
}

export function Badge({
  variant = 'neutral',
  size = 'md',
  dot = false,
  className = '',
  children,
  ...props
}: BadgeProps) {
  return (
    <span
      className={[
        'dt-badge',
        `dt-badge--${variant}`,
        `dt-badge--${size}`,
        className,
      ].filter(Boolean).join(' ')}
      {...props}
    >
      {dot && <span className="dt-badge__dot" aria-hidden="true" />}
      {children}
    </span>
  )
}
