"""Endpoint modul Evaluasi: KPI Calculator and Comparator (SCRUM-32).

Izinnya sama dengan menjalankan dan melihat hasil simulasi (`baseline:read`): pengguna
evaluasi adalah pengambil keputusan yang sama dengan UC-03 dan UC-04.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.auth.dependencies import require_permission
from app.modules.auth.models import AppUser
from app.modules.evaluasi import service
from app.modules.evaluasi.kpi_definitions import KPI_DEFINITIONS, EvaluationConfig
from app.modules.evaluasi.schemas import (
    CompareRequest,
    EvaluateRequest,
    KpiCatalog,
    KpiDefinitionOut,
    KpiEvaluation,
    ScenarioComparison,
)
from app.modules.simulasi.scenario_router import SCENARIO_PERMISSION

router = APIRouter(prefix="/evaluasi", tags=["Evaluasi"])

Db = Annotated[Session, Depends(get_db)]
Reader = [Depends(require_permission(SCENARIO_PERMISSION))]
Evaluator = Annotated[AppUser, Depends(require_permission(SCENARIO_PERMISSION))]
RunId = Annotated[int, Path(ge=1)]

NOT_FOUND = {
    status.HTTP_404_NOT_FOUND: {
        "description": "Simulasi tersimpan tidak ada, atau belum dievaluasi "
        "(`detail.type = not_evaluated`)"
    }
}
UNPROCESSABLE = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {
        "description": "Permintaan tidak valid, atau simulasi tidak dapat dijalankan ulang"
    }
}


def _http(exc: Exception) -> HTTPException:
    if isinstance(exc, service.NotEvaluatedError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"msg": exc.message, "type": "not_evaluated"}
        )
    if isinstance(exc, service.ScenarioNotSimulatedError):
        return HTTPException(
            status.HTTP_404_NOT_FOUND, detail={"msg": exc.message, "type": "not_simulated"}
        )
    if isinstance(exc, service.RunNotFoundError):
        return HTTPException(status.HTTP_404_NOT_FOUND, detail=exc.message)
    if isinstance(exc, service.EvaluationError):
        return HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=exc.message)
    raise exc


@router.get(
    "/kpi-definitions",
    dependencies=Reader,
    response_model=KpiCatalog,
    summary="Daftar KPI evaluasi (FR-04.1 s.d. FR-04.10), arah, toleransi, dan parameter bawaan",
)
def kpi_definitions() -> KpiCatalog:
    return KpiCatalog(
        kpis=[
            KpiDefinitionOut(
                key=d.key,
                fr=d.fr,
                label=d.label,
                unit=d.unit,
                direction=d.direction,
                target=d.target,
                tolerance=d.tolerance,
                missing=d.missing,
                definition=d.definition,
                formula=d.formula,
            )
            for d in KPI_DEFINITIONS
        ],
        default_config=EvaluationConfig().resolved(),
    )


@router.post(
    "/runs/{run_id}/kpis",
    response_model=KpiEvaluation,
    responses=NOT_FOUND | UNPROCESSABLE,
    summary="Hitung KPI baseline dan skenario sebuah simulasi tersimpan, lalu simpan",
)
def evaluate_run(
    db: Db, user: Evaluator, run_id: RunId, payload: EvaluateRequest | None = None
) -> KpiEvaluation:
    """Body boleh kosong (konfigurasi bawaan). Hasil disimpan per simulasi dan konfigurasi;
    permintaan berikutnya dengan konfigurasi yang sama mengembalikan hasil tersimpan kecuali
    `refresh` diisi true. Simulasi dengan replikasi menghasilkan median dan rentang."""
    payload = payload or EvaluateRequest()
    try:
        return service.evaluate_run(
            db, run_id, payload.config, user.username, refresh=payload.refresh
        )
    except (service.RunNotFoundError, service.EvaluationError) as exc:
        raise _http(exc) from exc


@router.get(
    "/runs/{run_id}/kpis",
    dependencies=Reader,
    response_model=KpiEvaluation,
    responses=NOT_FOUND,
    summary="KPI tersimpan sebuah simulasi (konfigurasi bawaan)",
)
def get_evaluation(db: Db, run_id: RunId) -> KpiEvaluation:
    try:
        return service.get_evaluation(db, run_id)
    except (service.RunNotFoundError, service.NotEvaluatedError) as exc:
        raise _http(exc) from exc


@router.post(
    "/compare",
    response_model=ScenarioComparison,
    responses=NOT_FOUND | UNPROCESSABLE,
    summary="Bandingkan KPI beberapa skenario terhadap baseline dan pilih skenario terbaik",
)
def compare(db: Db, user: Evaluator, payload: CompareRequest) -> ScenarioComparison:
    """Isi `run_ids` (simulasi tersimpan) atau `scenario_ids` (simulasi tersimpan terakhir
    setiap skenario), paling banyak 10. Simulasi yang belum dievaluasi dengan konfigurasi ini
    dievaluasi dan disimpan lebih dulu."""
    try:
        return service.compare(db, payload, user.username)
    except (
        service.RunNotFoundError,
        service.ScenarioNotSimulatedError,
        service.EvaluationError,
    ) as exc:
        raise _http(exc) from exc
