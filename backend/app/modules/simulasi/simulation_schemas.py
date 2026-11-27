"""Skema API menjalankan simulasi (UC-03, SCRUM-28) dan melihat hasilnya (UC-04, SCRUM-29).

KPI hasil simulasi memakai skema dan rumus KPI evaluasi (`KpiEvaluationItem`, FR-04.1 s.d.
FR-04.10) dengan konfigurasi bawaan, sehingga sama dengan hasil modul Evaluasi.
"""

from datetime import date, datetime
from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.modules.evaluasi.kpi_definitions import EvaluationConfig
from app.modules.evaluasi.schemas import KpiEvaluationItem, StatusCount
from app.modules.simulasi.scenario_schemas import ParameterIssue, ScenarioSummary
from app.modules.simulasi.sim_replication import DEFAULT_RUNS, DEFAULT_SEED, MAX_RUNS, MIN_RUNS
from app.modules.simulasi.sim_scheduler import TaskState, WarningCode
from app.modules.simulasi.snapshot_schemas import DataSource, IsoDate

Value = date | float | None


class RunMode(StrEnum):
    RUN_ONLY = "run_only"
    RUN_AND_SAVE = "run_and_save"


class ReplicationOptions(BaseModel):
    """Mode replikasi Monte Carlo (SCRUM-27)."""

    model_config = ConfigDict(extra="forbid")

    runs: int = Field(
        default=DEFAULT_RUNS, ge=MIN_RUNS, le=MAX_RUNS, description="Jumlah replikasi"
    )
    seed: int = Field(
        default=DEFAULT_SEED,
        ge=0,
        le=2**31 - 1,
        description="Benih generator acak; benih yang sama memberi hasil yang sama",
    )


class SimulationRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: RunMode = Field(
        default=RunMode.RUN_ONLY,
        description="run_only: hasil hanya dikembalikan. run_and_save: skenario divalidasi dan "
        "disimpan lebih dulu, lalu hasilnya ikut disimpan untuk UC-04",
    )
    parameters: dict[str, Any] | None = Field(
        default=None,
        description="Isian formulir yang belum disimpan; kosong berarti parameter tersimpan",
    )
    replication: ReplicationOptions | None = Field(
        default=None, description="Isi (boleh {}) untuk menjalankan replikasi Monte Carlo"
    )
    start_date: IsoDate | None = Field(
        default=None,
        description="Tanggal mulai simulasi; kosong berarti hari kerja pertama setelah "
        "realisasi terakhir di baseline snapshot",
    )


class Range(BaseModel):
    """Median dan rentang suatu nilai di antara replikasi."""

    median: Value
    p10: Value
    p90: Value
    min: Value
    max: Value
    runs: int = Field(description="Jumlah replikasi yang punya nilai")
    missing: int = Field(0, description="Jumlah replikasi tanpa nilai (misalnya tidak terjadwal)")


class TaskSchedule(BaseModel):
    state: TaskState
    start: date | None
    finish: date | None
    owner: str | None
    effort: float | None = Field(description="Effort penuh yang dipakai (hari kerja)")
    dependency_ready: date | None
    reason: str | None = Field(description="Alasan task tidak terjadwal")


class TaskReplication(BaseModel):
    baseline_start: Range
    baseline_finish: Range
    scenario_start: Range
    scenario_finish: Range
    baseline_unscheduled_runs: int
    scenario_unscheduled_runs: int


class TaskResult(BaseModel):
    task_id: str
    project_id: str
    task_name: str
    required_role_id: str
    complexity_id: str | None
    added: bool = Field(description="Task baru dari Change Request")
    targeted: bool = Field(description="Langsung disentuh parameter skenario")
    affected: bool = Field(description="Disentuh skenario, atau status/mulai/selesainya berubah")
    owner_changed: bool = Field(description="Dikerjakan anggota lain daripada di baseline")
    baseline: TaskSchedule | None
    scenario: TaskSchedule | None
    start_shift: int | None = Field(description="Pergeseran mulai (hari kerja, + = mundur)")
    finish_shift: int | None = Field(description="Pergeseran selesai (hari kerja, + = mundur)")
    replication: TaskReplication | None = None


class ProjectSide(BaseModel):
    active: bool
    priority_rank: int
    target_launch_date: date
    completion: date | None
    schedule_variance: int | None = Field(
        None,
        description="Schedule Variance proyek (FR-04.1): hari kerja dari target rilis ke "
        "proyeksi selesai, + = terlambat; kosong bila tidak dapat diluncurkan",
    )
    delay_days: int | None = Field(
        description="Keterlambatan setelah target rilis (hari kerja) = max(Schedule Variance, 0)"
    )
    on_target: bool | None = Field(description="Diluncurkan tepat waktu (Schedule Variance <= 0)")
    unscheduled_tasks: int


class ProjectReplication(BaseModel):
    baseline_completion: Range
    scenario_completion: Range
    baseline_sv: Range = Field(description="Schedule Variance proyek di antara replikasi")
    scenario_sv: Range
    baseline_on_time_probability: float | None = Field(
        description="On-Time Launch Probability proyek (FR-04.2)"
    )
    scenario_on_time_probability: float | None


class ProjectResult(BaseModel):
    project_id: str
    project_name: str
    targeted: bool
    affected: bool
    baseline: ProjectSide
    scenario: ProjectSide
    completion_shift: int | None = Field(description="Hari kerja, + = selesai lebih lambat")
    task_count: int
    affected_task_count: int
    replication: ProjectReplication | None = None


