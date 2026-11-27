"""Endpoint DSS per skenario: layar Rekomendasi › Per skenario (SCRUM-70, UC-07).

Izin mengikuti Simulasi dan Evaluasi (`baseline:read`): aktor UC-07 adalah pengambil keputusan
yang sama dengan UC-01 s.d. UC-06. Sweep dimulai otomatis setelah Run and save (SCRUM-69);
`POST .../sweep` menjalankannya ulang, misalnya setelah gagal.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import AppUser
from app.modules.dss import service
from app.modules.dss.schemas import CandidateRead, DssScenario, SweepRead, SweepSummary
from app.modules.dss.sweep_job import SweepScheduler, get_sweep_scheduler
from app.modules.simulasi.scenario_router import SCENARIO_PERMISSION, to_http
from app.modules.simulasi.scenario_service import ScenarioNotFoundError
from app.modules.simulasi.simulation_service import NotSimulatedError

router = APIRouter(prefix="/dss", tags=["DSS"])

Db = Annotated[Session, Depends(get_db)]
Reader = [Depends(require_permission(SCENARIO_PERMISSION))]
Runner = Annotated[AppUser, Depends(require_permission(SCENARIO_PERMISSION))]
Scheduler = Annotated[SweepScheduler, Depends(get_sweep_scheduler)]
ScenarioId = Annotated[int, Path(ge=1)]
CandidateId = Annotated[int, Path(ge=1)]

NOT_FOUND = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Skenario tidak ditemukan, belum disimulasikan dan disimpan "
        "(`detail.type = not_simulated`), atau belum punya sweep (`detail.type = no_sweep`)"
    }
}
RUNNING = {status.HTTP_409_CONFLICT: {"description": "Sweep skenario ini masih berjalan"}}


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, NotSimulatedError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"msg": exc.message, "type": "not_simulated"}
        )
    if isinstance(exc, service.NoSweepError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"msg": exc.message, "type": "no_sweep"}
        )
    if isinstance(exc, service.CandidateNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message)
    if isinstance(exc, service.SweepRunningError):
        return HTTPException(
            status.HTTP_409_CONFLICT, detail={"msg": exc.message, "type": "sweep_running"}
        )
    return to_http(exc)


@router.get(
    "/scenarios",
    dependencies=Reader,
    response_model=list[DssScenario],
    summary="Skenario yang sudah disimulasikan beserta status sweep terakhirnya",
)
def list_scenarios(db: Db) -> list[DssScenario]:
    return service.list_scenarios(db)


@router.get(
    "/scenarios/{scenario_id}/sweep",
    dependencies=Reader,
    response_model=SweepRead,
    responses=NOT_FOUND,
    summary="Sweep terakhir: status, kemajuan, dan kandidat menurut peringkat",
)
def get_sweep(db: Db, scenario_id: ScenarioId) -> SweepRead:
    """Selama `pending`/`running`, `candidates` kosong dan `completed_candidates` menunjukkan
    kemajuan. `current_rank` adalah peringkat pilihan Anda dari `ranked_candidates`."""
    try:
        return service.sweep_read(db, scenario_id)
    except (ScenarioNotFoundError, NotSimulatedError, service.NoSweepError) as exc:
        raise _http(exc) from exc


@router.get(
    "/scenarios/{scenario_id}/sweep/candidates/{candidate_id}",
    dependencies=Reader,
    response_model=CandidateRead,
    responses=NOT_FOUND,
    summary="Rincian satu kandidat: fakta kriteria dan perbandingan KPI lengkap",
)
def get_candidate(db: Db, scenario_id: ScenarioId, candidate_id: CandidateId) -> CandidateRead:
    try:
        return service.candidate_read(db, scenario_id, candidate_id)
    except (ScenarioNotFoundError, service.CandidateNotFoundError) as exc:
        raise _http(exc) from exc


@router.post(
    "/scenarios/{scenario_id}/sweep",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=SweepSummary,
    responses=NOT_FOUND | RUNNING,
    summary="Jalankan ulang sweep untuk simulasi tersimpan terakhir",
)
def restart_sweep(
    db: Db, user: Runner, scheduler: Scheduler, scenario_id: ScenarioId
) -> SweepSummary:
    try:
        return service.restart(db, scenario_id, user.username, scheduler)
    except (ScenarioNotFoundError, NotSimulatedError, service.SweepRunningError) as exc:
        raise _http(exc) from exc
