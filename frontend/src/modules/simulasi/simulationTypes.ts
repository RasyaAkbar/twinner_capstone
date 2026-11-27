
import type { ScenarioSummary, ParameterIssue } from './types';

// SCRUM-30 — Simulation Engine API contracts

export type RunMode = 'run_only' | 'run_and_save';
export type SimulationValue = string | number | null;
export type KpiStatus = 'better' | 'worse' | 'same';
export type DataSource = 'synthetic' | 'puti';

export type TaskState =
  | 'done'
  | 'scheduled'
  | 'unscheduled'
  | 'on_hold'
  | 'dropped';

// ─── Run configuration ──────────────────────

export interface ReplicationOptions {
  runs?: number;
  seed?: number;
}

export interface ReplicationInfo {
  runs: number;
  seed: number;
}

export interface SimulationRunRequest {
  mode: RunMode;
  parameters?: Record<string, unknown> | null;
  replication?: ReplicationOptions | null;
  start_date?: string | null;
}

export interface LatestRun {
  run_id: number;
  created_at: string;
  created_by: string | null;
  start_date: string;
  replication: ReplicationInfo | null;
  is_stale: boolean;
}

export interface RunPreview {
  scenario: ScenarioSummary;
  parameters: Record<string, unknown>;
  parameter_errors: ParameterIssue[];
  can_run: boolean;
  snapshot_id: number;
  data_source: DataSource;
  default_start_date: string;
  default_replications: number;
  latest_run: LatestRun | null;
}

// ─── Replication ranges ─────────────────────

export interface SimulationRange {
  median: SimulationValue;
  p10: SimulationValue;
  p90: SimulationValue;
  min: SimulationValue;
  max: SimulationValue;
  runs: number;
  missing: number;
}

// ─── KPI results ────────────────────────────

// KPI hasil simulasi = KPI modul Evaluasi (FR-04.1 s.d. FR-04.10, `KpiEvaluationItem` backend).

export type KpiDirection = 'lower' | 'higher' | 'target';
export type KpiUnit = 'working_days' | 'probability' | 'index' | 'count' | 'ratio';

export interface KpiRange {
  median: number | null;
  p10: number | null;
  p90: number | null;
  min: number | null;
  max: number | null;
  runs: number;
  missing: number;
}

export interface KpiPoint {
  baseline: number | null;
  scenario: number | null;
  delta: number | null;
  status: KpiStatus;
}

export interface KpiReplication {
  runs: number;
  /** Kosong untuk On-Time Launch Probability (proporsi replikasi, tanpa rentang). */
  baseline: KpiRange | null;
  scenario: KpiRange | null;
  delta: KpiRange | null;
}

export interface KpiResult {
  key: string;
  fr: string;
  label: string;
  unit: KpiUnit;
  direction: KpiDirection;
  target: number | null;
  tolerance: number;
  definition: string;
  formula: string;
  /** Asal nilai utama: simulasi deterministik atau median replikasi. */
  basis: 'deterministic' | 'median';
  baseline: number | null;
  scenario: number | null;
  delta: number | null;
  status: KpiStatus;
  deterministic: KpiPoint;
  replication: KpiReplication | null;
}

export interface KpiStatusCount {
  better: number;
  same: number;
  worse: number;
}

/** Parameter rumus KPI (FR-04.11). */
export interface KpiConfig {
  wp: number;
  wo: number;
  wt: number;
  capacity_reference: number | null;
  overload_threshold: number;
  soi_min_weeks: number;
  hard_factor: number;
  soft_factor: number | null;
  tolerances: Record<string, number>;
}

// ─── Task schedule ──────────────────────────

export interface TaskSchedule {
  state: TaskState;
  start: string | null;
  finish: string | null;
  owner: string | null;
  effort: number | null;
  dependency_ready: string | null;
  reason: string | null;
}

export interface TaskReplication {
  baseline_start: SimulationRange;
  baseline_finish: SimulationRange;
  scenario_start: SimulationRange;
  scenario_finish: SimulationRange;
  baseline_unscheduled_runs: number;
  scenario_unscheduled_runs: number;
}

export interface TaskResult {
  task_id: string;
  project_id: string;
  task_name: string;
  required_role_id: string;
  complexity_id: string | null;
  added: boolean;
  targeted: boolean;
  affected: boolean;
  owner_changed: boolean;
  baseline: TaskSchedule | null;
  scenario: TaskSchedule | null;
  start_shift: number | null;
  finish_shift: number | null;
  replication: TaskReplication | null;
}

