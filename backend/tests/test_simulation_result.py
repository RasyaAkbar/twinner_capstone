"""SCRUM-28/SCRUM-29: isi layar hasil simulasi (KPI, proyek, task, beban anggota), tanpa basis data.

Baseline uji, skenario Task Overrun TSK-0001 3 -> 5 hari:

    PRJ-001 target Jumat 09                 baseline     skenario
        TSK-0001 BE 3 hari (RES-001)          05-07        05-09
        TSK-0002 BE 2 hari, hard, rencana 08  08-09        12-13
    PRJ-002: TSK-0101 BE 2 hari (RES-002)     05-06        05-06
"""

from datetime import datetime, timezone

from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import TaskOverrunParams
from app.modules.simulasi.scenario_schemas import ScenarioSummary
from app.modules.simulasi.sim_engine import run_engine
from app.modules.simulasi.sim_kpi import KPI_DEFINITIONS
from app.modules.simulasi.simulation_result import build_result
from app.modules.simulasi.simulation_schemas import RunMode, SimulationResult
from tests.sim_fixtures import (
    START,
    assignment,
    d,
    dependency,
    project,
    resource,
    snapshot,
    task,
)

SNAPSHOT = snapshot(
    projects=[project("PRJ-001", target="2026-01-09"), project("PRJ-002", rank=2)],
    resources=[resource("RES-001"), resource("RES-002")],
    tasks=[
        task("TSK-0001", 3),
        task("TSK-0002", 2, start="2026-01-08"),
        task("TSK-0101", 2, project_id="PRJ-002"),
    ],
    task_dependencies=[dependency("DEP-001", "TSK-0002", "TSK-0001")],
    assignments=[
        assignment("ASG-0001", "TSK-0001", "RES-001"),
        assignment("ASG-0002", "TSK-0002", "RES-001"),
        assignment("ASG-0003", "TSK-0101", "RES-002"),
    ],
)
PARAMS = TaskOverrunParams(task_id="TSK-0001", original_days=3, revised_days=5, remaining_days=5)
NOW = datetime(2026, 1, 5, tzinfo=timezone.utc)
SCENARIO = ScenarioSummary(
    scenario_id=1,
    name="Overrun API",
    situation=Situation.TASK_OVERRUN,
    status="saved",
    snapshot_id=1,
    data_source="synthetic",
    duplicated_from=None,
    created_by="uji",
    updated_by="uji",
    created_at=NOW,
    updated_at=NOW,
    saved_at=NOW,
)


def result(runs: int | None = None) -> SimulationResult:
    out = run_engine(SNAPSHOT, Situation.TASK_OVERRUN, PARAMS, start_date=START, runs=runs, seed=3)
    return build_result(
        out,
        scenario=SCENARIO,
        parameters=PARAMS.model_dump(mode="json"),
        mode=RunMode.RUN_ONLY,
        snapshot_id=1,
        data_source="synthetic",
    )


RESULT = result()


def by_key(items, attribute):
    return {getattr(item, attribute): item for item in items}


def test_result_header():
    assert (RESULT.start_date, RESULT.end_date) == (d(5), d(13))
    assert (RESULT.run_id, RESULT.saved, RESULT.mode) == (None, False, RunMode.RUN_ONLY)
    assert RESULT.replication is None
    assert RESULT.parameters["revised_days"] == 5.0
    assert any("Estimasi TSK-0001" in note for note in RESULT.notes)


def test_kpis_compare_scenario_with_baseline():
    kpis = by_key(RESULT.kpis, "key")
    assert list(kpis) == [definition.key for definition in KPI_DEFINITIONS]
    completion = kpis["completion_date"]
    # 09 -> 13: Senin 12 dan Selasa 13.
    assert (completion.baseline, completion.scenario, completion.delta) == (d(9), d(13), 2.0)
    assert completion.status == "worse"
    assert (kpis["total_delay_days"].baseline, kpis["total_delay_days"].scenario) == (0.0, 2.0)
    assert kpis["projects_missing_target"].status == "worse"
    waiting = kpis["dependency_delayed_tasks"]
    assert (waiting.baseline, waiting.scenario) == (0.0, 1.0)
    assert kpis["peak_load"].status == "same"
    assert kpis["unscheduled_tasks"].status == "same"


