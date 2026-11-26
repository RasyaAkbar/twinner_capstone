
import { forwardRef, useId, type SelectHTMLAttributes } from 'react'
import '../Input/Input.css'
import './Select.css'

export interface SelectOption {
  label: string
  value: string
  disabled?: boolean
}

export interface SelectProps
  extends Omit<SelectHTMLAttributes<HTMLSelectElement>, 'children'> {
  label?: string
  helperText?: string
  error?: string
  placeholder?: string
  options: SelectOption[]
}

export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  (
    {
      label,
      helperText,
      error,
      placeholder = 'Select an option',
      options,
      id,
      required,
      className = '',
      ...props
    },
    ref,
  ) => {
    const generatedId = useId()
    const selectId = id ?? generatedId
    const descriptionId = `${selectId}-description`

    return (
      <div className="dt-field dt-field--full">
        {label && (
          <label className="dt-field__label" htmlFor={selectId}>
            {label}
            {required && <span className="dt-field__required"> *</span>}
          </label>
        )}

        <select
          ref={ref}
          id={selectId}
          required={required}
          aria-invalid={Boolean(error)}
          aria-describedby={error || helperText ? descriptionId : undefined}
          className={`dt-input dt-select ${error ? 'dt-input--error' : ''} ${className}`}
          defaultValue={props.value === undefined && props.defaultValue === undefined ? '' : undefined}
          {...props}
        >
          <option value="" disabled>
            {placeholder}
          </option>
          {options.map((option) => (
            <option
              key={option.value}
              value={option.value}
              disabled={option.disabled}
            >
              {option.label}
            </option>
          ))}
        </select>

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

Select.displayName = 'Select'
