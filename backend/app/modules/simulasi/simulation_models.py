"""Hasil simulasi tersimpan (SCRUM-28 "Run and save", SCRUM-29), di schema `twinner`.

Satu baris per simulasi yang dijalankan dengan mode `run_and_save`. Parameter yang disimulasikan
disalin ke baris ini, sehingga hasil tetap dapat dijelaskan walaupun skenarionya diubah kemudian
(hasil seperti itu ditandai `is_stale`). Nilai KPI disimpan terpisah dari hasil lengkap supaya
perbandingan skenario (UC-06) tidak perlu membaca seluruh hasil.

DATA_CONTRACT bagian 4: tabel ini tidak punya foreign key ke schema `public`.
"""

from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Identity,
    Integer,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.simulasi.snapshot_models import SCHEMA


class SimulationRun(Base):
    __tablename__ = "simulation_run"
    __table_args__ = (
        CheckConstraint("jsonb_typeof(parameters) = 'object'", name="parameters_is_object"),
        CheckConstraint("jsonb_typeof(kpis) = 'array'", name="kpis_is_array"),
        CheckConstraint("jsonb_typeof(result) = 'object'", name="result_is_object"),
        CheckConstraint("replications IS NULL OR replications > 0", name="replications_positive"),
        CheckConstraint("warning_count >= 0", name="warning_count_min"),
        CheckConstraint("duration_ms >= 0", name="duration_ms_min"),
        {"schema": SCHEMA},
    )

    run_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    # Hasil ikut terhapus bersama skenarionya.
    scenario_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.scenario.scenario_id", ondelete="CASCADE")
    )
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.baseline_snapshot.snapshot_id", ondelete="RESTRICT")
    )
    situation: Mapped[str] = mapped_column(String(40))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB)
    start_date: Mapped[date] = mapped_column(Date)
    # Jumlah replikasi Monte Carlo; NULL untuk simulasi deterministik saja.
    replications: Mapped[int | None] = mapped_column(Integer)
    seed: Mapped[int | None] = mapped_column(BigInteger)
    # Daftar KPI (baseline, skenario, selisih, status) per simulasi (SCRUM-32).
    kpis: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
    # SimulationResult lengkap untuk layar hasil simulasi (UC-04).
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    warning_count: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    # Username, bukan foreign key ke app_user: tabel itu milik J&C (DATA_CONTRACT 7.2).
    created_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
