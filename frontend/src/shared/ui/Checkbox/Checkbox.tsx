
import { forwardRef, useId, type InputHTMLAttributes } from 'react'
import './Checkbox.css'

export interface CheckboxProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  label: string
  description?: string
}

export const Checkbox = forwardRef<HTMLInputElement, CheckboxProps>(
  (
    {
      label,
      description,
      className = '',
      id,
      ...props
    },
    ref,
  ) => {
    const generatedId = useId()
    const checkboxId = id ?? generatedId
    const descriptionId = `${checkboxId}-description`

    return (
      <div className={`dt-checkbox-field ${className}`}>
        <input
          ref={ref}
          id={checkboxId}
          type="checkbox"
          className="dt-checkbox"
          aria-describedby={description ? descriptionId : undefined}
          {...props}
        />

        <div className="dt-checkbox-field__content">
          <label htmlFor={checkboxId}>{label}</label>
          {description && (
            <span id={descriptionId}>{description}</span>
          )}
        </div>
      </div>
    )
  },
)

Checkbox.displayName = 'Checkbox'
