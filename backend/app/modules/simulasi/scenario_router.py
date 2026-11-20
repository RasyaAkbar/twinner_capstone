"""Endpoint Scenario Manager (SCRUM-22).

Router ini dipasang di router modul Simulasi (`router.include_router`), sehingga path-nya
berawalan /simulasi. Semua endpoint memakai izin `baseline:read`: aktor F1 adalah Kaur/Asman dan
System Analyst tanpa pembatasan fungsi (epic SCRUM-6), sama dengan pembaca baseline snapshot.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import AppUser
from app.modules.auth.permissions import Perm
from app.modules.simulasi import scenario_service as service
from app.modules.simulasi.scenario_models import ScenarioStatus, Situation
from app.modules.simulasi.scenario_schemas import (
    ScenarioCreate,
    ScenarioDuplicate,
    ScenarioRead,
    ScenarioSummary,
    ScenarioUpdate,
    SituationInfo,
)

router = APIRouter()

SCENARIO_PERMISSION = Perm.BASELINE_READ
Db = Annotated[Session, Depends(get_db)]
Reader = [Depends(require_permission(SCENARIO_PERMISSION))]
Manager = Annotated[AppUser, Depends(require_permission(SCENARIO_PERMISSION))]
ScenarioId = Annotated[int, Path(ge=1)]

NOT_FOUND = {status.HTTP_404_NOT_FOUND: {"description": "Skenario tidak ditemukan"}}
NO_BASELINE = {status.HTTP_409_CONFLICT: {"description": "Belum ada baseline snapshot"}}
INVALID = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Isian tidak valid; `detail` berisi `loc`, `msg`, dan `type` per field"
    }
}


def to_http(exc: Exception) -> HTTPException:
    if isinstance(exc, service.ScenarioNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message)
    if isinstance(exc, service.NoBaselineError):
        return HTTPException(status.HTTP_409_CONFLICT, detail={"msg": exc.message})
    if isinstance(exc, service.ScenarioValidationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.as_detail())
    raise exc


# Didaftarkan sebelum /scenarios/{scenario_id} supaya "situations" tidak dibaca sebagai ID.
@router.get(
    "/scenarios/situations",
    dependencies=Reader,
    response_model=list[SituationInfo],
    summary="Delapan situasi keputusan beserta skema parameter untuk formulir UC-02",
)
def list_situations() -> list[SituationInfo]:
    return service.list_situations()


@router.get(
    "/scenarios",
    dependencies=Reader,
    response_model=list[ScenarioSummary],
    summary="Daftar skenario, terakhir diubah lebih dulu",
)
def list_scenarios(
    db: Db,
    scenario_status: Annotated[ScenarioStatus | None, Query(alias="status")] = None,
    situation: Situation | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ScenarioSummary]:
    """`?status=saved` untuk daftar skenario tersimpan (SCRUM-21)."""
    return service.list_scenarios(db, scenario_status, situation, limit)


@router.post(
    "/scenarios",
    response_model=ScenarioRead,
    status_code=status.HTTP_201_CREATED,
    responses=NO_BASELINE | INVALID,
    summary="Buat draft skenario dari satu situasi dan baseline snapshot",
)
def create_scenario(db: Db, user: Manager, payload: ScenarioCreate) -> ScenarioRead:
    """Tanpa `snapshot_id`, skenario dikaitkan ke snapshot terbaru. Bila belum ada snapshot sama
    sekali, hasilnya 409 dengan pesan bahwa skenario belum dapat dibuat (UC-01)."""
    try:
        return service.create_scenario(db, payload, user.username)
    except (service.NoBaselineError, service.ScenarioValidationError) as exc:
        raise to_http(exc) from exc


@router.get(
    "/scenarios/{scenario_id}",
    dependencies=Reader,
    response_model=ScenarioRead,
    responses=NOT_FOUND,
    summary="Satu skenario beserta parameter dan hasil validasinya",
)
def get_scenario(db: Db, scenario_id: ScenarioId) -> ScenarioRead:
    try:
        return service.get_scenario(db, scenario_id)
    except service.ScenarioNotFoundError as exc:
        raise to_http(exc) from exc


@router.patch(
    "/scenarios/{scenario_id}",
    response_model=ScenarioRead,
    responses=NOT_FOUND | INVALID,
    summary="Ubah nama atau parameter skenario (disimpan sebagai draft)",
)
def update_scenario(
    db: Db, user: Manager, scenario_id: ScenarioId, payload: ScenarioUpdate
) -> ScenarioRead:
    """Parameter yang belum lengkap atau belum valid tetap disimpan, dan kesalahannya
    dikembalikan di `parameter_errors`. Mengubah parameter skenario `saved` mengembalikannya ke
    `draft` sampai disimpan lagi."""
    try:
        return service.update_scenario(db, scenario_id, payload, user.username)
    except (service.ScenarioNotFoundError, service.ScenarioValidationError) as exc:
        raise to_http(exc) from exc


@router.post(
    "/scenarios/{scenario_id}/save",
    response_model=ScenarioRead,
    responses=NOT_FOUND | INVALID,
    summary="Validasi penuh lalu simpan skenario sehingga siap disimulasikan",
)
def save_scenario(db: Db, user: Manager, scenario_id: ScenarioId) -> ScenarioRead:
    """Parameter tidak valid: 422 dengan `loc` per field, misalnya
    `["body", "parameters", "absence_period", "end"]`, dan skenario tidak berubah."""
    try:
        return service.save_scenario(db, scenario_id, user.username)
    except (service.ScenarioNotFoundError, service.ScenarioValidationError) as exc:
        raise to_http(exc) from exc


@router.post(
    "/scenarios/{scenario_id}/duplicate",
    response_model=ScenarioRead,
    status_code=status.HTTP_201_CREATED,
    responses=NOT_FOUND | INVALID,
    summary="Duplikasi skenario dengan nama baru dan baseline snapshot yang sama",
)
def duplicate_scenario(
    db: Db,
    user: Manager,
    scenario_id: ScenarioId,
    payload: ScenarioDuplicate | None = None,
) -> ScenarioRead:
    try:
        return service.duplicate_scenario(
            db, scenario_id, payload or ScenarioDuplicate(), user.username
        )
    except (service.ScenarioNotFoundError, service.ScenarioValidationError) as exc:
        raise to_http(exc) from exc


@router.delete(
    "/scenarios/{scenario_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    responses=NOT_FOUND,
    summary="Hapus skenario",
)
def delete_scenario(db: Db, user: Manager, scenario_id: ScenarioId) -> Response:
    try:
        service.delete_scenario(db, scenario_id)
    except service.ScenarioNotFoundError as exc:
        raise to_http(exc) from exc
    return Response(status_code=status.HTTP_204_NO_CONTENT)
