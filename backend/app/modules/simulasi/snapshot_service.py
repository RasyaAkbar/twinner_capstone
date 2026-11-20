"""Impor dan loader baseline snapshot (SCRUM-15).

Dipakai fitur lain tWinner:
- Scenario Manager (SCRUM-22) dan UC-01 (SCRUM-19): `current_snapshot_id` untuk mengaitkan draft
  skenario ke snapshot terbaru, atau tahu bahwa belum ada baseline sama sekali.
- Mesin simulasi (SCRUM-24, SCRUM-28): `load_snapshot` untuk langkah 1 simulasi (§IV.2.3).
"""

import hashlib
import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.simulasi.snapshot_import import MAX_REPORTED_ERRORS, parse_snapshot_files
from app.modules.simulasi.snapshot_models import (
    SNAPSHOT_TABLES,
    BaselineSnapshot,
    BaselineSnapshotTable,
)
from app.modules.simulasi.snapshot_schemas import (
    BaselineSnapshotData,
    SnapshotImportRequest,
    SnapshotImportResult,
    SnapshotSummary,
)


class SnapshotNotFoundError(Exception):
    def __init__(self, snapshot_id: int | None) -> None:
        self.snapshot_id = snapshot_id
        if snapshot_id is None:
            message = "Belum ada baseline snapshot. Impor data baseline terlebih dahulu"
        else:
            message = f"Baseline snapshot {snapshot_id} tidak ditemukan"
        super().__init__(message)
        self.message = message


def checksum(tables: dict[str, list[dict[str, Any]]]) -> str:
    """SHA-256 dari bentuk kanonis isi snapshot (urutan tabel, kunci, dan baris tetap)."""
    canonical = json.dumps(tables, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def import_snapshot(
    db: Session, request: SnapshotImportRequest, imported_by: str | None
) -> SnapshotImportResult:
    """Periksa sebelas berkas, lalu simpan sebagai snapshot baru atau tidak sama sekali."""
    parsed = parse_snapshot_files(request.files)
    result = SnapshotImportResult(
        dry_run=request.dry_run,
        applied=False,
        row_counts=parsed.row_counts,
        error_count=len(parsed.errors),
        errors=parsed.errors[:MAX_REPORTED_ERRORS],
        snapshot=None,
    )
    if parsed.errors or request.dry_run:
        return result

    data = {
        table: [row.model_dump(mode="json") for row in parsed.tables[table]]
        for table in SNAPSHOT_TABLES
    }
    snapshot = BaselineSnapshot(
        data_source=request.data_source,
        label=request.label or None,
        imported_by=imported_by,
        row_counts={table: len(rows) for table, rows in data.items()},
        checksum=checksum(data),
    )
    db.add(snapshot)
    db.flush()
    db.add_all(
        BaselineSnapshotTable(snapshot_id=snapshot.snapshot_id, table_name=table, data=rows)
        for table, rows in data.items()
    )
    db.commit()
    db.refresh(snapshot)
    result.applied = True
    result.snapshot = _summary(snapshot, is_current=True)
    return result


def current_snapshot_id(db: Session) -> int | None:
    """Snapshot terbaru, atau None bila belum pernah ada impor (UC-01 alur alternatif)."""
    return db.scalar(select(func.max(BaselineSnapshot.snapshot_id)))


def list_snapshots(db: Session, limit: int = 50) -> list[SnapshotSummary]:
    """Snapshot terbaru lebih dulu. Isi JSONB tidak ikut dibaca."""
    current = current_snapshot_id(db)
    statement = select(BaselineSnapshot).order_by(BaselineSnapshot.snapshot_id.desc()).limit(limit)
    return [
        _summary(snapshot, is_current=snapshot.snapshot_id == current)
        for snapshot in db.scalars(statement)
    ]


def load_snapshot(db: Session, snapshot_id: int | None = None) -> BaselineSnapshotData:
    """Muat snapshot lengkap sebagai objek bertipe; `None` berarti snapshot terbaru.

    Setiap panggilan membuat objek baru dari basis data, jadi pemanggil boleh mengubahnya
    (misalnya aturan skenario SCRUM-24) tanpa mengubah baseline yang tersimpan.
    Melempar `SnapshotNotFoundError` bila snapshot tidak ada.
    """
    current = current_snapshot_id(db)
    target = current if snapshot_id is None else snapshot_id
    snapshot = db.get(BaselineSnapshot, target) if target is not None else None
    if snapshot is None:
        raise SnapshotNotFoundError(snapshot_id)
    tables: dict[str, list[dict[str, Any]]] = {
        part.table_name: part.data
        for part in db.scalars(
            select(BaselineSnapshotTable).where(
                BaselineSnapshotTable.snapshot_id == snapshot.snapshot_id
            )
        )
    }
    summary = _summary(snapshot, is_current=snapshot.snapshot_id == current)
    return BaselineSnapshotData.model_validate(
        {**summary.model_dump(), **{table: tables.get(table, []) for table in SNAPSHOT_TABLES}}
    )


def _summary(snapshot: BaselineSnapshot, *, is_current: bool) -> SnapshotSummary:
    summary = SnapshotSummary.model_validate(snapshot)
    summary.is_current = is_current
    return summary