def test_affected_tasks_and_their_shift():
    tasks = by_key(RESULT.tasks, "task_id")
    overrun = tasks["TSK-0001"]
    assert (overrun.targeted, overrun.affected, overrun.owner_changed) == (True, True, False)
    assert (overrun.start_shift, overrun.finish_shift) == (0, 2)
    assert (overrun.baseline.finish, overrun.scenario.finish) == (d(7), d(9))
    assert overrun.scenario.effort == 5.0

    dependent = tasks["TSK-0002"]
    assert (dependent.targeted, dependent.affected) == (False, True)
    assert (dependent.start_shift, dependent.finish_shift) == (2, 2)
    assert dependent.scenario.dependency_ready == d(12)

    other = tasks["TSK-0101"]
    assert (other.affected, other.finish_shift) == (False, 0)


def test_affected_projects_with_completion_shift():
    projects = by_key(RESULT.projects, "project_id")
    assert list(projects) == ["PRJ-001", "PRJ-002"]
    late = projects["PRJ-001"]
    assert (late.affected, late.completion_shift, late.affected_task_count) == (True, 2, 2)
    assert (late.baseline.on_target, late.scenario.on_target) == (True, False)
    assert (late.baseline.delay_days, late.scenario.delay_days) == (0, 2)
    assert projects["PRJ-002"].affected is False


def test_member_load_per_day_and_per_week():
    members = by_key(RESULT.members, "resource_id")
    busy = members["RES-001"]
    assert busy.affected is True
    # Baseline dan skenario dibandingkan pada rentang yang sama: 05..13.
    assert [day.day for day in busy.days] == [d(5), d(6), d(7), d(8), d(9), d(12), d(13)]
    week_two = busy.days[-1]
    assert (week_two.baseline_load, week_two.scenario_load) == (0.0, 1.0)
    assert week_two.scenario_tasks == ["TSK-0002"]
    assert [(p.period_start, p.period_end) for p in busy.periods] == [
        (d(5), d(11)),
        (d(12), d(18)),
    ]
    assert (busy.periods[1].baseline_load, busy.periods[1].scenario_load) == (0.0, 1.0)
    assert busy.scenario_overloaded_days == 0
    assert members["RES-002"].affected is False


def test_summary_counts():
    summary = RESULT.summary
    assert (summary.affected_projects, summary.affected_tasks, summary.affected_members) == (
        1,
        2,
        1,
    )
    assert (summary.unscheduled_tasks, summary.overloaded_members) == (0, 0)


def test_result_survives_a_json_round_trip_for_storage():
    dumped = RESULT.model_dump(mode="json")
    assert SimulationResult.model_validate(dumped) == RESULT


def test_replication_adds_median_and_range_to_every_value():
    replicated = result(runs=20)
    assert replicated.replication.runs == 20
    for kpi in replicated.kpis:
        assert kpi.replication is not None
        assert kpi.replication.baseline.runs == 20
    task = by_key(replicated.tasks, "task_id")["TSK-0002"]
    finish = task.replication.scenario_finish
    assert finish.min <= finish.median <= finish.max
    project = by_key(replicated.projects, "project_id")["PRJ-001"]
    assert project.replication.scenario_completion.runs == 20
    member = by_key(replicated.members, "resource_id")["RES-001"]
    assert member.periods[0].replication.scenario.runs == 20
    # Masih dapat disimpan sebagai JSON.
    assert SimulationResult.model_validate(replicated.model_dump(mode="json")) == replicated
