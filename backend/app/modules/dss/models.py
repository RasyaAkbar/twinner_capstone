"""Hasil sweep DSS per skenario (SCRUM-68), di schema `twinner`.

`dss_sweep`: satu baris per sweep, yaitu satu kali DSS mencoba semua nilai lever untuk satu
simulasi tersimpan (`simulation_run`). Statusnya:

- `pending`: dibuat setelah Run and save dan menunggu job latar belakang (SCRUM-69).
- `running`: sedang disimulasikan; `completed_candidates` dari `total_candidates` sudah selesai
  dan `updated_at` diperbarui setiap kandidat.
- `done`: kandidat dan peringkatnya tersimpan di `dss_sweep_candidate`.
- `failed`: gagal; pesannya di `error`. Dapat dijalankan ulang.
- `cancelled`: digantikan sweep yang lebih baru untuk skenario yang sama.

`dss_sweep_candidate`: satu baris per kandidat, urutannya `position` (pilihan pengguna di posisi
0). Kandidat yang dinilai punya `rank` dan `score`; kandidat yang dikecualikan atau gagal hanya
membawa `error`. KPI kandidat terhadap baseline (`vs_baseline`) dan terhadap pilihan pengguna
(`vs_current`) disimpan dalam bentuk `KpiEvaluationItem` modul Evaluasi.

Hanya sweep terbaru per skenario yang dibaca API; sweep lama tetap sebagai riwayat. Sweep ikut
terhapus bersama skenario atau simulasinya. DATA_CONTRACT bagian 4: tabel ini tidak punya
foreign key ke schema `public`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Identity,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.simulasi.snapshot_models import SCHEMA


class SweepStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


# Sama dengan `sweep_runner.CandidateStatus`.
CANDIDATE_STATUSES = ("done", "failed", "excluded")


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class DssSweep(Base):
    __tablename__ = "dss_sweep"
    __table_args__ = (
        CheckConstraint(_one_of("status", tuple(SweepStatus)), name="status_valid"),
        CheckConstraint("total_candidates >= 0", name="total_candidates_min"),
        CheckConstraint(
            "completed_candidates >= 0 AND completed_candidates <= total_candidates",
            name="completed_candidates_range",
        ),
        CheckConstraint("omitted_candidates >= 0", name="omitted_candidates_min"),
        CheckConstraint("jsonb_typeof(weights) = 'object'", name="weights_is_object"),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_ms_min"),
        {"schema": SCHEMA},
    )

    sweep_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.scenario.scenario_id", ondelete="CASCADE")
    )
    run_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.simulation_run.run_id", ondelete="CASCADE")
    )
    situation: Mapped[str] = mapped_column(String(40))
    lever: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), server_default=SweepStatus.PENDING.value)
    # Kandidat yang disimulasikan (termasuk pilihan pengguna) dan yang sudah selesai.
    total_candidates: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    completed_candidates: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # Kandidat layak yang tidak disimulasikan karena batas DSS_MAX_CANDIDATES (truncated > 0).
    omitted_candidates: Mapped[int] = mapped_column(Integer, server_default=text("0"))
    # Alasan skenario tidak punya nilai lever lain untuk dicoba.
    no_lever: Mapped[str | None] = mapped_column(Text)
    no_action_needed: Mapped[bool | None] = mapped_column(Boolean)
    no_suitable_candidate: Mapped[bool | None] = mapped_column(Boolean)
    # Bobot kriteria penilaian yang dipakai (`ScoringWeights.as_json`).
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB)
    error: Mapped[str | None] = mapped_column(Text)
    # Username, bukan foreign key ke app_user: tabel itu milik J&C (DATA_CONTRACT 7.2).
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)


class DssSweepCandidate(Base):
    __tablename__ = "dss_sweep_candidate"
    __table_args__ = (
        UniqueConstraint("sweep_id", "position"),
        CheckConstraint(_one_of("status", CANDIDATE_STATUSES), name="status_valid"),
        CheckConstraint("position >= 0", name="position_min"),
        CheckConstraint("rank IS NULL OR rank >= 1", name="rank_min"),
        CheckConstraint("score IS NULL OR (score >= 0 AND score <= 1)", name="score_range"),
        CheckConstraint(
            "value IS NULL OR jsonb_typeof(value) IN ('string', 'number', 'null')",
            name="value_is_scalar",
        ),
        CheckConstraint("jsonb_typeof(parameters) = 'object'", name="parameters_is_object"),
        CheckConstraint("jsonb_typeof(actions) = 'array'", name="actions_is_array"),
        CheckConstraint("jsonb_typeof(criteria) = 'object'", name="criteria_is_object"),
        CheckConstraint("jsonb_typeof(vs_baseline) = 'array'", name="vs_baseline_is_array"),
        CheckConstraint("jsonb_typeof(vs_current) = 'array'", name="vs_current_is_array"),
        CheckConstraint(
            "replication IS NULL OR jsonb_typeof(replication) = 'object'",
            name="replication_is_object",
        ),
        CheckConstraint("duration_ms >= 0", name="duration_ms_min"),
        {"schema": SCHEMA},
    )

    candidate_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    sweep_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.dss_sweep.sweep_id", ondelete="CASCADE")
    )
    # Urutan dari `build_candidates`: pilihan pengguna, kandidat layak, lalu yang dikecualikan.
    position: Mapped[int] = mapped_column(Integer)
    rank: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    # Nilai lever: ID anggota, peringkat prioritas, atau null (tanpa mentor, tanpa pengalihan).
    value: Mapped[Any] = mapped_column(JSONB(none_as_null=True), nullable=True)
    label: Mapped[str] = mapped_column(String(300))
    is_current: Mapped[bool] = mapped_column(Boolean)
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    actions: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # Alasan dikecualikan atau pesan kegagalan.
    error: Mapped[str | None] = mapped_column(Text)
    score: Mapped[float | None] = mapped_column(Float)
    # {kriteria: {"score": 0..1 atau null, "facts": {...}}}
    criteria: Mapped[dict[str, Any]] = mapped_column(JSONB)
    vs_baseline: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    vs_current: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text)
    # {"runs": n, "seed": s} bila kandidat ini mendapat replikasi Monte Carlo.
    # `none_as_null`: None disimpan sebagai NULL SQL, bukan JSON null (lihat CHECK di atas).
    replication: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    duration_ms: Mapped[int] = mapped_column(Integer)
