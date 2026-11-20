"""SCRUM-27: replikasi Monte Carlo dengan median dan rentang, tanpa basis data."""

import random
import tempfile
import time
from copy import deepcopy
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import MemberAbsenceParams, TaskOverrunParams
from app.modules.simulasi.sim_kpi import KPI_DEFINITIONS, KpiStatus
from app.modules.simulasi.sim_replication import (
    DEFAULT_RUNS,
    MIN_EFFORT,
    percentile,
    run_replications,
    sample_efforts,
    summarize,
    summarize_dates,
)
from app.modules.simulasi.sim_rules import apply_scenario
from app.modules.simulasi.sim_world import build_world
from app.modules.simulasi.snapshot_import import parse_snapshot_files
from app.modules.simulasi.snapshot_models import SNAPSHOT_TABLES
from app.modules.simulasi.snapshot_schemas import BaselineSnapshotData
from synthetic.final_sources import write_final_sources
from synthetic.world import build_world as build_synthetic_world
from tests.sim_fixtures import assignment, complexity, d, dependency, resource, task, world

# CX-M: 2..6 hari. TSK-0001 (3 hari) <- TSK-0002 (2 hari, hard), keduanya milik RES-001.
WORLD = world(
    complexity_levels=[complexity("CX-M", 2, 6, 1.0)],
    resources=[resource("RES-001")],
    tasks=[task("TSK-0001", 3), task("TSK-0002", 2)],
    task_dependencies=[dependency("DEP-001", "TSK-0002", "TSK-0001")],
    assignments=[
        assignment("ASG-0001", "TSK-0001", "RES-001"),
        assignment("ASG-0002", "TSK-0002", "RES-001"),
    ],
)


# --- statistik ---------------------------------------------------------------------------------


def test_percentile_interpolates_linearly():
    values = [1.0, 2.0, 3.0, 4.0]
    assert percentile(values, 0.5) == 2.5
    # Posisi (n - 1) x q: 0.3 dan 2.7.
    assert percentile(values, 0.1) == pytest.approx(1.3)
    assert percentile(values, 0.9) == pytest.approx(3.7)
    assert percentile([7.0], 0.9) == 7.0


def test_summarize_reports_median_range_and_missing_runs():
    stats = summarize([3.0, None, 1.0, 2.0])
    assert (stats.median, stats.p10, stats.p90) == (2.0, 1.2, 2.8)
    assert (stats.min, stats.max, stats.runs, stats.missing) == (1.0, 3.0, 3, 1)


def test_summarize_without_values():
    stats = summarize([None, None])
    assert (stats.median, stats.runs, stats.missing) == (None, 0, 2)


def test_summarize_dates_rounds_to_the_nearest_day():
    stats = summarize_dates([d(5), d(7), None])
    # Median tepat di tengah 05 dan 07; P10 = 05 + 0.2 hari; P90 = 05 + 1.8 hari.
    assert (stats.median, stats.p10, stats.p90) == (d(6), d(5), d(7))
    assert (stats.min, stats.max, stats.missing) == (d(5), d(7), 1)


# --- undian effort -----------------------------------------------------------------------------


def test_efforts_are_drawn_between_min_and_max_days_of_the_complexity_level():
    w = world(
        complexity_levels=[complexity("CX-M", 2, 6, 1.0)],
        resources=[resource("RES-001")],
        tasks=[
            task("TSK-0001", 3),
            # Estimasi di luar rentang tetap diundi dalam rentang (puncak dijepit ke 6).
            task("TSK-0002", 10),
            task(
                "TSK-0003",
                3,
                status="done",
                actual_start="2026-01-02",
                actual_finish="2026-01-02",
            ),
            task("TSK-0004", 3, complexity_id="CX-Z"),
            task("TSK-0005", 3),
        ],
    )
    w.tasks["TSK-0005"].effort_fixed = True
    for seed in range(50):
        efforts = sample_efforts(w, random.Random(seed))
        # Task selesai, task dengan estimasi skenario, dan level tak dikenal tidak diundi.
        assert set(efforts) == {"TSK-0001", "TSK-0002"}
        assert all(2 <= value <= 6 for value in efforts.values())


def test_effort_draws_are_reproducible_with_the_same_seed():
    first = sample_efforts(WORLD, random.Random(11))
    assert first == sample_efforts(WORLD, random.Random(11))
    assert first != sample_efforts(WORLD, random.Random(12))


def test_level_with_zero_minimum_never_gives_an_empty_task():
    w = world(
        complexity_levels=[complexity("CX-M", 0, 0.05, 1.0)],
        tasks=[task("TSK-0001", 0.05)],
    )
    assert sample_efforts(w, random.Random(1))["TSK-0001"] >= MIN_EFFORT


