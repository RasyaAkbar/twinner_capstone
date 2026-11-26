import { useId } from 'react';

import type {
  Baseline,
  ParameterSchema,
  Parameters,
  SituationInfo,
} from './types';

import {
  resolveSchema,
  createInitialValue,
  getFieldLabel,
  getValueLabel,
} from './situations';

import { getBaselineOptions } from './baselineOptions';
import { getFieldErrors, type FieldErrors } from './validation';

import './scenarioForm.css';

export type Draft = {
  name: string;
  parameters: Parameters;
};

type Props = {
  baseline: Baseline;
  situation: SituationInfo;
  draft: Draft;
  errors: FieldErrors;
  disabled?: boolean;
  onChange: (draft: Draft) => void;
};

export function ScenarioForm({
  baseline,
  situation,
  draft,
  errors,
  disabled = false,
  onChange,
}: Props) {
  const schema = situation.parameters_schema;
  const root = resolveSchema(schema, schema);

  return (
    <fieldset className="sim-form-fields" disabled={disabled}>
      <div className="sim-form">
        <div className="form-grid">
          <label className="field field-wide">
            <span>Nama skenario *</span>
            <input
              value={draft.name}
              placeholder="Masukkan nama skenario"
              aria-invalid={Boolean(errors.name?.length)}
              onChange={(e) =>
                onChange({
                  ...draft,
                  name: e.target.value,
                })
              }
            />
            <ErrorMessages messages={errors.name ?? []} />
          </label>

          <div className="field field-wide">
            <span>Situasi keputusan</span>
            <input value={situation.label} readOnly />
            <small>{situation.description}</small>
          </div>

          {Object.entries(root.properties ?? {}).map(
            ([key, field]) => (
              <ParameterField
                key={key}
                fieldKey={key}
                path={key}
                schema={field}
                rootSchema={schema}
                baseline={baseline}
                situation={situation}
                parameters={draft.parameters}
                value={draft.parameters[key]}
                required={root.required?.includes(key)}
                errors={errors}
                onChange={(value) => {
                  const parameters = {
                    ...draft.parameters,
                    [key]: value,
                  };

                  // Clear dependent selections.
                  if (key === 'project_id') {
                    if (situation.situation === 'change_request') {
                      parameters.removed_task_ids = [];
                    }

                    if (situation.situation === 'priority_change') {
                      parameters.affected_task_ids = [];
                    }
                  }

                  if (key === 'resource_id') {
                    if (situation.situation === 'member_absence') {
                      parameters.affected_task_ids = [];
                    }

                    if (situation.situation === 'member_rotation') {
                      parameters.existing_assignment_ids = [];
                    }
                  }

                  if (key === 'origin_project_id') {
                    parameters.destination_project_id = null;
                    parameters.existing_assignment_ids = [];
                  }

                  if (
                    key === 'transfer_date' &&
                    situation.situation === 'member_rotation'
                  ) {
                    parameters.existing_assignment_ids = [];
                  }

                  if (
                    key === 'task_id' &&
                    situation.situation === 'task_blocker'
                  ) {
                    parameters.blocking_task_id = null;
                    parameters.independent_task_ids = [];
                  }

                  onChange({ ...draft, parameters });
                }}
              />
            ),
          )}

          <ErrorMessages messages={errors._form ?? []} />
        </div>
      </div>
    </fieldset>
  );
}

type FieldProps = {
  fieldKey: string;
  path: string;
  schema: ParameterSchema;
  rootSchema: ParameterSchema;
  baseline: Baseline;
  situation: SituationInfo;
  parameters: Parameters;
  value: unknown;
  required?: boolean;
  errors: FieldErrors;
  onChange: (value: unknown) => void;
};

function ErrorMessages({
  messages,
}: {
  messages: string[];
}) {
  if (!messages.length) return null;

  return (
    <div className="sim-field-errors" role="alert">
      {messages.map((message, index) => (
        <small key={index}>{message}</small>
      ))}
    </div>
  );
}