// ─── Project results ────────────────────────

export interface ProjectSide {
  active: boolean;
  priority_rank: number;
  target_launch_date: string;
  completion: string | null;
  /** Schedule Variance proyek (FR-04.1), hari kerja; + = terlambat; null = tidak dapat diluncurkan. */
  schedule_variance: number | null;
  /** max(Schedule Variance, 0). */
  delay_days: number | null;
  on_target: boolean | null;
  unscheduled_tasks: number;
}

export interface ProjectReplication {
  baseline_completion: SimulationRange;
  scenario_completion: SimulationRange;
  baseline_sv: SimulationRange;
  scenario_sv: SimulationRange;
  baseline_on_time_probability: number | null;
  scenario_on_time_probability: number | null;
}

export interface ProjectResult {
  project_id: string;
  project_name: string;
  targeted: boolean;
  affected: boolean;
  baseline: ProjectSide;
  scenario: ProjectSide;
  completion_shift: number | null;
  task_count: number;
  affected_task_count: number;
  replication: ProjectReplication | null;
}

// ─── Member workload ────────────────────────

export interface MemberDayLoad {
  day: string;
  baseline_load: number | null;
  scenario_load: number | null;
  scenario_capacity: number;
  scenario_absence: string | null;
  scenario_tasks: string[];
  /** Beban harian di atas 100% kapasitas (bukan Overload Flag). */
  baseline_over_capacity: boolean;
  over_capacity: boolean;
}

export interface PeriodReplication {
  /** Resource Load Index minggu ini di antara replikasi. */
  baseline: SimulationRange;
  scenario: SimulationRange;
  /** Replikasi dengan RLI di atas threshold Overload Flag. */
  baseline_overloaded_runs: number;
  scenario_overloaded_runs: number;
}

export interface MemberPeriod {
  period_start: string;
  period_end: string;
  /** Resource Load Index minggu ini (FR-04.3); null bila tidak bekerja. */
  baseline_rli: number | null;
  scenario_rli: number | null;
  /** RLI di atas threshold (Overload Flag, FR-04.4). */
  baseline_overloaded: boolean;
  overloaded: boolean;
  /** Rata-rata utilisasi kapasitas harian. */
  baseline_load: number | null;
  scenario_load: number | null;
  baseline_peak: number | null;
  scenario_peak: number | null;
  baseline_over_capacity_days: number;
  scenario_over_capacity_days: number;
  replication: PeriodReplication | null;
}

export interface MemberResult {
  resource_id: string;
  resource_name: string;
  primary_role_id: string;
  added: boolean;
  targeted: boolean;
  affected: boolean;
  baseline_peak_rli: number | null;
  scenario_peak_rli: number | null;
  baseline_overloaded_weeks: number;
  scenario_overloaded_weeks: number;
  baseline_soi: number;
  scenario_soi: number;
  baseline_peak_load: number | null;
  scenario_peak_load: number | null;
  baseline_over_capacity_days: number;
  scenario_over_capacity_days: number;
  periods: MemberPeriod[];
  days: MemberDayLoad[];
}

// ─── Warnings and summary ───────────────────

export interface SimulationWarning {
  run: 'baseline' | 'scenario';
  code: string;
  task_id: string | null;
  project_id: string | null;
  message: string;
}

export interface ResultSummary {
  affected_projects: number;
  affected_tasks: number;
  affected_members: number;
  unscheduled_tasks: number;
  overloaded_members: number;
}

// ─── Complete simulation result ─────────────

export interface SimulationResult {
  run_id: number | null;
  mode: RunMode;
  saved: boolean;
  is_stale: boolean;
  scenario: ScenarioSummary;
  parameters: Record<string, unknown>;
  snapshot_id: number;
  data_source: DataSource;
  start_date: string;
  end_date: string;
  replication: ReplicationInfo | null;
  duration_ms: number;
  created_at: string | null;
  created_by: string | null;
  summary: ResultSummary;
  kpi_config: KpiConfig;
  kpi_summary: KpiStatusCount;
  kpis: KpiResult[];
  projects: ProjectResult[];
  tasks: TaskResult[];
  members: MemberResult[];
  warnings: SimulationWarning[];
  notes: string[];
}

// ─── Drill-down responses ───────────────────

export interface ProjectDrilldown {
  run_id: number;
  is_stale: boolean;
  project: ProjectResult;
  tasks: TaskResult[];
}

export interface MemberDrilldown {
  run_id: number;
  is_stale: boolean;
  member: MemberResult;
  tasks: TaskResult[];
}
