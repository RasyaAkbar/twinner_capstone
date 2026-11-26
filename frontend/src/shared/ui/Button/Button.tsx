
import {
  forwardRef,
  type ButtonHTMLAttributes,
  type ReactNode,
} from 'react'
import './Button.css'

export type ButtonVariant =
  | 'primary'
  | 'secondary'
  | 'outline'
  | 'ghost'
  | 'danger'

export type ButtonSize = 'sm' | 'md' | 'lg'

export interface ButtonProps
  extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  loading?: boolean
  fullWidth?: boolean
  leftIcon?: ReactNode
  rightIcon?: ReactNode
}

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  (
    {
      variant = 'primary',
      size = 'md',
      loading = false,
      fullWidth = false,
      leftIcon,
      rightIcon,
      disabled,
      className = '',
      children,
      type = 'button',
      ...props
    },
    ref,
  ) => {
    const isDisabled = disabled || loading

    return (
      <button
        ref={ref}
        type={type}
        disabled={isDisabled}
        aria-busy={loading || undefined}
        className={[
          'dt-button',
          `dt-button--${variant}`,
          `dt-button--${size}`,
          fullWidth && 'dt-button--full',
          loading && 'dt-button--loading',
          className,
        ]
          .filter(Boolean)
          .join(' ')}
        {...props}
      >
        {loading && (
          <span className="dt-button__spinner" aria-hidden="true" />
        )}

        {!loading && leftIcon && (
          <span className="dt-button__icon" aria-hidden="true">
            {leftIcon}
          </span>
        )}

        <span className="dt-button__label">{children}</span>

        {!loading && rightIcon && (
          <span className="dt-button__icon" aria-hidden="true">
            {rightIcon}
          </span>
        )}
      </button>
    )
  },
)

Button.displayName = 'Button'
