"""SCRUM-32: evaluasi lengkap satu simulasi, deterministik dan dengan replikasi (median, rentang).

Dunia: PRJ-001 target Kam 08 (hari kerja ke-4 sejak Sen 05). TSK-0001 milik RES-001, estimasi
3 hari, level CX-M (2..6 hari). Skenario Task Overrun: TSK-0001 menjadi 5 hari (tetap, tidak
diundi).

Task dengan effort e selesai pada hari kerja ke-ceil(e), jadi SV = ceil(e) - 4 hari kerja:
- Deterministik: baseline e = 3 -> selesai Rab 07, SV -1. Skenario e = 5 -> Jum 09, SV +1.
- Replikasi: baseline e diundi segitiga(2, 6, puncak 3); skenario selalu SV +1. Nilai yang
  diharapkan dihitung ulang di tes dari undian yang sama (`sample_efforts` dengan benih yang
  sama), tanpa melewati penjadwal.
"""

import math
import random
import statistics

import pytest

from app.modules.evaluasi.evaluation import build_evaluation, evaluate
from app.modules.evaluasi.kpi_definitions import (
    ESTIMATE_ACCURACY,
    KPI_KEYS,
    ON_TIME_PROBABILITY,
    REWORK_RATE,
    SCHEDULE_VARIANCE,
    EvaluationConfig,
    KpiStatus,
)
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import TaskOverrunParams
from app.modules.simulasi.sim_replication import SEED_STRIDE, sample_efforts
from app.modules.simulasi.sim_world import build_world
from tests.sim_fixtures import START, assignment, complexity, project, resource, snapshot, task

CONFIG = EvaluationConfig()
OVERRUN = TaskOverrunParams(task_id="TSK-0001", original_days=3, revised_days=5, remaining_days=5)


def make_snapshot(min_days: float = 2, max_days: float = 6):
    return snapshot(
        complexity_levels=[complexity("CX-M", min_days, max_days)],
        projects=[project("PRJ-001", target="2026-01-08")],
        resources=[resource("RES-001")],
        tasks=[task("TSK-0001", 3)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    )


def run(snap, runs=None, seed=7, config=CONFIG):
    out = evaluate(
        snap, Situation.TASK_OVERRUN, OVERRUN, config, start_date=START, runs=runs, seed=seed
    )
    return build_evaluation(out, config, situation="task_overrun", snapshot_id=1)


def kpi(evaluation, key):
    return next(k for k in evaluation.kpis if k.key == key)


def test_deterministic_evaluation():
    evaluation = run(make_snapshot())
    assert evaluation.replication is None
    assert [k.key for k in evaluation.kpis] == list(KPI_KEYS)
    sv = kpi(evaluation, SCHEDULE_VARIANCE)
    assert (sv.basis, sv.baseline, sv.scenario, sv.delta, sv.status) == (
        "deterministic",
        -1.0,
        1.0,
        2.0,
        KpiStatus.WORSE,
    )
    on_time = kpi(evaluation, ON_TIME_PROBABILITY)
    assert (on_time.baseline, on_time.scenario, on_time.status) == (1.0, 0.0, KpiStatus.WORSE)
    # Baseline 3 / 3; skenario 5 hari / estimasi baseline 3.
    ear = kpi(evaluation, ESTIMATE_ACCURACY)
    assert (ear.baseline, ear.scenario, ear.status) == (1.0, pytest.approx(5 / 3), KpiStatus.WORSE)
    # Tidak ada task yang pernah masuk QA.
    rework = kpi(evaluation, REWORK_RATE)
    assert (rework.baseline, rework.scenario, rework.status) == (None, None, KpiStatus.SAME)
    assert evaluation.summary.better + evaluation.summary.same + evaluation.summary.worse == 11
    assert evaluation.details.projects[0].model_dump(
        include={"baseline_sv", "scenario_sv", "sv_delta"}
    ) == {
        "baseline_sv": -1,
        "scenario_sv": 1,
        "sv_delta": 2,
    }


def test_replications_report_median_and_range():
    runs, seed = 40, 7
    evaluation = run(make_snapshot(), runs=runs, seed=seed)
    world = build_world(make_snapshot(), START)
    efforts = [
        sample_efforts(world, random.Random(seed * SEED_STRIDE + i))["TSK-0001"]
        for i in range(runs)
    ]
    baseline_sv = [math.ceil(e - 1e-9) - 4 for e in efforts]
    deltas = [1 - sv for sv in baseline_sv]

    sv = kpi(evaluation, SCHEDULE_VARIANCE)
    assert sv.basis == "median"
    assert sv.replication.runs == runs
    assert sv.replication.baseline.median == statistics.median(baseline_sv)
    assert (sv.replication.baseline.min, sv.replication.baseline.max) == (
        min(baseline_sv),
        max(baseline_sv),
    )
    assert (sv.replication.scenario.min, sv.replication.scenario.max) == (1, 1)
    assert sv.replication.delta.median == statistics.median(deltas)
    # Nilai utama = median; status menurut median selisih.
    assert (sv.baseline, sv.scenario, sv.delta) == (
        statistics.median(baseline_sv),
        1,
        statistics.median(deltas),
    )
    # Deterministik tetap tersedia.
    assert (sv.deterministic.baseline, sv.deterministic.scenario) == (-1, 1)

    on_time = kpi(evaluation, ON_TIME_PROBABILITY)
    expected = sum(1 for v in baseline_sv if v <= 0) / runs
    assert (on_time.baseline, on_time.scenario) == (pytest.approx(expected), 0.0)
    assert on_time.replication.baseline is None  # proporsi, tanpa rentang
    assert evaluation.details.projects[0].baseline_on_time_probability == pytest.approx(expected)
    # Undian tidak semuanya tepat waktu atau semuanya terlambat.
    assert 0 < expected < 1


def test_same_seed_gives_the_same_evaluation():
    first = run(make_snapshot(), runs=15, seed=3)
    again = run(make_snapshot(), runs=15, seed=3)
    assert [k.model_dump() for k in first.kpis] == [k.model_dump() for k in again.kpis]


def test_fixed_effort_replications_equal_the_deterministic_run():
    # Rentang kompleksitas 3..3: semua replikasi sama dengan simulasi deterministik.
    evaluation = run(make_snapshot(3, 3), runs=10)
    for item in evaluation.kpis:
        if item.key == ON_TIME_PROBABILITY:
            assert (item.baseline, item.scenario) == (1.0, 0.0)
            continue
        for side in ("baseline", "scenario"):
            stats = getattr(item.replication, side)
            assert stats.min == stats.max == getattr(item.deterministic, side), item.key
        assert item.status == item.deterministic.status, item.key


def test_tolerance_from_config_changes_the_status():
    # SV memburuk 2 hari; dengan toleransi 2 hari dianggap sama.
    config = EvaluationConfig(tolerances={SCHEDULE_VARIANCE: 2})
    evaluation = run(make_snapshot(), config=config)
    sv = kpi(evaluation, SCHEDULE_VARIANCE)
    assert (sv.tolerance, sv.status) == (2, KpiStatus.SAME)
    assert evaluation.config.tolerances[SCHEDULE_VARIANCE] == 2
