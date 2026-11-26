
import { forwardRef, type InputHTMLAttributes } from 'react'
import './SearchInput.css'

export interface SearchInputProps
  extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type'> {
  onClear?: () => void
}

export const SearchInput = forwardRef<HTMLInputElement, SearchInputProps>(
  (
    {
      value,
      onClear,
      className = '',
      placeholder = 'Search...',
      ...props
    },
    ref,
  ) => (
    <div className={`dt-search ${className}`}>
      <svg
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        aria-hidden="true"
      >
        <circle cx="11" cy="11" r="7" />
        <path d="m20 20-4-4" />
      </svg>

      <input
        ref={ref}
        type="search"
        value={value}
        placeholder={placeholder}
        aria-label={props['aria-label'] ?? placeholder}
        {...props}
      />

      {value != null && String(value).length > 0 && onClear && (
        <button
          type="button"
          onClick={onClear}
          aria-label="Clear search"
        >
          ×
        </button>
      )}
    </div>
  ),
)

SearchInput.displayName = 'SearchInput'