# --- replikasi ---------------------------------------------------------------------------------


def test_replications_are_reproducible_with_a_seed():
    first = run_replications(WORLD, deepcopy(WORLD), runs=30, seed=5)
    second = run_replications(WORLD, deepcopy(WORLD), runs=30, seed=5)
    assert first == second


def test_same_draws_for_baseline_and_scenario_leave_no_difference():
    summary = run_replications(WORLD, deepcopy(WORLD), runs=25, seed=3)
    for definition in KPI_DEFINITIONS:
        stats = summary.kpis[definition.key]
        assert (stats.delta.min, stats.delta.max) == (0.0, 0.0), definition.key
        assert stats.status == KpiStatus.SAME
    tasks = summary.tasks["TSK-0002"]
    assert tasks.baseline_finish == tasks.scenario_finish


def test_median_and_range_are_reported_for_kpis_tasks_projects_and_members():
    summary = run_replications(WORLD, deepcopy(WORLD), runs=40, seed=9)
    assert (summary.runs, summary.seed) == (40, 9)
    completion = summary.kpis["completion_date"].baseline
    assert completion.runs == 40
    assert completion.min <= completion.p10 <= completion.median <= completion.p90
    assert completion.p90 <= completion.max
    # Effort 2..6 hari per task: paling cepat 05 + (2 + 2) hari kerja, paling lambat 12 hari.
    assert d(8) <= completion.min and completion.max <= d(20)
    finish = summary.tasks["TSK-0002"].baseline_finish
    assert finish.runs == 40 and finish.min <= finish.median <= finish.max
    assert summary.projects["PRJ-001"].baseline_completion.median is not None
    weeks = summary.members["RES-001"]
    assert d(5) in weeks
    assert weeks[d(5)].baseline.runs == 40


def test_fixed_scenario_estimate_is_not_drawn_and_shifts_every_replication():
    params = TaskOverrunParams(
        task_id="TSK-0001", original_days=3, revised_days=8, remaining_days=8
    )
    scenario = apply_scenario(WORLD, Situation.TASK_OVERRUN, params).world
    summary = run_replications(WORLD, scenario, runs=30, seed=1)
    # Baseline TSK-0001 paling lama 6 hari; skenario selalu 8 hari, jadi selalu lebih lambat.
    delta = summary.kpis["completion_date"].delta
    assert delta.min > 0
    assert summary.kpis["completion_date"].status == KpiStatus.WORSE
    assert summary.tasks["TSK-0001"].scenario_finish.min == summary.tasks[
        "TSK-0001"
    ].scenario_finish.max


def _synthetic_snapshot() -> BaselineSnapshotData:
    folder = Path(tempfile.mkdtemp())
    write_final_sources(build_synthetic_world(42, date(2026, 11, 6)), folder)
    parsed = parse_snapshot_files(
        {p.name: p.read_text(encoding="utf-8") for p in folder.glob("*.csv")}
    )
    assert parsed.errors == []
    return BaselineSnapshotData.model_validate(
        {
            "snapshot_id": 1,
            "data_source": "synthetic",
            "label": None,
            "imported_by": None,
            "created_at": datetime(2026, 11, 6, tzinfo=timezone.utc),
            "row_counts": parsed.row_counts,
            "checksum": "0" * 64,
            "is_current": True,
            **{t: [row.model_dump() for row in parsed.tables[t]] for t in SNAPSHOT_TABLES},
        }
    )


def test_default_run_count_finishes_quickly_on_the_synthetic_dataset():
    snapshot = _synthetic_snapshot()
    baseline = build_world(snapshot)
    member = next(
        t.owner for t in baseline.tasks.values() if t.owner and baseline.schedulable(t)
    )
    params = MemberAbsenceParams.model_validate(
        {
            "resource_id": member,
            "absence_type": "sudden_leave",
            "absence_period": {"start": "2026-11-09", "end": "2026-11-20"},
        }
    )
    scenario = apply_scenario(baseline, Situation.MEMBER_ABSENCE, params).world
    began = time.perf_counter()
    summary = run_replications(baseline, scenario, runs=DEFAULT_RUNS, seed=42)
    elapsed = time.perf_counter() - began
    assert summary.runs == DEFAULT_RUNS == 500
    # Sekitar 6 detik di laptop pengembang; batas longgar untuk mesin CI yang lebih lambat.
    assert elapsed < 60, f"{DEFAULT_RUNS} replikasi butuh {elapsed:.1f} detik"
