"""Endpoint menjalankan simulasi (UC-03, SCRUM-28) dan melihat hasilnya (UC-04, SCRUM-29).

Dipasang di router modul Simulasi, sehingga path-nya berawalan /simulasi/scenarios/{id}/.
Izinnya sama dengan Scenario Manager (`baseline:read`): aktor UC-03 dan UC-04 adalah pengambil
keputusan yang sama dengan UC-01 dan UC-02.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import AppUser
from app.modules.simulasi import simulation_service as service
from app.modules.simulasi.scenario_router import INVALID, NOT_FOUND, SCENARIO_PERMISSION, to_http
from app.modules.simulasi.scenario_service import ScenarioNotFoundError, ScenarioValidationError
from app.modules.simulasi.simulation_schemas import (
    MemberDrilldown,
    ProjectDrilldown,
    RunPreview,
    SimulationResult,
    SimulationRunRequest,
)

router = APIRouter()

Db = Annotated[Session, Depends(get_db)]
Reader = [Depends(require_permission(SCENARIO_PERMISSION))]
Runner = Annotated[AppUser, Depends(require_permission(SCENARIO_PERMISSION))]
ScenarioId = Annotated[int, Path(ge=1)]
ItemId = Annotated[str, Path(min_length=1, max_length=20)]

NOT_SIMULATED = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Skenario tidak ditemukan, atau belum disimulasikan "
        "(`detail.type = not_simulated`)"
    }
}


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, service.NotSimulatedError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"msg": exc.message, "type": "not_simulated"}
        )
    if isinstance(exc, service.ResultItemNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message)
    return to_http(exc)


@router.get(
    "/scenarios/{scenario_id}/run-preview",
    dependencies=Reader,
    response_model=RunPreview,
    responses=NOT_FOUND,
    summary="Skenario dan parameternya sebelum simulasi dijalankan",
)
def preview_run(db: Db, scenario_id: ScenarioId) -> RunPreview:
    """`can_run` false berarti parameter tersimpan belum lengkap atau tidak valid; kesalahannya
    ada di `parameter_errors`."""
    try:
        return service.preview_run(db, scenario_id)
    except ScenarioNotFoundError as exc:
        raise _http(exc) from exc


@router.post(
    "/scenarios/{scenario_id}/run",
    response_model=SimulationResult,
    responses=NOT_FOUND | INVALID,
    summary="Jalankan simulasi baseline dan skenario (run only / run and save)",
)
def run_simulation(
    db: Db, user: Runner, scenario_id: ScenarioId, payload: SimulationRunRequest
) -> SimulationResult:
    """Parameter tidak lengkap atau tidak valid menghentikan simulasi: 422 dengan `loc` per
    field, dan skenario tidak berubah. `run_and_save` menyimpan skenario lebih dulu, lalu
    hasilnya, sehingga dapat dibuka lagi lewat `GET .../result`. Isi `replication` (boleh `{}`)
    untuk median dan rentang dari replikasi Monte Carlo (bawaan 500 kali)."""
    try:
        return service.run_simulation(db, scenario_id, payload, user.username)
    except (ScenarioNotFoundError, ScenarioValidationError) as exc:
        raise _http(exc) from exc


@router.get(
    "/scenarios/{scenario_id}/result",
    dependencies=Reader,
    response_model=SimulationResult,
    responses=NOT_SIMULATED,
    summary="Hasil simulasi tersimpan terakhir sebuah skenario",
)
def get_result(db: Db, scenario_id: ScenarioId) -> SimulationResult:
    """Bila skenario belum pernah disimulasikan dengan run and save, hasilnya 404 dengan
    `detail.type = not_simulated` dan pesan agar simulasi dijalankan lebih dulu."""
    try:
        return service.get_result(db, scenario_id)
    except (ScenarioNotFoundError, service.NotSimulatedError) as exc:
        raise _http(exc) from exc


@router.get(
    "/scenarios/{scenario_id}/result/projects/{project_id}",
    dependencies=Reader,
    response_model=ProjectDrilldown,
    responses=NOT_SIMULATED,
    summary="Pergeseran mulai dan selesai setiap task satu proyek",
)
def project_drilldown(
    db: Db, scenario_id: ScenarioId, project_id: ItemId
) -> ProjectDrilldown:
    try:
        return service.project_drilldown(db, scenario_id, project_id)
    except (
        ScenarioNotFoundError,
        service.NotSimulatedError,
        service.ResultItemNotFoundError,
    ) as exc:
        raise _http(exc) from exc


@router.get(
    "/scenarios/{scenario_id}/result/members/{resource_id}",
    dependencies=Reader,
    response_model=MemberDrilldown,
    responses=NOT_SIMULATED,
    summary="Beban harian satu anggota beserta periode berbeban berlebih",
)
def member_drilldown(
    db: Db, scenario_id: ScenarioId, resource_id: ItemId
) -> MemberDrilldown:
    try:
        return service.member_drilldown(db, scenario_id, resource_id)
    except (
        ScenarioNotFoundError,
        service.NotSimulatedError,
        service.ResultItemNotFoundError,
    ) as exc:
        raise _http(exc) from exc
