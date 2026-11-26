"""Skema API evaluasi KPI (SCRUM-32): hasil KPI per simulasi dan perbandingan skenario."""

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.modules.evaluasi.kpi_definitions import (
    KPI_BY_KEY,
    Direction,
    EvaluationConfig,
    KpiStatus,
    KpiUnit,
    Missing,
)

Value = float | None
MAX_COMPARED = 10


class KpiDefinitionOut(BaseModel):
    key: str
    fr: str
    label: str
    unit: KpiUnit
    direction: Direction
    target: float | None
    tolerance: float = Field(description="Toleransi bawaan status `same`")
    missing: Missing = Field(description="Arti nilai kosong saat dibandingkan")
    definition: str
    formula: str


class KpiCatalog(BaseModel):
    kpis: list[KpiDefinitionOut]
    default_config: EvaluationConfig


class KpiRange(BaseModel):
    """Median dan rentang suatu nilai di antara replikasi."""

    median: Value
    p10: Value
    p90: Value
    min: Value
    max: Value
    runs: int = Field(description="Jumlah replikasi yang punya nilai")
    missing: int = Field(0, description="Jumlah replikasi tanpa nilai")


class KpiPoint(BaseModel):
    baseline: Value
    scenario: Value
    delta: Value
    status: KpiStatus


class KpiReplicationStats(BaseModel):
    runs: int
    baseline: KpiRange | None
    scenario: KpiRange | None
    delta: KpiRange | None = Field(description="Selisih skenario - baseline per replikasi")


class KpiEvaluationItem(BaseModel):
    key: str
    fr: str
    label: str
    unit: KpiUnit
    direction: Direction
    target: float | None
    tolerance: float = Field(description="Toleransi yang dipakai untuk status")
    definition: str
    formula: str
    basis: Literal["deterministic", "median"] = Field(
        description="Asal nilai utama: simulasi deterministik, atau median replikasi "
        "(proporsi replikasi untuk On-Time Launch Probability)"
    )
    baseline: Value
    scenario: Value
    delta: Value = Field(description="Skenario - baseline")
    status: KpiStatus
    deterministic: KpiPoint = Field(description="Nilai simulasi deterministik")
    replication: KpiReplicationStats | None = None


class StatusCount(BaseModel):
    better: int
    same: int
    worse: int


class ProjectKpi(BaseModel):
    project_id: str
    project_name: str
    target_launch_date: date | None
    baseline_sv: int | None = Field(
        description="Hari kerja; + = terlambat; kosong = tidak dapat diluncurkan"
    )
    scenario_sv: int | None
    sv_delta: int | None
    baseline_on_time_probability: float | None
    scenario_on_time_probability: float | None


class WeekCounts(BaseModel):
    tasks: int
    operational: int
    tickets: int
    rli: float
    overloaded: bool


class MemberWeekKpi(BaseModel):
    week_start: date
    baseline: WeekCounts | None
    scenario: WeekCounts | None


class MemberKpi(BaseModel):
    resource_id: str
    resource_name: str
    primary_role_id: str
    baseline_peak_rli: float | None
    scenario_peak_rli: float | None
    baseline_overloaded_weeks: int
    scenario_overloaded_weeks: int
    baseline_soi: float
    scenario_soi: float
    weeks: list[MemberWeekKpi]


class WeekBalance(BaseModel):
    week_start: date
    baseline_wbi: float | None
    scenario_wbi: float | None


class TaskCascade(BaseModel):
    task_id: str
    project_id: str
    baseline_delay: float | None = Field(description="Hari kerja setelah planned_finish")
    scenario_delay: float | None
    baseline_cdi: float
    scenario_cdi: float


class EarGroup(BaseModel):
    group: str
    baseline: float | None
    scenario: float | None


class TimeUse(BaseModel):
    working_days: int
    blocked_days: int
    reallocated_days: int


