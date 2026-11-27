"""SCRUM-65: menjalankan sweep DSS dan membandingkan KPI setiap kandidat, tanpa basis data.

Baseline uji (`tests.sim_fixtures`, satu task per anggota per hari): PRJ-001 target Jumat
2026-01-09, RES-001 memegang TSK-0001 (4 hari), RES-002 dan RES-003 BE tanpa task, RES-004 BE
cuti 2026-01-05 s.d. 2026-01-09. Skenario pengguna: RES-001 sudden_leave pada pekan yang sama.

    baseline       TSK-0001 selesai 01-08, Schedule Variance -1
    pilihan        menunggu RES-001, selesai 01-15, SV +4
    RES-002/003    dialihkan sejak 01-05, selesai 01-08, SV -1 (5 hari lebih baik dari pilihan)
    RES-004        dikecualikan: tidak hadir sepanjang periode itu
"""

import time

import pytest

from app.modules.dss import sweep_runner
from app.modules.dss.sweep_candidates import build_candidates
from app.modules.dss.sweep_runner import (
    TOP_REPLICATED,
    CandidateStatus,
    SweepError,
    replicate,
    run_sweep,
)
from app.modules.evaluasi.evaluation import kpi_items
from app.modules.evaluasi.kpi_definitions import SCHEDULE_VARIANCE, KpiStatus
from app.modules.simulasi import sim_engine
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import MemberAbsenceParams
from app.modules.simulasi.sim_engine import run_engine
from tests.sim_fixtures import START, absence, assignment, project, resource, snapshot, task
from tests.test_sim_replication import _synthetic_snapshot

SNAPSHOT = snapshot(
    projects=[project(target="2026-01-09")],
    resources=[resource(f"RES-00{i}") for i in range(1, 5)],
    tasks=[task("TSK-0001", 4)],
    assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    availability=[absence("AVL-001", "RES-004", "2026-01-05", "2026-01-09")],
)
PARAMS = {
    "resource_id": "RES-001",
    "absence_type": "sudden_leave",
    "absence_period": {"start": "2026-01-05", "end": "2026-01-09"},
}


def candidates():
    return build_candidates(Situation.MEMBER_ABSENCE, PARAMS, SNAPSHOT, start_date=START)


def sweep(**kwargs):
    return run_sweep(SNAPSHOT, candidates(), start_date=START, **kwargs)


def kpi(items, key=SCHEDULE_VARIANCE):
    return next(item for item in items if item.key == key)


def engine_items(*, actions=(), runs=None, seed=42):
    out = run_engine(
        SNAPSHOT,
        Situation.MEMBER_ABSENCE,
        MemberAbsenceParams.model_validate(PARAMS),
        start_date=START,
        actions=actions,
        runs=runs,
        seed=seed,
    )
    replication = out.replication.kpis if out.replication else None
    return kpi_items(out.baseline.kpis, out.scenario.kpis, replication, out.config)


# --- hasil deterministik -----------------------------------------------------------------------


def test_one_result_per_candidate_in_candidate_order():
    result = sweep()

    assert [(r.candidate.value, r.status) for r in result.results] == [
        (None, CandidateStatus.DONE),
        ("RES-002", CandidateStatus.DONE),
        ("RES-003", CandidateStatus.DONE),
        ("RES-004", CandidateStatus.EXCLUDED),
    ]
    assert result.results[-1].error.startswith("Tidak hadir sepanjang")
    assert result.start_date == START
    assert [r.candidate.value for r in result.done] == [None, "RES-002", "RES-003"]


def test_users_choice_matches_the_saved_simulation():
    result = sweep()

    assert result.current.vs_baseline == engine_items()
    assert {item.status for item in result.current.vs_current} == {KpiStatus.SAME}


def test_candidate_matches_engine_with_its_action():
    result = sweep()
    replacement = result.results[1]

    assert replacement.vs_baseline == engine_items(actions=replacement.candidate.actions)
    assert replacement.side.schedule.tasks["TSK-0001"].finish == START.replace(day=8)


