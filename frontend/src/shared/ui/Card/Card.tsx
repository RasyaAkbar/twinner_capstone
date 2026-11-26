
import type { HTMLAttributes, ReactNode } from 'react'
import './Card.css'

export interface CardProps extends HTMLAttributes<HTMLDivElement> {
  padding?: 'none' | 'sm' | 'md' | 'lg'
  children: ReactNode
}

export function Card({
  padding = 'md',
  className = '',
  children,
  ...props
}: CardProps) {
  return (
    <div
      className={`dt-card dt-card--${padding} ${className}`.trim()}
      {...props}
    >
      {children}
    </div>
  )
}

export interface CardHeaderProps extends HTMLAttributes<HTMLDivElement> {
  title: string
  description?: string
  action?: ReactNode
}

export function CardHeader({
  title,
  description,
  action,
  className = '',
  ...props
}: CardHeaderProps) {
  return (
    <div className={`dt-card__header ${className}`.trim()} {...props}>
      <div className="dt-card__heading">
        <h3 className="dt-card__title">{title}</h3>
        {description && (
          <p className="dt-card__description">{description}</p>
        )}
      </div>
      {action && <div className="dt-card__action">{action}</div>}
    </div>
  )
}

export function CardContent({
  className = '',
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`dt-card__content ${className}`.trim()} {...props}>
      {children}
    </div>
  )
}

export function CardFooter({
  className = '',
  children,
  ...props
}: HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={`dt-card__footer ${className}`.trim()} {...props}>
      {children}
    </div>
  )
}
