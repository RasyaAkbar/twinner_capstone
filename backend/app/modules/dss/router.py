"""Endpoint modul DSS (placeholder).

Izin mengikuti Simulasi dan Evaluasi (`baseline:read`). Endpoint ditambahkan di sini saat modul
DSS dikerjakan.
"""

from fastapi import APIRouter, Depends

from app.modules.auth.dependencies import require_permission
from app.modules.simulasi.scenario_router import SCENARIO_PERMISSION

router = APIRouter(prefix="/dss", tags=["DSS"])
Reader = [Depends(require_permission(SCENARIO_PERMISSION))]


@router.get("/status", dependencies=Reader, summary="Status modul DSS (placeholder)")
def status() -> dict[str, str]:
    return {"module": "dss", "status": "placeholder"}