"""SCRUM-28/SCRUM-29: isi layar hasil simulasi (KPI, proyek, task, beban anggota), tanpa basis data.

Baseline uji, skenario Task Overrun TSK-0001 3 -> 5 hari:

    PRJ-001 target Jumat 09                 baseline     skenario
        TSK-0001 BE 3 hari (RES-001)          05-07        05-09
        TSK-0002 BE 2 hari, hard, rencana 08  08-09        12-13
    PRJ-002: TSK-0101 BE 2 hari (RES-002)     05-06        05-06

KPI mengikuti modul Evaluasi (kapasitas referensi fixture 1.0, threshold 1.0):
    RLI RES-001 minggu 05: baseline 2 task = 2.0 (overload), skenario 1 task = 1.0.
    SV portofolio: baseline 0 (PRJ-001 tepat 09), skenario +2 (selesai 13).
"""

from datetime import datetime, timezone

from app.modules.evaluasi.evaluation import build_evaluation, evaluate
from app.modules.evaluasi.kpi_definitions import KPI_BY_KEY, KPI_KEYS, EvaluationConfig
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import TaskOverrunParams
from app.modules.simulasi.scenario_schemas import ScenarioSummary
from app.modules.simulasi.sim_engine import run_engine
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


def test_kpis_follow_the_evaluasi_definitions():
    kpis = by_key(RESULT.kpis, "key")
    assert list(kpis) == list(KPI_KEYS)
    for key, item in kpis.items():
        definition = KPI_BY_KEY[key]
        assert (item.fr, item.label, item.direction) == (
            definition.fr,
            definition.label,
            definition.direction,
        )
        assert item.tolerance == definition.tolerance
    assert RESULT.kpi_config == EvaluationConfig().resolved()


def test_kpis_compare_scenario_with_baseline():
    kpis = by_key(RESULT.kpis, "key")
    sv = kpis["schedule_variance"]
    # 09 -> 13: Senin 12 dan Selasa 13.
    assert (sv.baseline, sv.scenario, sv.delta, sv.status) == (0.0, 2.0, 2.0, "worse")
    on_time = kpis["on_time_probability"]
    assert (on_time.baseline, on_time.scenario, on_time.status) == (1.0, 0.0, "worse")
    rli = kpis["resource_load_index"]
    assert (rli.baseline, rli.scenario, rli.status) == (2.0, 1.0, "better")
    assert (kpis["overload_flags"].baseline, kpis["overload_flags"].scenario) == (1.0, 0.0)
    # TSK-0001 terlambat 2 hari dan diteruskan penuh (hard) ke TSK-0002.
    assert (kpis["cascade_delay_impact"].scenario, kpis["cascade_delay_impact"].status) == (
        2.0,
        "worse",
    )
    # (5 + 2 + 2) hari aktual / (3 + 2 + 2) hari estimasi baseline.
    assert kpis["estimate_accuracy_ratio"].scenario == round(9 / 7, 6)
    assert (RESULT.kpi_summary.better, RESULT.kpi_summary.worse) == (2, 5)


def test_kpis_are_identical_to_the_evaluasi_module():
    """Hasil simulasi dan evaluasi KPI dengan konfigurasi bawaan selalu sama."""
    for runs in (None, 20):
        simulated = result(runs=runs)
        out = evaluate(
            SNAPSHOT,
            Situation.TASK_OVERRUN,
            PARAMS,
            EvaluationConfig(),
            start_date=START,
            runs=runs,
            seed=3,
        )
        evaluated = build_evaluation(
            out, EvaluationConfig(), situation="task_overrun", snapshot_id=1
        )
        assert [k.model_dump() for k in simulated.kpis] == [k.model_dump() for k in evaluated.kpis]


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
    assert (late.baseline.schedule_variance, late.scenario.schedule_variance) == (0, 2)
    assert projects["PRJ-002"].scenario.schedule_variance < 0
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
    assert busy.scenario_over_capacity_days == 0
    # Overload mengikuti RLI mingguan KPI evaluasi, bukan beban harian.
    first, second = busy.periods
    assert (first.baseline_rli, first.scenario_rli) == (2.0, 1.0)
    assert (first.baseline_overloaded, first.overloaded) == (True, False)
    assert (second.baseline_rli, second.scenario_rli) == (0.0, 1.0)
    assert (busy.baseline_peak_rli, busy.scenario_peak_rli) == (2.0, 1.0)
    assert (busy.baseline_overloaded_weeks, busy.scenario_overloaded_weeks) == (1, 0)
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
        assert kpi.basis == "median"
        assert kpi.replication is not None and kpi.replication.runs == 20
        if kpi.key != "on_time_probability":
            assert kpi.replication.baseline.runs + kpi.replication.baseline.missing == 20
    task = by_key(replicated.tasks, "task_id")["TSK-0002"]
    finish = task.replication.scenario_finish
    assert finish.min <= finish.median <= finish.max
    project = by_key(replicated.projects, "project_id")["PRJ-001"]
    assert project.replication.scenario_completion.runs == 20
    assert project.replication.scenario_sv.runs == 20
    assert 0.0 <= project.replication.scenario_on_time_probability <= 1.0
    member = by_key(replicated.members, "resource_id")["RES-001"]
    assert member.periods[0].replication.scenario.runs == 20
    # Masih dapat disimpan sebagai JSON.
    assert SimulationResult.model_validate(replicated.model_dump(mode="json")) == replicated
