"""Hasil evaluasi KPI tersimpan per simulasi (SCRUM-32), di schema `twinner`.

Satu baris per simulasi tersimpan (`twinner.simulation_run`) dan konfigurasi KPI. Konfigurasi
(bobot RLI, threshold, toleransi) disimpan lengkap beserta hash-nya, sehingga evaluasi ulang
dengan konfigurasi yang sama memakai hasil tersimpan dan konfigurasi lain menjadi baris baru.
Hasil ikut terhapus bersama simulasinya.

DATA_CONTRACT bagian 4: tabel ini tidak punya foreign key ke schema `public`.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.simulasi.snapshot_models import SCHEMA


class KpiEvaluationRow(Base):
    __tablename__ = "kpi_evaluation"
    __table_args__ = (
        UniqueConstraint("run_id", "config_hash"),
        CheckConstraint("jsonb_typeof(config) = 'object'", name="config_is_object"),
        CheckConstraint("jsonb_typeof(kpis) = 'array'", name="kpis_is_array"),
        CheckConstraint("jsonb_typeof(result) = 'object'", name="result_is_object"),
        CheckConstraint("duration_ms >= 0", name="duration_ms_min"),
        {"schema": SCHEMA},
    )

    evaluation_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    run_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.simulation_run.run_id", ondelete="CASCADE")
    )
    # sha256 dari konfigurasi lengkap (EvaluationConfig.resolved) dalam JSON berurutan.
    config_hash: Mapped[str] = mapped_column(String(64))
    config: Mapped[dict[str, Any]] = mapped_column(JSONB)
    # Daftar KPI (nilai baseline, skenario, selisih, status, median dan rentang).
    kpis: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # KpiEvaluation lengkap, termasuk rincian per proyek, anggota, dan task.
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    duration_ms: Mapped[int] = mapped_column(Integer)
    # Username, bukan foreign key ke app_user: tabel itu milik J&C (DATA_CONTRACT 7.2).
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
