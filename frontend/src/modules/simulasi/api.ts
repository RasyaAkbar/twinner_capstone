
/**
 * Scenario Manager API
 * SCRUM-19 / SCRUM-23
 *
 * Uses the existing authenticated API client.
 */

import {
  apiGet,
  apiPost,
  apiPatch,
  apiDelete,
} from '../../shared/api/client';

import type {
  Baseline,
  SnapshotSummary,
  Scenario,
  ScenarioSummary,
  ScenarioCreate,
  ScenarioUpdate,
  ScenarioDuplicate,
  ScenarioStatus,
  Situation,
  SituationInfo,
} from './types';

export type * from './types';

const BASE_PATH = '/simulasi';
const SCENARIOS_PATH = `${BASE_PATH}/scenarios`;
const SNAPSHOTS_PATH = `${BASE_PATH}/baseline-snapshots`;

// Baseline snapshots

export function getBaseline(signal?: AbortSignal) {
  return apiGet<Baseline>(
    `${SNAPSHOTS_PATH}/current`,
    signal,
  );
}

export function getBaselineById(
  snapshotId: number,
  signal?: AbortSignal,
) {
  return apiGet<Baseline>(
    `${SNAPSHOTS_PATH}/${snapshotId}`,
    signal,
  );
}

export function listBaselineSnapshots(
  signal?: AbortSignal,
) {
  return apiGet<SnapshotSummary[]>(
    SNAPSHOTS_PATH,
    signal,
  );
}

// Decision situations

export function listSituations(signal?: AbortSignal) {
  return apiGet<SituationInfo[]>(
    `${SCENARIOS_PATH}/situations`,
    signal,
  );
}

// Scenario Manager

export type ScenarioFilters = {
  status?: ScenarioStatus;
  situation?: Situation;
  limit?: number;
};

export function listScenarios(
  filters: ScenarioFilters = {},
  signal?: AbortSignal,
) {
  const query = new URLSearchParams();

  if (filters.status) {
    query.set('status', filters.status);
  }

  if (filters.situation) {
    query.set('situation', filters.situation);
  }

  if (filters.limit !== undefined) {
    query.set('limit', String(filters.limit));
  }

  const suffix = query.toString();

  return apiGet<ScenarioSummary[]>(
    `${SCENARIOS_PATH}${suffix ? `?${suffix}` : ''}`,
    signal,
  );
}

export function getScenario(
  scenarioId: number,
  signal?: AbortSignal,
) {
  return apiGet<Scenario>(
    `${SCENARIOS_PATH}/${scenarioId}`,
    signal,
  );
}

export function createScenario(input: ScenarioCreate) {
  return apiPost<Scenario>(SCENARIOS_PATH, input);
}

export function updateScenario(
  scenarioId: number,
  input: ScenarioUpdate,
) {
  return apiPatch<Scenario>(
    `${SCENARIOS_PATH}/${scenarioId}`,
    input,
  );
}

export function saveScenario(scenarioId: number) {
  return apiPost<Scenario>(
    `${SCENARIOS_PATH}/${scenarioId}/save`,
    undefined,
  );
}

export function duplicateScenario(
  scenarioId: number,
  input: ScenarioDuplicate = {},
) {
  return apiPost<Scenario>(
    `${SCENARIOS_PATH}/${scenarioId}/duplicate`,
    input,
  );
}

export function deleteScenario(scenarioId: number) {
  return apiDelete(
    `${SCENARIOS_PATH}/${scenarioId}`,
  );
}
