"""Menjalankan simulasi skenario dan membaca hasilnya (UC-03/SCRUM-28, UC-04/SCRUM-29).

Alur UC-03 (Gambar 4.1):
1. `preview_run`: skenario, parameter, dan hasil validasinya ditampilkan sebelum dijalankan.
2. `run_simulation`: parameter (tersimpan, atau isian formulir yang belum disimpan) divalidasi
   penuh. Parameter tidak lengkap atau tidak valid menghentikan simulasi (HTTP 422, alur
   alternatif), dan tidak ada yang berubah.
   - `run_only`: baseline dan skenario disimulasikan; hasil hanya dikembalikan.
   - `run_and_save`: skenario disimpan (status `saved`) lebih dulu, lalu disimulasikan, dan
     hasilnya disimpan di `twinner.simulation_run`.
3. Hasil berisi KPI baseline dan skenario, selisih, dan statusnya, beserta proyek, task, dan
   beban anggota yang terdampak.

UC-04: `get_result` membuka hasil tersimpan terakhir. Skenario yang belum pernah disimulasikan
(dan disimpan) menghasilkan `NotSimulatedError` yang meminta pengguna menjalankan simulasi dulu.
"""

import json
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.simulasi import snapshot_service
from app.modules.simulasi.scenario_models import Scenario, ScenarioStatus
from app.modules.simulasi.scenario_params import BaselineIndex, validate_parameters
from app.modules.simulasi.scenario_schemas import ParameterIssue, ScenarioSummary
from app.modules.simulasi.scenario_service import (
    MAX_PARAMETERS_CHARS,
    ScenarioNotFoundError,
    ScenarioValidationError,
)
from app.modules.simulasi.sim_engine import run_engine
from app.modules.simulasi.sim_world import default_start_date
from app.modules.simulasi.simulation_models import SimulationRun
from app.modules.simulasi.simulation_result import build_result
from app.modules.simulasi.simulation_schemas import (
    LatestRun,
    MemberDrilldown,
    ProjectDrilldown,
    ReplicationInfo,
    RunMode,
    RunPreview,
    SimulationResult,
    SimulationRunRequest,
)
from app.modules.simulasi.snapshot_schemas import BaselineSnapshotData

NOT_SIMULATED_MESSAGE = (
    "Skenario ini belum disimulasikan. Jalankan simulasi (Run and save) terlebih dahulu untuk "
    "melihat hasilnya"
)


class NotSimulatedError(Exception):
    """Belum ada hasil simulasi tersimpan untuk skenario ini (UC-04 alur alternatif)."""

    message = NOT_SIMULATED_MESSAGE

    def __init__(self, scenario_id: int) -> None:
        super().__init__(self.message)
        self.scenario_id = scenario_id


class ResultItemNotFoundError(Exception):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# --- sebelum dijalankan ------------------------------------------------------------------------


def preview_run(db: Session, scenario_id: int) -> RunPreview:
    scenario = _scenario(db, scenario_id)
    snapshot = snapshot_service.load_snapshot(db, scenario.snapshot_id)
    outcome = validate_parameters(
        scenario.situation, scenario.parameters, BaselineIndex.build(snapshot)
    )
    latest = _latest(db, scenario_id)
    return RunPreview(
        scenario=ScenarioSummary.model_validate(scenario),
        parameters=scenario.parameters,
        parameter_errors=[ParameterIssue(**error.as_detail()) for error in outcome.errors],
        can_run=outcome.ok,
        snapshot_id=scenario.snapshot_id,
        data_source=scenario.data_source,
        default_start_date=default_start_date(snapshot),
        latest_run=_latest_summary(latest, scenario) if latest is not None else None,
    )


# --- menjalankan -------------------------------------------------------------------------------


def run_simulation(
    db: Session, scenario_id: int, request: SimulationRunRequest, username: str | None
) -> SimulationResult:
    scenario = _scenario(db, scenario_id)
    snapshot = snapshot_service.load_snapshot(db, scenario.snapshot_id)
    params_json, params = _validated(scenario, snapshot, request)

    if request.mode == RunMode.RUN_AND_SAVE:
        # Skenario disimpan sebelum simulasi dijalankan (UC-03).
        scenario.parameters = params_json
        scenario.status = ScenarioStatus.SAVED
        scenario.saved_at = func.now()
        scenario.updated_by = username
        scenario.updated_at = func.now()
        db.commit()
        db.refresh(scenario)

    replication = request.replication
    out = run_engine(
        snapshot,
        scenario.situation,
        params,
        start_date=request.start_date,
        runs=replication.runs if replication else None,
        seed=replication.seed if replication else 0,
    )
    result = build_result(
        out,
        scenario=ScenarioSummary.model_validate(scenario),
        parameters=params_json,
        mode=request.mode,
        snapshot_id=scenario.snapshot_id,
        data_source=scenario.data_source,
    )
    if request.mode != RunMode.RUN_AND_SAVE:
        return result

    run = SimulationRun(
        scenario_id=scenario.scenario_id,
        snapshot_id=scenario.snapshot_id,
        situation=scenario.situation,
        parameters=params_json,
        start_date=result.start_date,
        replications=replication.runs if replication else None,
        seed=replication.seed if replication else None,
        kpis=[kpi.model_dump(mode="json") for kpi in result.kpis],
        result={},
        warning_count=len(result.warnings),
        duration_ms=result.duration_ms,
        created_by=username,
    )
    db.add(run)
    db.flush()
    db.refresh(run)
    result.run_id = run.run_id
    result.saved = True
    result.created_at = run.created_at
    result.created_by = username
    run.result = result.model_dump(mode="json")
    db.commit()
    return result


