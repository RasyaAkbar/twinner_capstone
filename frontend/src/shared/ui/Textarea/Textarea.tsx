import { forwardRef, useId, type TextareaHTMLAttributes } from 'react'
import '../Input/Input.css'
import './Textarea.css'

export interface TextareaProps
  extends TextareaHTMLAttributes<HTMLTextAreaElement> {
  label?: string
  helperText?: string
  error?: string
}

export const Textarea = forwardRef<HTMLTextAreaElement, TextareaProps>(
  (
    {
      label,
      helperText,
      error,
      id,
      required,
      className = '',
      rows = 4,
      ...props
    },
    ref,
  ) => {
    const generatedId = useId()
    const textareaId = id ?? generatedId
    const descriptionId = `${textareaId}-description`

    return (
      <div className="dt-field dt-field--full">
        {label && (
          <label className="dt-field__label" htmlFor={textareaId}>
            {label}
            {required && <span className="dt-field__required"> *</span>}
          </label>
        )}

        <textarea
          ref={ref}
          id={textareaId}
          rows={rows}
          required={required}
          aria-invalid={Boolean(error)}
          aria-describedby={error || helperText ? descriptionId : undefined}
          className={`dt-input dt-textarea ${error ? 'dt-input--error' : ''} ${className}`}
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

Textarea.displayName = 'Textarea'