function ParameterField(props: FieldProps) {
  const {
    fieldKey,
    path,
    rootSchema,
    baseline,
    situation,
    parameters,
    value,
    required,
    errors,
    onChange,
  } = props;

  const inputId = useId();
  const schema = resolveSchema(props.schema, rootSchema);
  const label = getFieldLabel(fieldKey);
  const messages = getFieldErrors(errors, path);

  const options = getBaselineOptions(
    fieldKey,
    baseline,
    situation.situation,
    parameters,
  );

  const textValue =
    value === null || value === undefined
      ? ''
      : String(value);

  const invalid = messages.length > 0;

  // Nested objects, such as absence_period.
  if (schema.type === 'object') {
    const objectValue =
      value !== null &&
      typeof value === 'object' &&
      !Array.isArray(value)
        ? (value as Parameters)
        : {};

    return (
      <fieldset className="field-wide sim-subform">
        <legend>
          {label}{required ? ' *' : ''}
        </legend>

        <div className="form-grid">
          {Object.entries(schema.properties ?? {}).map(
            ([key, child]) => (
              <ParameterField
                {...props}
                key={key}
                fieldKey={key}
                path={`${path}.${key}`}
                schema={child}
                value={objectValue[key]}
                required={schema.required?.includes(key)}
                onChange={(next) =>
                  onChange({
                    ...objectValue,
                    [key]: next,
                  })
                }
              />
            ),
          )}
        </div>

        <ErrorMessages messages={messages} />
      </fieldset>
    );
  }

  // Arrays: task selections and repeatable objects.
  if (schema.type === 'array') {
    const values = Array.isArray(value) ? value : [];
    const itemSchema = schema.items ?? {};
    const resolvedItem = resolveSchema(itemSchema, rootSchema);

    if (options !== null) {
      return (
        <fieldset className="field-wide sim-subform">
          <legend>
            {label}{required ? ' *' : ''}
          </legend>

          <small className="sim-selection-count">
            {values.length} dipilih
          </small>

          <div className="check-list sim-option-list">
            {options.map((option) => (
              <label
                className="field-check"
                key={option.value}
              >
                <input
                  type="checkbox"
                  checked={values.includes(option.value)}
                  onChange={(e) =>
                    onChange(
                      e.target.checked
                        ? [...values, option.value]
                        : values.filter(
                            (item) => item !== option.value,
                          ),
                    )
                  }
                />
                <span>{option.label}</span>
              </label>
            ))}

            {options.length === 0 && (
              <small>Tidak ada pilihan yang tersedia.</small>
            )}
          </div>

          {values
            .filter((item) =>
              !options.some((option) => option.value === item),
            )
            .map((item) => (
              <div key={String(item)} className="sim-array-item">
                <small>Pilihan sebelumnya: {String(item)}</small>
                <button
                  type="button"
                  className="button button-ghost"
                  onClick={() =>
                    onChange(values.filter((v) => v !== item))
                  }
                >
                  Hapus
                </button>
              </div>
            ))}

          <ErrorMessages messages={messages} />

          {values.map((_, index) => (
            <ErrorMessages
              key={index}
              messages={getFieldErrors(errors, `${path}.${index}`)}
            />
          ))}
        </fieldset>
      );
    }

    return (
      <fieldset className="field-wide sim-subform">
        <legend>
          {label}{required ? ' *' : ''}
        </legend>

        {values.map((item, index) => (
          <div className="sim-array-item" key={index}>
            <div className="sim-array-heading">
              <strong>Item {index + 1}</strong>

              <button
                type="button"
                className="button button-ghost"
                onClick={() =>
                  onChange(values.filter((_, i) => i !== index))
                }
              >
                Hapus
              </button>
            </div>

            <ParameterField
              {...props}
              fieldKey={
                resolvedItem.type === 'object'
                  ? `Item ${index + 1}`
                  : fieldKey
              }
              path={`${path}.${index}`}
              schema={itemSchema}
              value={item}
              required
              onChange={(next) =>
                onChange(
                  values.map((old, i) =>
                    i === index ? next : old,
                  ),
                )
              }
            />
          </div>
        ))}

        <button
          type="button"
          className="button button-ghost"
          disabled={
            schema.maxItems !== undefined &&
            values.length >= schema.maxItems
          }
          onClick={() =>
            onChange([
              ...values,
              createInitialValue(itemSchema, rootSchema),
            ])
          }
        >
          + Tambah item
        </button>

        <ErrorMessages messages={messages} />
      </fieldset>
    );
  }

  // Entity dropdowns and enum options.
  if (options !== null || schema.enum) {
    const choices =
      options ??
      schema.enum!.map((item) => ({
        value: String(item),
        label: getValueLabel(item),
      }));

    return (
      <label className="field" htmlFor={inputId}>
        <span>{label}{required ? ' *' : ''}</span>

        <select
          id={inputId}
          value={textValue}
          aria-invalid={invalid}
          onChange={(e) => {
            const raw = e.target.value;

            if (!raw) {
              onChange(required ? '' : null);
            } else if (
              schema.type === 'number' ||
              schema.type === 'integer'
            ) {
              onChange(Number(raw));
            } else {
              onChange(raw);
            }
          }}
        >
          <option value="">Pilih {label.toLowerCase()}</option>

          {textValue &&
            !choices.some((choice) => choice.value === textValue) && (
              <option value={textValue}>
                {textValue} (pilihan sebelumnya)
              </option>
            )}

          {choices.map((choice) => (
            <option key={choice.value} value={choice.value}>
              {choice.label}
            </option>
          ))}
        </select>

        {choices.length === 0 && (
          <small>Tidak ada pilihan yang tersedia.</small>
        )}

        <ErrorMessages messages={messages} />
      </label>
    );
  }

  // Boolean fields.
  if (schema.type === 'boolean') {
    return (
      <div className="field">
        <label className="field-check">
          <input
            type="checkbox"
            checked={Boolean(value)}
            onChange={(e) => onChange(e.target.checked)}
          />
          <span>{label}</span>
        </label>

        <ErrorMessages messages={messages} />
      </div>
    );
  }

  // Numeric, date and text fields.
  const isNumeric =
    schema.type === 'number' ||
    schema.type === 'integer';

  const inputType = isNumeric
    ? 'number'
    : schema.format === 'date'
      ? 'date'
      : 'text';

  return (
    <label className="field" htmlFor={inputId}>
      <span>{label}{required ? ' *' : ''}</span>

      <input
        id={inputId}
        type={inputType}
        value={textValue}
        aria-invalid={invalid}
        min={isNumeric ? schema.minimum : undefined}
        max={isNumeric ? schema.maximum : undefined}
        step={
          isNumeric
            ? schema.type === 'integer' ? 1 : 'any'
            : undefined
        }
        maxLength={schema.maxLength}
        placeholder={
          inputType === 'text'
            ? `Masukkan ${label.toLowerCase()}`
            : undefined
        }
        onChange={(e) => {
          const raw = e.target.value;

          onChange(
            raw === ''
              ? required ? '' : null
              : isNumeric ? Number(raw) : raw,
          );
        }}
      />

      <ErrorMessages messages={messages} />
    </label>
  );
}
