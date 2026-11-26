
export type DataSource = 'synthetic' | 'puti';

export type Situation =
  | 'change_request'
  | 'member_absence'
  | 'new_member'
  | 'member_rotation'
  | 'task_overrun'
  | 'task_blocker'
  | 'priority_change'
  | 'critical_bug';

export type ScenarioStatus = 'draft' | 'saved';

export type Parameters = Record<string, unknown>;

// JSON Schema returned by the Scenario Manager API.
export type ParameterSchema = {
  title?: string;
  description?: string;
  type?: string;
  format?: string;
  properties?: Record<string, ParameterSchema>;
  required?: string[];
  items?: ParameterSchema;
  enum?: Array<string | number>;
  default?: unknown;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  minLength?: number;
  maxLength?: number;
  maxItems?: number;
  pattern?: string;
  anyOf?: ParameterSchema[];
  allOf?: ParameterSchema[];
  $ref?: string;
  $defs?: Record<string, ParameterSchema>;
};

export type SituationInfo = {
  situation: Situation;
  label: string;
  description: string;
  parameters_schema: ParameterSchema;
};

// Scenario Manager
export type ParameterIssue = {
  loc: Array<string | number>;
  msg: string;
  type: string;
};

export type ScenarioSummary = {
  scenario_id: number;
  name: string;
  situation: Situation;
  status: ScenarioStatus;
  snapshot_id: number;
  data_source: DataSource;
  duplicated_from: number | null;
  created_by: string | null;
  updated_by: string | null;
  created_at: string;
  updated_at: string;
  saved_at: string | null;
};

export type Scenario = ScenarioSummary & {
  parameters: Parameters;
  parameter_errors: ParameterIssue[];
};

export type ScenarioCreate = {
  name: string;
  situation: Situation;
  snapshot_id?: number | null;
  parameters?: Parameters;
};

export type ScenarioUpdate = {
  name?: string;
  parameters?: Parameters;
};

export type ScenarioDuplicate = {
  name?: string;
};

// Baseline snapshot
export type SnapshotSummary = {
  snapshot_id: number;
  data_source: DataSource;
  label: string | null;
  imported_by: string | null;
  created_at: string;
  row_counts: Record<string, number>;
  checksum: string;
  is_current: boolean;
};

export type BaselineProject = {
  project_id: string;
  project_name: string;
  process_owner: string;
  priority_rank: number;
  start_date: string;
  target_launch_date: string;
  actual_launch_date: string | null;
  status: string;
  dor_fulfilled: boolean;
};

export type BaselineResource = {
  resource_id: string;
  resource_name: string;
  primary_role_id: string;
  secondary_role_ids: string | null;
  cross_role_capable: boolean;
  join_date: string;
  seniority: string;
};

export type BaselineRole = {
  role_id: string;
  role_name: string;
  capacity_reference: number;
  description: string | null;
};

export type BaselineComplexity = {
  complexity_id: string;
  complexity_name: string;
  min_days: number;
  max_days: number;
  load_weight: number;
};

export type BaselineTask = {
  task_id: string;
  project_id: string;
  feature_name: string;
  task_name: string;
  required_role_id: string;
  complexity_id: string;
  priority_rank: number;
  estimated_days: number;
  phase: string;
  planned_start: string;
  planned_finish: string;
  actual_start: string | null;
  actual_finish: string | null;
  status: string;
  qa_reject_count: number;
  dod_fulfilled: boolean;
};

export type BaselineAssignment = {
  assignment_id: string;
  task_id: string;
  resource_id: string;
  role_in_task: string;
  assigned_date: string;
  released_date: string | null;
  is_takeover: boolean;
};

export type BaselineDependency = {
  dependency_id: string;
  task_id: string;
  depends_on_task_id: string;
  dependency_type: string;
  propagation_factor: number;
};

export type BaselineAvailability = {
  availability_id: string;
  resource_id: string;
  date_start: string;
  date_end: string;
  absence_type: string;
  is_known_at_planning: boolean;
};

export type BaselineWorkload = {
  item_id: string;
  resource_id: string;
  item_type: string;
  related_project_id: string | null;
  received_date: string;
  started_date: string | null;
  resolved_date: string | null;
  severity: string | null;
  is_hotfix: boolean;
};

export type BaselineChangeEvent = {
  event_id: string;
  project_id: string;
  event_type: string;
  event_date: string;
  requested_by: string | null;
  decision: string | null;
  resolution_mode: string | null;
  affected_task_id: string | null;
  extension_days: number | null;
};

export type BaselineHoliday = {
  holiday_date: string;
  holiday_name: string;
};

export type Baseline = SnapshotSummary & {
  projects: BaselineProject[];
  resources: BaselineResource[];
  roles: BaselineRole[];
  complexity_levels: BaselineComplexity[];
  tasks: BaselineTask[];
  assignments: BaselineAssignment[];
  task_dependencies: BaselineDependency[];
  availability: BaselineAvailability[];
  workload_items: BaselineWorkload[];
  change_events: BaselineChangeEvent[];
  holidays: BaselineHoliday[];
};

// Parameter objects used by scenario forms.
export type DateRange = {
  start: string;
  end: string;
};

export type NewTask = {
  task_name: string;
  required_role_id: string;
  estimated_days: number;
};

export type ChangeRequestParams = {
  project_id: string;
  added_tasks: NewTask[];
  removed_task_ids: string[];
  affected_period: DateRange;
  target_date: string;
};

export type MemberAbsenceParams = {
  resource_id: string;
  absence_type:
    | 'planned_leave'
    | 'sudden_leave'
    | 'maternity_leave';
  absence_period: DateRange;
  affected_task_ids: string[];
};

export type NewMemberParams = {
  role_id: string;
  seniority: string;
  join_date: string;
  onboarding_weeks: number;
  initial_capacity: number;
  mentor_resource_id?: string | null;
  mentor_share?: number | null;
};

export type MemberRotationParams = {
  resource_id: string;
  origin_project_id: string;
  destination_project_id?: string | null;
  transfer_date: string;
  existing_assignment_ids: string[];
};

export type TaskOverrunParams = {
  task_id: string;
  original_days: number;
  revised_days: number;
  remaining_days: number;
};

export type TaskBlockerParams = {
  task_id: string;
  blocking_task_id?: string | null;
  blocking_days: number;
  independent_task_ids: string[];
};

export type PriorityChangeParams = {
  project_id: string;
  new_priority_rank: number;
  affected_task_ids: string[];
  estimated_days?: number | null;
  target_date: string;
};

export type CriticalBugParams = {
  severity: string;
  fixing_days: number;
  resource_id: string;
  affected_task_ids: string[];
};

export type ScenarioParameterMap = {
  change_request: ChangeRequestParams;
  member_absence: MemberAbsenceParams;
  new_member: NewMemberParams;
  member_rotation: MemberRotationParams;
  task_overrun: TaskOverrunParams;
  task_blocker: TaskBlockerParams;
  priority_change: PriorityChangeParams;
  critical_bug: CriticalBugParams;
};

export type ScenarioParameters =
  ScenarioParameterMap[Situation];
