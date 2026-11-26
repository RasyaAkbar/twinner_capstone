import type { ParameterIssue } from './types';
import type { ApiIssue } from '../../shared/api/client';

export type FieldErrors = Record<string, string[]>;

/**
 * Convert backend validation locations into form paths.
 *
 * ["body", "parameters", "absence_period", "end"]
 * -> "absence_period.end"
 *
 * ["body", "parameters", "added_tasks", 0, "task_name"]
 * -> "added_tasks.0.task_name"
 */
export function getFieldPath(
  location: Array<string | number>,
): string {
  const path = [...location];

  if (path[0] === 'body') {
    path.shift();
  }

  if (path[0] === 'parameters') {
    path.shift();
  }

  return path.length ? path.join('.') : '_form';
}

/**
 * Preserve all validation messages for each field.
 */
export function mapParameterErrors(
  issues: readonly ParameterIssue[],
): FieldErrors {
  const errors: FieldErrors = {};

  for (const issue of issues) {
    const path = getFieldPath(issue.loc);

    errors[path] = [
      ...(errors[path] ?? []),
      issue.msg,
    ];
  }

  return errors;
}

/**
 * Retrieve errors for a specific input.
 */
export function getFieldErrors(
  errors: FieldErrors,
  path: string,
): string[] {
  return errors[path] ?? [];
}

/**
 * Retrieve errors belonging to a group or array.
 */
export function getNestedErrors(
  errors: FieldErrors,
  path: string,
): string[] {
  return Object.entries(errors)
    .filter(
      ([key]) =>
        key === path ||
        key.startsWith(`${path}.`),
    )
    .flatMap(([, messages]) => messages);
}

/**
 * Remove stale errors when a field is edited.
 */
export function clearFieldErrors(
  errors: FieldErrors,
  path: string,
): FieldErrors {
  return Object.fromEntries(
    Object.entries(errors).filter(
      ([key]) =>
        key !== path &&
        !key.startsWith(`${path}.`),
    ),
  );
}

/**
 * Convert normalized API errors into nested form errors.
 */
export function mapApiErrors(
  issues: readonly ApiIssue[],
): FieldErrors {
  const errors: FieldErrors = {};

  for (const issue of issues) {
    const path = issue.path
      ? getFieldPath(issue.path)
      : issue.field ?? '_form';

    errors[path] = [
      ...(errors[path] ?? []),
      issue.message,
    ];
  }

  return errors;
}