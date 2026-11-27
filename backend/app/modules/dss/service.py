"""Isi API DSS per skenario (SCRUM-70): daftar skenario, sweep terakhir, rincian kandidat, ulang."""

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.dss import sweep_service
from app.modules.dss.models import DssSweep, DssSweepCandidate, SweepStatus
from app.modules.dss.schemas import (
    CandidateRead,
    CandidateSummary,
    DssScenario,
    SweepRead,
    SweepSummary,
)
from app.modules.dss.sweep_job import SweepScheduler, job_sessions
from app.modules.dss.sweep_levers import LEVERS
from app.modules.simulasi.scenario_models import Scenario, Situation
from app.modules.simulasi.scenario_schemas import ScenarioSummary
from app.modules.simulasi.scenario_service import ScenarioNotFoundError
from app.modules.simulasi.simulation_models import SimulationRun
from app.modules.simulasi.simulation_service import NotSimulatedError


class NoSweepError(Exception):
    message = (
        "Belum ada rekomendasi untuk simulasi tersimpan skenario ini; jalankan sweep lewat "
        "POST /dss/scenarios/{scenario_id}/sweep"
    )


class SweepRunningError(Exception):
    message = "Sweep untuk skenario ini masih berjalan"


class CandidateNotFoundError(Exception):
    def __init__(self, candidate_id: int) -> None:
        super().__init__(f"Kandidat {candidate_id} tidak ada di sweep skenario ini")
        self.message = str(self)


def list_scenarios(db: Session) -> list[DssScenario]:
    """Skenario dengan simulasi tersimpan, beserta sweep terakhirnya; yang terbaru lebih dulu."""
    latest_runs = (
        select(SimulationRun.scenario_id, func.max(SimulationRun.run_id).label("run_id"))
        .group_by(SimulationRun.scenario_id)
        .subquery()
    )
    rows = db.execute(
        select(Scenario, SimulationRun)
        .join(latest_runs, latest_runs.c.scenario_id == Scenario.scenario_id)
        .join(SimulationRun, SimulationRun.run_id == latest_runs.c.run_id)
        .order_by(SimulationRun.run_id.desc())
    ).all()
    latest_sweeps = (
        select(func.max(DssSweep.sweep_id)).group_by(DssSweep.scenario_id).scalar_subquery()
    )
    sweeps = {
        sweep.scenario_id: sweep
        for sweep in db.scalars(select(DssSweep).where(DssSweep.sweep_id.in_(latest_sweeps)))
    }
    items: list[DssScenario] = []
    for scenario, run in rows:
        sweep = sweeps.get(scenario.scenario_id)
        if sweep is not None:
            sweep_service.close_if_stale(db, sweep)
        items.append(
            DssScenario(
                scenario=ScenarioSummary.model_validate(scenario),
                run_id=run.run_id,
                run_created_at=run.created_at,
                is_stale=run.parameters != scenario.parameters
                or (sweep is not None and sweep.run_id != run.run_id),
                sweep=_summary(sweep) if sweep is not None else None,
            )
        )
    return items


def sweep_read(db: Session, scenario_id: int) -> SweepRead:
    scenario = _scenario(db, scenario_id)
    if sweep_service.latest_run(db, scenario_id) is None:
        raise NotSimulatedError(scenario_id)
    sweep = sweep_service.latest_sweep(db, scenario_id)
    if sweep is None:
        raise NoSweepError
    run = db.get(SimulationRun, sweep.run_id)
    assert run is not None
    rows = list(
        db.scalars(
            select(DssSweepCandidate)
            .where(DssSweepCandidate.sweep_id == sweep.sweep_id)
            .order_by(
                DssSweepCandidate.rank.is_(None),
                DssSweepCandidate.rank,
                DssSweepCandidate.position,
            )
        )
    )
    current = next((row for row in rows if row.is_current), None)
    return SweepRead(
        **_summary(sweep).model_dump(),
        scenario=ScenarioSummary.model_validate(scenario),
        snapshot_id=run.snapshot_id,
        data_source=scenario.data_source,
        is_stale=run.parameters != scenario.parameters,
        weights=sweep.weights,
        truncated=sweep.omitted_candidates > 0,
        omitted_candidates=sweep.omitted_candidates,
        no_lever=sweep.no_lever,
        no_action_needed=sweep.no_action_needed,
        no_suitable_candidate=sweep.no_suitable_candidate,
        current_rank=current.rank if current is not None else None,
        ranked_candidates=sum(1 for row in rows if row.rank is not None),
        candidates=[CandidateSummary(**_candidate_fields(row)) for row in rows],
    )


def candidate_read(db: Session, scenario_id: int, candidate_id: int) -> CandidateRead:
    _scenario(db, scenario_id)
    row = db.get(DssSweepCandidate, candidate_id)
    sweep = db.get(DssSweep, row.sweep_id) if row is not None else None
    if row is None or sweep is None or sweep.scenario_id != scenario_id:
        raise CandidateNotFoundError(candidate_id)
    return CandidateRead(
        **_candidate_fields(row),
        sweep_id=row.sweep_id,
        parameters=row.parameters,
        actions=row.actions,
        criteria_facts={key: value.get("facts", {}) for key, value in row.criteria.items()},
        vs_baseline=row.vs_baseline,
        vs_current=row.vs_current,
        replication=row.replication,
        duration_ms=row.duration_ms,
    )


def restart(
    db: Session, scenario_id: int, username: str | None, scheduler: SweepScheduler
) -> SweepSummary:
    """Sweep baru untuk simulasi tersimpan terakhir, misalnya setelah sweep sebelumnya gagal."""
    _scenario(db, scenario_id)
    run = sweep_service.latest_run(db, scenario_id)
    if run is None:
        raise NotSimulatedError(scenario_id)
    previous = sweep_service.latest_sweep(db, scenario_id)
    if previous is not None and previous.status in sweep_service.ACTIVE:
        raise SweepRunningError
    sweep = sweep_service.start_sweep(db, run, username)
    scheduler.submit(sweep.sweep_id, job_sessions(db))
    db.refresh(sweep)
    return _summary(sweep)


def _scenario(db: Session, scenario_id: int) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise ScenarioNotFoundError(scenario_id)
    return scenario


def _summary(sweep: DssSweep) -> SweepSummary:
    return SweepSummary(
        sweep_id=sweep.sweep_id,
        run_id=sweep.run_id,
        situation=Situation(sweep.situation),
        lever=sweep.lever,
        lever_label=LEVERS[Situation(sweep.situation)].label,
        status=SweepStatus(sweep.status),
        total_candidates=sweep.total_candidates,
        completed_candidates=sweep.completed_candidates,
        error=sweep.error,
        created_by=sweep.created_by,
        created_at=sweep.created_at,
        started_at=sweep.started_at,
        finished_at=sweep.finished_at,
        duration_ms=sweep.duration_ms,
    )


def _candidate_fields(row: DssSweepCandidate) -> dict[str, Any]:
    return {
        "candidate_id": row.candidate_id,
        "position": row.position,
        "rank": row.rank,
        "status": row.status,
        "value": row.value,
        "label": row.label,
        "is_current": row.is_current,
        "score": row.score,
        "criteria": {key: value.get("score") for key, value in row.criteria.items()},
        "reason": row.reason,
        "error": row.error,
        "replicated": row.replication is not None,
    }