def _validated(
    scenario: Scenario, snapshot: BaselineSnapshotData, request: SimulationRunRequest
) -> tuple[dict[str, Any], Any]:
    """Validasi penuh; kesalahan apa pun menghentikan simulasi (UC-03 alur alternatif)."""
    raw = request.parameters if request.parameters is not None else scenario.parameters
    if len(json.dumps(raw, ensure_ascii=False)) > MAX_PARAMETERS_CHARS:
        raise ScenarioValidationError(
            [
                (
                    ("parameters",),
                    f"Parameter terlalu besar (paling banyak {MAX_PARAMETERS_CHARS} karakter)",
                    "parameters_too_large",
                )
            ]
        )
    index = BaselineIndex.build(snapshot)
    outcome = validate_parameters(scenario.situation, raw, index)
    errors = [(("parameters", *e.loc), e.message, e.type) for e in outcome.errors]
    if request.start_date is not None and index.horizon is not None:
        first, last = index.horizon
        if not first <= request.start_date <= last:
            errors.append(
                (
                    ("start_date",),
                    f"Di luar rentang baseline ({first.isoformat()} s.d. {last.isoformat()})",
                    "date_out_of_range",
                )
            )
    if errors or outcome.params is None:
        raise ScenarioValidationError(errors)
    return outcome.params.model_dump(mode="json"), outcome.params


# --- hasil tersimpan (UC-04) -------------------------------------------------------------------


def get_result(db: Session, scenario_id: int) -> SimulationResult:
    scenario = _scenario(db, scenario_id)
    run = _latest(db, scenario_id)
    if run is None:
        raise NotSimulatedError(scenario_id)
    result = SimulationResult.model_validate(run.result)
    # Nama dan status skenario bisa berubah setelah hasil disimpan.
    result.scenario = ScenarioSummary.model_validate(scenario)
    result.is_stale = _is_stale(run, scenario)
    return result


def project_drilldown(db: Session, scenario_id: int, project_id: str) -> ProjectDrilldown:
    result = get_result(db, scenario_id)
    project = next((p for p in result.projects if p.project_id == project_id), None)
    if project is None:
        raise ResultItemNotFoundError(f"Proyek {project_id} tidak ada di hasil simulasi")
    return ProjectDrilldown(
        run_id=result.run_id,
        is_stale=result.is_stale,
        project=project,
        tasks=[t for t in result.tasks if t.project_id == project_id],
    )


def member_drilldown(db: Session, scenario_id: int, resource_id: str) -> MemberDrilldown:
    result = get_result(db, scenario_id)
    member = next((m for m in result.members if m.resource_id == resource_id), None)
    if member is None:
        raise ResultItemNotFoundError(f"Anggota {resource_id} tidak ada di hasil simulasi")
    owned = [
        t
        for t in result.tasks
        if any(side is not None and side.owner == resource_id for side in (t.baseline, t.scenario))
    ]
    return MemberDrilldown(
        run_id=result.run_id, is_stale=result.is_stale, member=member, tasks=owned
    )


# --- bantuan -----------------------------------------------------------------------------------


def _scenario(db: Session, scenario_id: int) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise ScenarioNotFoundError(scenario_id)
    return scenario


def _latest(db: Session, scenario_id: int) -> SimulationRun | None:
    return db.scalar(
        select(SimulationRun)
        .where(SimulationRun.scenario_id == scenario_id)
        .order_by(SimulationRun.run_id.desc())
        .limit(1)
    )


def _is_stale(run: SimulationRun, scenario: Scenario) -> bool:
    return run.parameters != scenario.parameters


def _latest_summary(run: SimulationRun, scenario: Scenario) -> LatestRun:
    return LatestRun(
        run_id=run.run_id,
        created_at=run.created_at,
        created_by=run.created_by,
        start_date=run.start_date,
        replication=(
            ReplicationInfo(runs=run.replications, seed=run.seed or 0)
            if run.replications
            else None
        ),
        is_stale=_is_stale(run, scenario),
    )
