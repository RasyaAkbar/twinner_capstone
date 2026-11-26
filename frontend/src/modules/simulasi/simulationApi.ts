
/**
 * Simulation Engine API
 * SCRUM-30 — Simulation Result Screen
 *
 * Uses the existing authenticated API client.
 */

import {
  apiGet,
  apiPost,
} from '../../shared/api/client';

import type {
  RunPreview,
  SimulationRunRequest,
  SimulationResult,
  ProjectDrilldown,
  MemberDrilldown,
} from './simulationTypes';

export type * from './simulationTypes';

const BASE_PATH = '/simulasi/scenarios';

function scenarioPath(scenarioId: number): string {
  return `${BASE_PATH}/${scenarioId}`;
}

// ─── Run preview ────────────────────────────

export function getRunPreview(
  scenarioId: number,
  signal?: AbortSignal,
) {
  return apiGet<RunPreview>(
    `${scenarioPath(scenarioId)}/run-preview`,
    signal,
  );
}

// ─── Execute simulation ─────────────────────

export function runSimulation(
  scenarioId: number,
  input: SimulationRunRequest,
) {
  return apiPost<SimulationResult>(
    `${scenarioPath(scenarioId)}/run`,
    input,
  );
}

// ─── Retrieve saved result ──────────────────

export function getSimulationResult(
  scenarioId: number,
  signal?: AbortSignal,
) {
  return apiGet<SimulationResult>(
    `${scenarioPath(scenarioId)}/result`,
    signal,
  );
}

// ─── Project drill-down ─────────────────────

export function getProjectDrilldown(
  scenarioId: number,
  projectId: string,
  signal?: AbortSignal,
) {
  return apiGet<ProjectDrilldown>(
    `${scenarioPath(scenarioId)}/result/projects/${encodeURIComponent(projectId)}`,
    signal,
  );
}

// ─── Member drill-down ──────────────────────

export function getMemberDrilldown(
  scenarioId: number,
  resourceId: string,
  signal?: AbortSignal,
) {
  return apiGet<MemberDrilldown>(
    `${scenarioPath(scenarioId)}/result/members/${encodeURIComponent(resourceId)}`,
    signal,
  );
}
