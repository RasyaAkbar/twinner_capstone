"""Baseline snapshot tWinner (SCRUM-15), di schema `twinner` milik tWinner.

Simulasi what-if selalu berjalan terhadap salinan baseline yang dibekukan, bukan terhadap final
sources J&C yang terus berubah. Setiap impor membuat satu snapshot baru yang tidak pernah diubah
lagi, sehingga skenario yang menunjuk `snapshot_id` yang sama selalu memakai data yang sama
(UC-06: membandingkan skenario dari snapshot berbeda memberi peringatan).

Isi setiap tabel D-01 s.d. D-11 disimpan sebagai satu array JSONB per tabel per snapshot:
- snapshot hanya pernah dibaca utuh oleh mesin simulasi, tidak di-query per baris;
- skema JSON-nya mengikuti kolom Data Dictionary apa adanya (DATA_CONTRACT bagian 6), jadi
  tidak perlu menggandakan 11 tabel beserta constraint-nya di schema `twinner`;
- keabsahan isi dijaga validator impor (`snapshot_import`) sebelum disimpan.

DATA_CONTRACT bagian 4: tWinner menyimpan datanya di schema `twinner` dan J&C tidak menulis ke
sana. Tabel di sini tidak punya foreign key ke schema `public`.
"""

from datetime import datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

SCHEMA = "twinner"

# Asal data baseline (UC-01 menampilkannya): sintetis selama pengembangan, PuTI nantinya.
DATA_SOURCES = ("synthetic", "puti")

# D-01 s.d. D-11 dengan nama tabel Data Dictionary, berurutan mengikuti foreign key.
SNAPSHOT_TABLES = (
    "roles",
    "complexity_levels",
    "holidays",
    "projects",
    "resources",
    "tasks",
    "task_dependencies",
    "assignments",
    "workload_items",
    "availability",
    "change_events",
)


def _one_of(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class BaselineSnapshot(Base):
    """Satu kali impor baseline: kapan, dari mana, oleh siapa, dan berapa baris per tabel."""

    __tablename__ = "baseline_snapshot"
    __table_args__ = (
        CheckConstraint(_one_of("data_source", DATA_SOURCES), name="data_source_valid"),
        CheckConstraint("checksum ~ '^[0-9a-f]{64}$'", name="checksum_format"),
        {"schema": SCHEMA},
    )

    snapshot_id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)
    data_source: Mapped[str] = mapped_column(String(20))
    label: Mapped[str | None] = mapped_column(String(200))
    # Username pengimpor. Bukan foreign key ke app_user: tabel itu milik J&C (DATA_CONTRACT 7.2).
    imported_by: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    # {nama_tabel: jumlah_baris}, supaya daftar snapshot tidak perlu membaca isi JSONB.
    row_counts: Mapped[dict[str, int]] = mapped_column(JSONB)
    # SHA-256 isi snapshot dalam bentuk kanonis: isi yang sama selalu menghasilkan nilai yang sama.
    checksum: Mapped[str] = mapped_column(String(64))


class BaselineSnapshotTable(Base):
    """Isi satu tabel final source dalam satu snapshot, sebagai array baris JSON."""

    __tablename__ = "baseline_snapshot_table"
    __table_args__ = (
        CheckConstraint(_one_of("table_name", SNAPSHOT_TABLES), name="table_name_valid"),
        CheckConstraint("jsonb_typeof(data) = 'array'", name="data_is_array"),
        {"schema": SCHEMA},
    )

    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey(f"{SCHEMA}.baseline_snapshot.snapshot_id", ondelete="CASCADE"),
        primary_key=True,
    )
    table_name: Mapped[str] = mapped_column(String(40), primary_key=True)
    data: Mapped[list[dict[str, Any]]] = mapped_column(JSONB)