class KpiDetails(BaseModel):
    """Rincian KPI dari simulasi deterministik (dan peluang tepat waktu dari replikasi)."""

    projects: list[ProjectKpi]
    members: list[MemberKpi]
    balance: list[WeekBalance]
    cascade: list[TaskCascade] = Field(description="Task dengan CDI > 0, terbesar lebih dulu")
    ear_by_complexity: list[EarGroup]
    ear_by_role: list[EarGroup]
    baseline_time: TimeUse
    scenario_time: TimeUse


class ReplicationInfo(BaseModel):
    runs: int
    seed: int


class KpiEvaluation(BaseModel):
    """KPI baseline dan skenario satu simulasi tersimpan, selisih, dan statusnya."""

    evaluation_id: int | None
    run_id: int | None
    scenario_id: int | None
    situation: str
    snapshot_id: int
    start_date: date
    end_date: date
    replication: ReplicationInfo | None
    config: EvaluationConfig = Field(description="Parameter rumus dan toleransi yang dipakai")
    summary: StatusCount
    kpis: list[KpiEvaluationItem]
    details: KpiDetails
    duration_ms: int
    created_at: datetime | None
    created_by: str | None


class EvaluateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    config: EvaluationConfig = Field(default_factory=EvaluationConfig)
    refresh: bool = Field(
        False, description="Hitung ulang walaupun hasil dengan konfigurasi ini sudah tersimpan"
    )


def _weights(value: dict[str, float]) -> dict[str, float]:
    unknown = sorted(set(value) - set(KPI_BY_KEY))
    if unknown:
        raise ValueError(f"KPI tidak dikenal: {', '.join(unknown)}")
    if any(weight < 0 for weight in value.values()):
        raise ValueError("Bobot tidak boleh negatif")
    return value


class CompareRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    run_ids: list[int] | None = Field(
        None, min_length=1, max_length=MAX_COMPARED, description="Simulasi tersimpan"
    )
    scenario_ids: list[int] | None = Field(
        None,
        min_length=1,
        max_length=MAX_COMPARED,
        description="Skenario; dipakai simulasi tersimpan terakhir masing-masing",
    )
    config: EvaluationConfig = Field(default_factory=EvaluationConfig)
    weights: dict[str, float] = Field(
        default_factory=dict,
        description="Bobot KPI untuk memilih skenario terbaik (bawaan 1; 0 = tidak dinilai)",
    )

    _check_weights = field_validator("weights")(_weights)

    @model_validator(mode="after")
    def _one_selection(self) -> "CompareRequest":
        if (self.run_ids is None) == (self.scenario_ids is None):
            raise ValueError("Isi salah satu: run_ids atau scenario_ids")
        ids = self.run_ids if self.run_ids is not None else self.scenario_ids
        if len(set(ids)) != len(ids):
            raise ValueError("ID tidak boleh berulang")
        if any(i < 1 for i in ids):
            raise ValueError("ID harus >= 1")
        return self


class RankedScenario(BaseModel):
    rank: int
    run_id: int
    scenario_id: int
    scenario_name: str
    situation: str
    status_score: float = Field(description="Jumlah bobot x (+1 better, 0 same, -1 worse)")
    magnitude_score: float = Field(
        description="Pemecah seri: jumlah bobot x perbaikan ternormalisasi (0..1)"
    )
    better: int
    same: int
    worse: int
    improves_on_baseline: bool = Field(description="Skor status > 0")
    is_best: bool


class ScenarioComparison(BaseModel):
    config: EvaluationConfig
    weights: dict[str, float] = Field(description="Bobot yang dipakai, semua KPI")
    best_run_id: int
    best_scenario_id: int
    comparable: bool = Field(
        description="Semua simulasi memakai baseline snapshot dan tanggal mulai yang sama"
    )
    warnings: list[str]
    ranking: list[RankedScenario]
    evaluations: list[KpiEvaluation]
