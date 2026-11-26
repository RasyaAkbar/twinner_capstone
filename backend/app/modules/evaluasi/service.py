"""Evaluasi KPI simulasi tersimpan dan perbandingan skenario (SCRUM-32).

- `evaluate_run`: KPI baseline dan skenario sebuah simulasi tersimpan (`twinner.simulation_run`,
  dibuat "Run and save" modul Simulasi). Hasilnya disimpan di `twinner.kpi_evaluation` per
  simulasi dan konfigurasi; permintaan berikutnya dengan konfigurasi yang sama memakai hasil
  tersimpan.
- `get_evaluation`: hasil tersimpan dengan konfigurasi bawaan.
- `compare`: evaluasi beberapa simulasi (atau simulasi terakhir beberapa skenario) dan pilih
  skenario terbaik.
"""

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.modules.evaluasi.evaluation import build_evaluation, evaluate
from app.modules.evaluasi.kpi_comparator import Candidate, KpiOutcome, rank
from app.modules.evaluasi.kpi_definitions import KPI_KEYS, EvaluationConfig
from app.modules.evaluasi.models import KpiEvaluationRow
from app.modules.evaluasi.schemas import (
    CompareRequest,
    KpiEvaluation,
    RankedScenario,
    ScenarioComparison,
)
from app.modules.simulasi import snapshot_service
from app.modules.simulasi.scenario_models import Scenario
from app.modules.simulasi.scenario_params import BaselineIndex, validate_parameters
from app.modules.simulasi.simulation_models import SimulationRun

NOT_EVALUATED_MESSAGE = (
    "KPI simulasi ini belum dievaluasi. Jalankan evaluasi (POST .../kpis) terlebih dahulu"
)


class RunNotFoundError(Exception):
    def __init__(self, run_id: int) -> None:
        self.message = f"Simulasi tersimpan {run_id} tidak ada"
        super().__init__(self.message)
        self.run_id = run_id


class ScenarioNotSimulatedError(Exception):
    def __init__(self, scenario_ids: list[int]) -> None:
        ids = ", ".join(str(i) for i in scenario_ids)
        self.message = (
            f"Skenario {ids} tidak ada atau belum punya simulasi tersimpan. Jalankan simulasi "
            "(Run and save) terlebih dahulu"
        )
        super().__init__(self.message)
        self.scenario_ids = scenario_ids


class NotEvaluatedError(Exception):
    message = NOT_EVALUATED_MESSAGE

    def __init__(self, run_id: int) -> None:
        super().__init__(self.message)
        self.run_id = run_id