def test_candidate_compared_with_users_choice():
    replacement = sweep().results[1]

    against_baseline = kpi(replacement.vs_baseline)
    assert (against_baseline.baseline, against_baseline.scenario) == (-1, -1)
    assert against_baseline.status == KpiStatus.SAME
    against_choice = kpi(replacement.vs_current)
    assert (against_choice.baseline, against_choice.scenario) == (4, -1)
    assert (against_choice.delta, against_choice.status) == (-5, KpiStatus.BETTER)


def test_baseline_is_simulated_once(monkeypatch):
    calls = []
    original = sim_engine.simulate

    def counting(world, calendar=None, efforts=None, **kwargs):
        calls.append(world)
        return original(world, calendar, efforts, **kwargs)

    monkeypatch.setattr(sim_engine, "simulate", counting)

    sweep()

    # Baseline sekali, lalu pilihan pengguna dan dua kandidat layak; RES-004 tidak.
    assert len(calls) == 4


# --- kegagalan dan kemajuan --------------------------------------------------------------------


def test_failed_candidate_is_marked_and_the_sweep_continues(monkeypatch):
    original = sweep_runner.simulate_scenario

    def failing(base, situation, params, actions=()):
        if actions and actions[0].to_resource_id == "RES-002":
            raise ValueError("Jadwal tidak dapat disusun")
        return original(base, situation, params, actions)

    monkeypatch.setattr(sweep_runner, "simulate_scenario", failing)
    progress = []

    result = sweep(progress=lambda done, total: progress.append((done, total)))

    failed = result.results[1]
    assert (failed.status, failed.error) == (CandidateStatus.FAILED, "Jadwal tidak dapat disusun")
    assert failed.vs_baseline == [] and failed.side is None
    assert result.results[2].status == CandidateStatus.DONE
    assert progress == [(1, 3), (2, 3), (3, 3)]


def test_users_choice_failing_stops_the_sweep(monkeypatch):
    def failing(base, situation, params, actions=()):
        raise ValueError("Snapshot rusak")

    monkeypatch.setattr(sweep_runner, "simulate_scenario", failing)

    with pytest.raises(SweepError, match="Skenario pengguna tidak dapat disimulasikan: Snapshot"):
        sweep()


# --- replikasi Monte Carlo ---------------------------------------------------------------------


def test_replication_only_for_selected_candidates():
    result = sweep(runs=20, seed=7)

    assert all(r.replication is None for r in result.results)
    replicate(result, [result.current, result.results[1]])

    current, replacement, other = result.results[:3]
    assert current.replication.runs == replacement.replication.runs == 20
    assert other.replication is None
    assert {item.basis for item in replacement.vs_baseline} == {"median"}
    assert {item.basis for item in other.vs_baseline} == {"deterministic"}
    # Benih dan jumlah replikasi sama dengan simulasi tersimpan: sebanding dengan pilihan pengguna.
    assert current.vs_baseline == engine_items(runs=20, seed=7)
    assert replacement.vs_baseline == engine_items(
        actions=replacement.candidate.actions, runs=20, seed=7
    )


def test_replicate_does_nothing_without_saved_replication():
    result = sweep()

    replicate(result, result.done)

    assert all(r.replication is None for r in result.results)
    assert TOP_REPLICATED == 3


# --- dataset sintetis --------------------------------------------------------------------------


def test_full_sweep_runtime_on_the_synthetic_dataset():
    data = _synthetic_snapshot()
    base = sim_engine.prepare_baseline(data)
    start, world = base.start_date, base.world
    member = next(t.owner for t in world.tasks.values() if t.owner and world.schedulable(t))
    params = {
        "resource_id": member,
        "absence_type": "sudden_leave",
        "absence_period": {"start": "2026-11-09", "end": "2026-11-20"},
    }
    candidate_set = build_candidates(Situation.MEMBER_ABSENCE, params, data, start_date=start)

    began = time.perf_counter()
    result = run_sweep(data, candidate_set, start_date=start)
    elapsed = time.perf_counter() - began

    assert len(result.done) == len(candidate_set.eligible) > 1
    assert elapsed < 60, f"{len(result.done)} kandidat butuh {elapsed:.1f} detik"
