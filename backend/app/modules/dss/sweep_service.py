"""Penyimpanan sweep DSS (SCRUM-68) untuk job latar belakang (SCRUM-69) dan API (SCRUM-70).

- `start_sweep` membuat baris `pending` untuk satu simulasi tersimpan; job menjalankannya.
- Hanya sweep terbaru per skenario yang dibaca. Sweep yang lebih lama digantikan: job-nya
  berhenti pada kandidat berikutnya dan statusnya `cancelled`.
- Sweep `pending` atau `running` yang `updated_at`-nya lebih tua dari DSS_SWEEP_STALE_MINUTES
  dianggap terhenti (misalnya proses API dimulai ulang) dan ditutup `failed` saat dibaca, sehingga
  dapat dijalankan ulang. Cara ini aman untuk beberapa proses API sekaligus, sama dengan job impor
  yang macet (IMPORT_STALE_MINUTES).
"""

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import Settings, get_settings
from app.modules.dss.models import DssSweep, DssSweepCandidate, SweepStatus
from app.modules.dss.sweep_levers import LEVERS
from app.modules.dss.sweep_runner import CandidateStatus, SweepRun
from app.modules.dss.sweep_scoring import Ranking, ScoringWeights
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.simulation_models import SimulationRun

ACTIVE = (SweepStatus.PENDING.value, SweepStatus.RUNNING.value)
SUPERSEDED = "Digantikan sweep yang lebih baru untuk skenario ini"


def weights_from(settings: Settings) -> ScoringWeights:
    return ScoringWeights(
        role_match=settings.dss_weight_role_match,
        available_capacity=settings.dss_weight_available_capacity,
        current_work=settings.dss_weight_current_work,
        kpi_effect=settings.dss_weight_kpi_effect,
    )


def start_sweep(db: Session, run: SimulationRun, username: str | None) -> DssSweep:
    """Sweep `pending` baru untuk simulasi tersimpan `run`; job-nya dijadwalkan pemanggil."""
    sweep = DssSweep(
        scenario_id=run.scenario_id,
        run_id=run.run_id,
        situation=run.situation,
        lever=LEVERS[Situation(run.situation)].lever,
        weights=weights_from(get_settings()).as_json(),
        created_by=username,
    )
    db.add(sweep)
    db.commit()
    db.refresh(sweep)
    return sweep


def latest_run(db: Session, scenario_id: int) -> SimulationRun | None:
    return db.scalar(
        select(SimulationRun)
        .where(SimulationRun.scenario_id == scenario_id)
        .order_by(SimulationRun.run_id.desc())
        .limit(1)
    )


def latest_sweep(db: Session, scenario_id: int) -> DssSweep | None:
    """Sweep terbaru skenario; yang terhenti ditutup `failed` lebih dulu."""
    sweep = db.scalar(
        select(DssSweep)
        .where(DssSweep.scenario_id == scenario_id)
        .order_by(DssSweep.sweep_id.desc())
        .limit(1)
    )
    if sweep is not None:
        close_if_stale(db, sweep)
    return sweep


def close_if_stale(db: Session, sweep: DssSweep, settings: Settings | None = None) -> bool:
    minutes = (settings or get_settings()).dss_sweep_stale_minutes
    if sweep.status not in ACTIVE:
        return False
    if sweep.updated_at >= datetime.now(UTC) - timedelta(minutes=minutes):
        return False
    sweep.status = SweepStatus.FAILED.value
    sweep.error = (
        f"Terhenti tanpa kemajuan lebih dari {minutes} menit (proses mati?); jalankan ulang"
    )
    sweep.finished_at = func.now()
    sweep.updated_at = func.now()
    db.commit()
    db.refresh(sweep)
    return True


def superseded(db: Session, sweep: DssSweep) -> bool:
    newer = db.scalar(
        select(DssSweep.sweep_id)
        .where(DssSweep.scenario_id == sweep.scenario_id, DssSweep.sweep_id > sweep.sweep_id)
        .limit(1)
    )
    return newer is not None


def save_results(
    db: Session, sweep: DssSweep, result: SweepRun, ranking: Ranking, reasons: list[str]
) -> None:
    """Simpan setiap kandidat (urutan `result.results`) dan penanda hasil sweep."""
    scored = {id(candidate.result): candidate for candidate in ranking.candidates}
    for position, (run, reason) in enumerate(zip(result.results, reasons, strict=True)):
        candidate = run.candidate
        rated = scored.get(id(run))
        db.add(
            DssSweepCandidate(
                sweep_id=sweep.sweep_id,
                position=position,
                rank=rated.rank if rated else None,
                status=run.status.value,
                value=candidate.value,
                label=candidate.label[:300],
                is_current=candidate.is_current,
                parameters=candidate.parameters,
                actions=[action.as_json() for action in candidate.actions],
                error=run.error if run.status != CandidateStatus.DONE else None,
                score=rated.score if rated else None,
                criteria=(
                    {
                        criterion.value: {"score": value.score, "facts": value.facts}
                        for criterion, value in rated.criteria.items()
                    }
                    if rated
                    else {}
                ),
                vs_baseline=[item.model_dump(mode="json") for item in run.vs_baseline],
                vs_current=[item.model_dump(mode="json") for item in run.vs_current],
                reason=reason,
                replication=(
                    {"runs": run.replication.runs, "seed": run.replication.seed}
                    if run.replication
                    else None
                ),
                duration_ms=run.duration_ms,
            )
        )
    sweep.completed_candidates = sweep.total_candidates
    sweep.no_action_needed = ranking.no_action_needed
    sweep.no_suitable_candidate = ranking.no_suitable_candidate