class EvaluationError(Exception):
    """Simulasi tersimpan tidak dapat dijalankan ulang (parameternya tidak lagi valid)."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


def config_hash(config: EvaluationConfig) -> str:
    canonical = json.dumps(
        config.resolved().model_dump(mode="json"), sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


# --- satu simulasi -------------------------------------------------------------------------------


def evaluate_run(
    db: Session,
    run_id: int,
    config: EvaluationConfig,
    username: str | None,
    *,
    refresh: bool = False,
) -> KpiEvaluation:
    run = _run(db, run_id)
    digest = config_hash(config)
    stored = _stored(db, run_id, digest)
    if stored is not None and not refresh:
        return _from_row(stored)

    evaluation = _compute(db, run, config)
    if stored is not None:
        db.delete(stored)
        db.flush()
    row = KpiEvaluationRow(
        run_id=run.run_id,
        config_hash=digest,
        config=evaluation.config.model_dump(mode="json"),
        kpis=[kpi.model_dump(mode="json") for kpi in evaluation.kpis],
        result={},
        duration_ms=evaluation.duration_ms,
        created_by=username,
    )
    db.add(row)
    db.flush()
    db.refresh(row)
    evaluation.evaluation_id = row.evaluation_id
    evaluation.created_at = row.created_at
    evaluation.created_by = username
    row.result = evaluation.model_dump(mode="json")
    db.commit()
    return evaluation


def get_evaluation(db: Session, run_id: int) -> KpiEvaluation:
    _run(db, run_id)
    stored = _stored(db, run_id, config_hash(EvaluationConfig()))
    if stored is None:
        raise NotEvaluatedError(run_id)
    return _from_row(stored)


def _compute(db: Session, run: SimulationRun, config: EvaluationConfig) -> KpiEvaluation:
    snapshot = snapshot_service.load_snapshot(db, run.snapshot_id)
    outcome = validate_parameters(run.situation, run.parameters, BaselineIndex.build(snapshot))
    if outcome.params is None:
        problems = "; ".join(error.message for error in outcome.errors)
        raise EvaluationError(
            f"Parameter simulasi {run.run_id} tidak dapat dijalankan ulang: {problems}"
        )
    out = evaluate(
        snapshot,
        run.situation,
        outcome.params,
        config,
        start_date=run.start_date,
        runs=run.replications,
        seed=run.seed or 0,
    )
    return build_evaluation(
        out,
        config,
        situation=run.situation,
        snapshot_id=run.snapshot_id,
        run_id=run.run_id,
        scenario_id=run.scenario_id,
    )


# --- perbandingan --------------------------------------------------------------------------------


def compare(db: Session, request: CompareRequest, username: str | None) -> ScenarioComparison:
    runs = _selected_runs(db, request)
    evaluations = [evaluate_run(db, run.run_id, request.config, username) for run in runs]
    scenarios = {
        s.scenario_id: s
        for s in db.scalars(
            select(Scenario).where(Scenario.scenario_id.in_({r.scenario_id for r in runs}))
        )
    }
    candidates = [
        Candidate(
            run_id=evaluation.run_id,
            kpis={
                kpi.key: KpiOutcome(kpi.baseline, kpi.scenario, kpi.delta, kpi.status)
                for kpi in evaluation.kpis
            },
        )
        for evaluation in evaluations
    ]
    weights = {key: request.weights.get(key, 1.0) for key in KPI_KEYS}
    ranked = rank(candidates, weights)
    by_run = {run.run_id: run for run in runs}
    ranking = [
        RankedScenario(
            rank=r.rank,
            run_id=r.run_id,
            scenario_id=by_run[r.run_id].scenario_id,
            scenario_name=scenarios[by_run[r.run_id].scenario_id].name,
            situation=by_run[r.run_id].situation,
            status_score=r.status_score,
            magnitude_score=r.magnitude_score,
            better=r.better,
            same=r.same,
            worse=r.worse,
            improves_on_baseline=r.improves_on_baseline,
            is_best=r.rank == 1,
        )
        for r in ranked
    ]
    best = ranking[0]
    return ScenarioComparison(
        config=request.config.resolved(),
        weights=weights,
        best_run_id=best.run_id,
        best_scenario_id=best.scenario_id,
        comparable=_comparable(runs),
        warnings=_warnings(runs, best),
        ranking=ranking,
        evaluations=evaluations,
    )


def _selected_runs(db: Session, request: CompareRequest) -> list[SimulationRun]:
    if request.run_ids is not None:
        found = {
            run.run_id: run
            for run in db.scalars(
                select(SimulationRun).where(SimulationRun.run_id.in_(request.run_ids))
            )
        }
        missing = [i for i in request.run_ids if i not in found]
        if missing:
            raise RunNotFoundError(missing[0])
        return [found[i] for i in request.run_ids]
    runs: list[SimulationRun] = []
    missing = []
    for scenario_id in request.scenario_ids or []:
        run = db.scalar(
            select(SimulationRun)
            .where(SimulationRun.scenario_id == scenario_id)
            .order_by(SimulationRun.run_id.desc())
            .limit(1)
        )
        if run is None:
            missing.append(scenario_id)
        else:
            runs.append(run)
    if missing:
        raise ScenarioNotSimulatedError(missing)
    return runs


def _comparable(runs: list[SimulationRun]) -> bool:
    return len({(run.snapshot_id, run.start_date) for run in runs}) <= 1


def _warnings(runs: list[SimulationRun], best: RankedScenario) -> list[str]:
    warnings: list[str] = []
    if not _comparable(runs):
        warnings.append(
            "Simulasi yang dibandingkan memakai baseline snapshot atau tanggal mulai berbeda. "
            "Status setiap skenario tetap terhadap baseline-nya sendiri, tetapi nilai KPI "
            "antar skenario tidak sebanding langsung."
        )
    replications = {run.replications for run in runs}
    if len(replications) > 1:
        warnings.append(
            "Sebagian simulasi memakai replikasi dan sebagian tidak (atau jumlahnya berbeda); "
            "nilai utama KPI berasal dari median untuk yang memakai replikasi."
        )
    if not best.improves_on_baseline:
        warnings.append(
            "Tidak ada skenario terpilih yang secara keseluruhan lebih baik dari baseline; "
            "skenario terbaik adalah yang paling sedikit memburukkan KPI."
        )
    return warnings


# --- bantuan -------------------------------------------------------------------------------------


def _run(db: Session, run_id: int) -> SimulationRun:
    run = db.get(SimulationRun, run_id)
    if run is None:
        raise RunNotFoundError(run_id)
    return run


def _stored(db: Session, run_id: int, digest: str) -> KpiEvaluationRow | None:
    return db.scalar(
        select(KpiEvaluationRow).where(
            KpiEvaluationRow.run_id == run_id, KpiEvaluationRow.config_hash == digest
        )
    )


def _from_row(row: KpiEvaluationRow) -> KpiEvaluation:
    data: dict[str, Any] = row.result
    return KpiEvaluation.model_validate(data)
