"""Skenario what-if tWinner (SCRUM-22), di schema `twinner` milik tWinner.

Satu baris per skenario: situasi keputusan (Tabel 4.2), parameter situasi itu (Tabel 4.4)
sebagai JSONB, dan baseline snapshot yang menjadi dasarnya. Skenario selalu menunjuk satu
snapshot yang tidak pernah berubah (SCRUM-15), jadi hasil validasi parameternya juga tidak
berubah selama parameternya tidak diubah.

Status:
- `draft`: baru dibuat atau parameternya diubah setelah disimpan. Parameter boleh belum
  lengkap atau belum valid; kesalahannya dilaporkan, tidak ditolak, supaya formulir UC-02
  dapat disimpan sebagai draf.
- `saved`: lolos validasi penuh saat disimpan dan siap disimulasikan. Mengubah parameter
  mengembalikannya ke `draft` sampai disimpan lagi, sehingga skenario `saved` selalu berisi
  parameter yang valid terhadap snapshot-nya.

DATA_CONTRACT bagian 4: tabel ini tidak punya foreign key ke schema `public`.
"""

from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.modules.simulasi.snapshot_models import DATA_SOURCES, SCHEMA


class Situation(StrEnum):
    """Delapan situasi keputusan yang dilaporkan PuTI (Tabel 4.2)."""

    CHANGE_REQUEST = "change_request"
    MEMBER_ABSENCE = "member_absence"
    NEW_MEMBER = "new_member"
    MEMBER_ROTATION = "member_rotation"
    TASK_OVERRUN = "task_overrun"
    TASK_BLOCKER = "task_blocker"
    PRIORITY_CHANGE = "priority_change"
    CRITICAL_BUG = "critical_bug"


class ScenarioStatus(StrEnum):
    DRAFT = "draft"
    SAVED = "saved"


NAME_MAX_LENGTH = 200


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class Scenario(Base):
    __tablename__ = "scenario"
    __table_args__ = (
        CheckConstraint(_one_of("situation", tuple(Situation)), name="situation_valid"),
        CheckConstraint(_one_of("status", tuple(ScenarioStatus)), name="status_valid"),
        CheckConstraint(_one_of("data_source", DATA_SOURCES), name="data_source_valid"),
        CheckConstraint("length(btrim(name)) > 0", name="name_not_blank"),
        CheckConstraint("jsonb_typeof(parameters) = 'object'", name="parameters_is_object"),
        CheckConstraint("status <> 'saved' OR saved_at IS NOT NULL", name="saved_has_saved_at"),
        {"schema": SCHEMA},
    )

    scenario_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH))
    situation: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), server_default=ScenarioStatus.DRAFT.value)
    # Snapshot tidak pernah dihapus aplikasi; RESTRICT menjaga skenario tidak kehilangan dasarnya.
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.baseline_snapshot.snapshot_id", ondelete="RESTRICT")
    )
    # Disalin dari snapshot saat dibuat, supaya daftar skenario dapat menampilkannya (UC-01)
    # tanpa membaca tabel snapshot.
    data_source: Mapped[str] = mapped_column(String(20))
    parameters: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default=text("'{}'::jsonb"))
    duplicated_from: Mapped[int | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.scenario.scenario_id", ondelete="SET NULL")
    )
    # Username, bukan foreign key ke app_user: tabel itu milik J&C (DATA_CONTRACT 7.2).
    created_by: Mapped[str | None] = mapped_column(String(100))
    updated_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # Terakhir kali lolos validasi penuh lewat endpoint save.
    saved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
