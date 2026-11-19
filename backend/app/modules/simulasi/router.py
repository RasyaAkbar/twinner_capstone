from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import AppUser
from app.modules.auth.permissions import Perm
from app.modules.simulasi import snapshot_service as service
from app.modules.simulasi.snapshot_schemas import (
    BaselineSnapshotData,
    SnapshotImportRequest,
    SnapshotImportResult,
    SnapshotSummary,
)

router = APIRouter(prefix="/simulasi", tags=["Simulasi"])

Db = Annotated[Session, Depends(get_db)]
# Impor baseline = pekerjaan impor data (Administrator Data). Membaca baseline = pengambil
# keputusan UC-01 (Kaur/Asman, System Analyst), sama dengan izin halaman baseline.
Importer = Annotated[AppUser, Depends(require_permission(Perm.IMPORT_RUN))]
SNAPSHOT_READ = [Depends(require_permission(Perm.BASELINE_READ))]
NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"description": "Snapshot tidak ada"}}


def _not_found(exc: service.SnapshotNotFoundError) -> HTTPException:
    return HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message)


@router.post(
    "/baseline-snapshots/import",
    response_model=SnapshotImportResult,
    summary="Impor 11 CSV final sources sebagai baseline snapshot baru (periksa dulu dengan "
    "dry_run)",
)
def import_baseline_snapshot(
    db: Db, user: Importer, payload: SnapshotImportRequest
) -> SnapshotImportResult:
    """Kesebelas berkas diperiksa lebih dulu. Bila ada satu kesalahan, tidak ada yang disimpan
    dan `errors` berisi kesalahan per berkas dan baris. Impor yang berhasil selalu membuat
    snapshot baru yang menjadi snapshot terbaru; snapshot lama tidak diubah."""
    return service.import_snapshot(db, payload, imported_by=user.username)


@router.get(
    "/baseline-snapshots",
    dependencies=SNAPSHOT_READ,
    response_model=list[SnapshotSummary],
    summary="Daftar baseline snapshot, terbaru lebih dulu",
)
def list_baseline_snapshots(
    db: Db, limit: Annotated[int, Query(ge=1, le=200)] = 50
) -> list[SnapshotSummary]:
    return service.list_snapshots(db, limit)


@router.get(
    "/baseline-snapshots/current",
    dependencies=SNAPSHOT_READ,
    response_model=BaselineSnapshotData,
    responses=NOT_FOUND,
    summary="Snapshot terbaru lengkap: proyek, task, dependency, penugasan, kapasitas, "
    "ketidakhadiran, hari libur",
)
def get_current_baseline_snapshot(db: Db) -> BaselineSnapshotData:
    try:
        return service.load_snapshot(db)
    except service.SnapshotNotFoundError as exc:
        raise _not_found(exc) from exc


@router.get(
    "/baseline-snapshots/{snapshot_id}",
    dependencies=SNAPSHOT_READ,
    response_model=BaselineSnapshotData,
    responses=NOT_FOUND,
    summary="Satu baseline snapshot lengkap",
)
def get_baseline_snapshot(db: Db, snapshot_id: Annotated[int, Path(ge=1)]) -> BaselineSnapshotData:
    try:
        return service.load_snapshot(db, snapshot_id)
    except service.SnapshotNotFoundError as exc:
        raise _not_found(exc) from exc
