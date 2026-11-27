"""Skema API DSS per skenario: layar Rekomendasi › Per skenario (SCRUM-70)."""

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.modules.dss.models import SweepStatus
from app.modules.evaluasi.schemas import KpiEvaluationItem
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_schemas import ScenarioSummary
from app.modules.simulasi.snapshot_schemas import DataSource

CRITERIA_DESCRIPTION = (
    "Nilai 0..1 per kriteria: role_match, available_capacity, current_work, kpi_effect; "
    "kosong berarti tidak berlaku (kandidat tanpa anggota)"
)


class SweepSummary(BaseModel):
    """Status dan kemajuan satu sweep."""

    model_config = ConfigDict(from_attributes=True)

    sweep_id: int
    run_id: int = Field(description="Simulasi tersimpan yang menjadi dasar sweep")
    situation: Situation
    lever: str = Field(description="Keputusan yang divariasikan, misalnya mentor_resource_id")
    lever_label: str
    status: SweepStatus = Field(
        description="pending, running, done, failed (lihat `error`), atau cancelled (digantikan "
        "sweep yang lebih baru)"
    )
    total_candidates: int = Field(description="Kandidat yang disimulasikan, termasuk pilihan Anda")
    completed_candidates: int
    error: str | None
    created_by: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None


class DssScenario(BaseModel):
    """Satu skenario yang sudah disimulasikan dan disimpan (pilihan "Pilih skenario")."""

    scenario: ScenarioSummary
    run_id: int = Field(description="Simulasi tersimpan terakhir")
    run_created_at: datetime
    is_stale: bool = Field(
        description="Parameter skenario berubah sejak simulasi terakhir, atau sweep terakhir "
        "berasal dari simulasi yang lebih lama"
    )
    sweep: SweepSummary | None = Field(description="Sweep terakhir; kosong bila belum ada")


class CandidateSummary(BaseModel):
    candidate_id: int
    position: int = Field(description="Urutan kandidat; pilihan Anda di posisi 0")
    rank: int | None = Field(description="Peringkat; kosong untuk kandidat dikecualikan/gagal")
    status: str = Field(description="done, failed, atau excluded")
    value: str | int | None = Field(description="Nilai lever: ID anggota, peringkat, atau kosong")
    label: str
    is_current: bool = Field(description="Pilihan Anda sendiri (skenario yang disimulasikan)")
    score: float | None = Field(description="Skor total 0..1")
    criteria: dict[str, float | None] = Field(description=CRITERIA_DESCRIPTION)
    reason: str
    error: str | None = Field(description="Alasan dikecualikan atau pesan kegagalan")
    replicated: bool = Field(description="Mendapat replikasi Monte Carlo (tiga teratas)")


class SweepRead(SweepSummary):
    """Isi layar Rekomendasi per skenario: kandidat terurut menurut peringkat."""

    scenario: ScenarioSummary
    snapshot_id: int
    data_source: DataSource = Field(description="Asal data baseline: synthetic atau puti")
    is_stale: bool = Field(
        description="Parameter skenario berubah sejak simulasi yang dipakai sweep ini"
    )
    weights: dict[str, float] = Field(description="Bobot kriteria yang dipakai penilaian")
    truncated: bool = Field(description="Sebagian kandidat tidak disimulasikan karena batas")
    omitted_candidates: int
    no_lever: str | None = Field(description="Alasan tidak ada nilai lever lain untuk dicoba")
    no_action_needed: bool | None = Field(
        description="Skenario Anda tidak memperburuk keterlambatan, beban berlebih, maupun target "
        "rilis dibandingkan baseline"
    )
    no_suitable_candidate: bool | None = Field(
        description="Tidak ada kandidat yang skornya lebih baik dari pilihan Anda"
    )
    current_rank: int | None = Field(description="Peringkat pilihan Anda")
    ranked_candidates: int = Field(description="Jumlah kandidat yang dinilai")
    candidates: list[CandidateSummary] = Field(
        description="Kandidat yang dinilai menurut peringkat, lalu yang dikecualikan atau gagal"
    )


class CandidateRead(CandidateSummary):
    """Rincian satu kandidat: nilai kriteria beserta faktanya dan perbandingan KPI lengkap."""

    sweep_id: int
    parameters: dict[str, Any] = Field(description="Parameter skenario yang disimulasikan")
    actions: list[dict[str, Any]] = Field(description="Respons di atas skenario (pengalihan task)")
    criteria_facts: dict[str, dict[str, Any]] = Field(
        description="Fakta mentah setiap kriteria, dasar kalimat alasan"
    )
    vs_baseline: list[KpiEvaluationItem] = Field(description="KPI kandidat terhadap baseline")
    vs_current: list[KpiEvaluationItem] = Field(description="KPI kandidat terhadap pilihan Anda")
    replication: dict[str, int] | None = Field(description="runs dan seed replikasi Monte Carlo")
    duration_ms: int
