
import { forwardRef, useId, type InputHTMLAttributes } from 'react'
import './Input.css'

export interface InputProps
  extends InputHTMLAttributes<HTMLInputElement> {
  label?: string
  helperText?: string
  error?: string
  fullWidth?: boolean
}

export const Input = forwardRef<HTMLInputElement, InputProps>(
  (
    {
      label,
      helperText,
      error,
      fullWidth = true,
      className = '',
      id,
      required,
      ...props
    },
    ref,
  ) => {
    const generatedId = useId()
    const inputId = id ?? generatedId
    const descriptionId = `${inputId}-description`

    return (
      <div className={`dt-field ${fullWidth ? 'dt-field--full' : ''}`}>
        {label && (
          <label className="dt-field__label" htmlFor={inputId}>
            {label}
            {required && <span className="dt-field__required"> *</span>}
          </label>
        )}

        <input
          ref={ref}
          id={inputId}
          required={required}
          aria-invalid={Boolean(error)}
          aria-describedby={error || helperText ? descriptionId : undefined}
          className={`dt-input ${error ? 'dt-input--error' : ''} ${className}`}
          {...props}
        />

        {(error || helperText) && (
          <span
            id={descriptionId}
            className={`dt-field__helper ${error ? 'dt-field__helper--error' : ''}`}
          >
            {error || helperText}
          </span>
        )}
      </div>
    )
  },
)

Input.displayName = 'Input'