class MemberDayLoad(BaseModel):
    day: date
    baseline_load: float | None = Field(description="Beban harian (1.0 = 100% kapasitas)")
    scenario_load: float | None
    scenario_capacity: float = Field(description="Kapasitas skenario hari itu (satuan beban)")
    scenario_absence: str | None
    scenario_tasks: list[str]
    baseline_over_capacity: bool
    over_capacity: bool = Field(
        description="Beban harian skenario di atas 100% kapasitas (bukan Overload Flag, yang "
        "memakai Resource Load Index mingguan)"
    )


class PeriodReplication(BaseModel):
    baseline: Range = Field(description="Resource Load Index minggu ini di antara replikasi")
    scenario: Range
    baseline_overloaded_runs: int = Field(description="Replikasi dengan RLI di atas threshold")
    scenario_overloaded_runs: int


class MemberPeriod(BaseModel):
    """Beban satu minggu (Senin sampai Minggu)."""

    period_start: date
    period_end: date
    baseline_rli: float | None = Field(
        description="Resource Load Index minggu ini (FR-04.3); kosong bila tidak bekerja"
    )
    scenario_rli: float | None
    baseline_overloaded: bool = Field(description="RLI baseline di atas threshold (FR-04.4)")
    overloaded: bool = Field(description="RLI skenario di atas threshold (FR-04.4)")
    baseline_load: float | None = Field(
        description="Rata-rata utilisasi kapasitas harian pada hari kerja"
    )
    scenario_load: float | None
    baseline_peak: float | None
    scenario_peak: float | None
    baseline_over_capacity_days: int = Field(description="Hari beban di atas 100% kapasitas")
    scenario_over_capacity_days: int
    replication: PeriodReplication | None = None


class MemberResult(BaseModel):
    resource_id: str
    resource_name: str
    primary_role_id: str
    added: bool = Field(description="Anggota baru dari skenario New Member")
    targeted: bool
    affected: bool
    baseline_peak_rli: float | None = Field(description="RLI mingguan tertinggi (FR-04.3)")
    scenario_peak_rli: float | None
    baseline_overloaded_weeks: int = Field(description="Minggu dengan Overload Flag (FR-04.4)")
    scenario_overloaded_weeks: int
    baseline_soi: float = Field(description="Sustained Overload Indicator anggota (FR-04.6)")
    scenario_soi: float
    baseline_peak_load: float | None = Field(description="Utilisasi kapasitas harian tertinggi")
    scenario_peak_load: float | None
    baseline_over_capacity_days: int
    scenario_over_capacity_days: int
    periods: list[MemberPeriod]
    days: list[MemberDayLoad]


class SimulationWarning(BaseModel):
    run: Literal["baseline", "scenario"]
    code: WarningCode
    task_id: str | None
    project_id: str | None
    message: str


class ReplicationInfo(BaseModel):
    runs: int
    seed: int


class ResultSummary(BaseModel):
    affected_projects: int
    affected_tasks: int
    affected_members: int
    unscheduled_tasks: int = Field(description="Task tidak terjadwal di skenario")
    overloaded_members: int = Field(
        description="Anggota dengan Overload Flag (RLI mingguan di atas threshold) di skenario"
    )


class SimulationResult(BaseModel):
    """Hasil simulasi baseline dan skenario (layar Simulation result, Tabel 4.15)."""

    run_id: int | None = Field(description="ID hasil tersimpan; kosong untuk run_only")
    mode: RunMode
    saved: bool = Field(description="Hasil ini tersimpan dan dapat dibuka lagi (UC-04)")
    is_stale: bool = Field(
        False, description="Parameter skenario sudah berubah sejak hasil ini disimpan"
    )
    scenario: ScenarioSummary
    parameters: dict[str, Any] = Field(description="Parameter yang disimulasikan")
    snapshot_id: int
    data_source: DataSource
    start_date: date
    end_date: date
    replication: ReplicationInfo | None
    duration_ms: int
    created_at: datetime | None
    created_by: str | None
    summary: ResultSummary
    kpi_config: EvaluationConfig = Field(
        description="Parameter rumus KPI yang dipakai (FR-04.11, bawaan modul Evaluasi)"
    )
    kpi_summary: StatusCount = Field(description="Jumlah KPI better, same, dan worse")
    kpis: list[KpiEvaluationItem] = Field(description="KPI FR-04.1 s.d. FR-04.10")
    projects: list[ProjectResult]
    tasks: list[TaskResult]
    members: list[MemberResult]
    warnings: list[SimulationWarning]
    notes: list[str] = Field(description="Asumsi dan perubahan yang diterapkan aturan skenario")
    dss_sweep_id: int | None = Field(
        None,
        description="Sweep DSS yang dimulai setelah run and save (SCRUM-69); statusnya di "
        "GET /dss/scenarios/{scenario_id}/sweep. Kosong untuk run_only dan hasil yang dibuka lagi",
    )


class ProjectDrilldown(BaseModel):
    """Pergeseran mulai dan selesai setiap task satu proyek (UC-04)."""

    run_id: int
    is_stale: bool
    project: ProjectResult
    tasks: list[TaskResult]


class MemberDrilldown(BaseModel):
    """Beban harian satu anggota dengan periode berbeban berlebih (UC-04)."""

    run_id: int
    is_stale: bool
    member: MemberResult
    tasks: list[TaskResult]


class LatestRun(BaseModel):
    run_id: int
    created_at: datetime
    created_by: str | None
    start_date: date
    replication: ReplicationInfo | None
    is_stale: bool


class RunPreview(BaseModel):
    """Yang ditampilkan sebelum simulasi dijalankan (UC-03 langkah 1)."""

    scenario: ScenarioSummary
    parameters: dict[str, Any]
    parameter_errors: list[ParameterIssue]
    can_run: bool = Field(description="Parameter tersimpan lengkap dan valid")
    snapshot_id: int
    data_source: DataSource
    default_start_date: date
    default_replications: int = DEFAULT_RUNS
    latest_run: LatestRun | None
