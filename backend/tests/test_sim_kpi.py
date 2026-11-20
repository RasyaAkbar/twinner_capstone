"""KPI hasil simulasi dan perbandingannya (dipakai UC-03/SCRUM-28), dengan contoh hitungan manual.

    PRJ-001 target Rabu 07: TSK-0001 3 hari 05-07, TSK-0002 hard <- TSK-0001, 08-09
        -> selesai 09, terlambat 2 hari kerja (08, 09)
    PRJ-002 target 30: TSK-0101 2 hari 05-06 -> tepat waktu
    PRJ-003 target 30: TSK-0201 butuh ROLE-QA yang tidak dimiliki siapa pun -> tidak terjadwal
"""

from datetime import date

import pytest

from app.modules.simulasi.sim_calendar import CapacityCalendar
from app.modules.simulasi.sim_kpi import (
    KPI_BY_KEY,
    KPI_DEFINITIONS,
    KpiStatus,
    calculate_kpis,
    compare_values,
    delay_after,
    member_loads,
    project_outcomes,
    signed_working_days,
    weekly_loads,
)
from app.modules.simulasi.sim_scheduler import MemberDay, simulate
from tests.sim_fixtures import assignment, d, dependency, project, resource, task, world

WORLD = world(
    projects=[
        project("PRJ-001", target="2026-01-07"),
        project("PRJ-002", rank=2),
        project("PRJ-003", rank=3),
    ],
    resources=[resource("RES-001"), resource("RES-002")],
    tasks=[
        task("TSK-0001", 3),
        task("TSK-0002", 2),
        task("TSK-0101", 2, project_id="PRJ-002"),
        task("TSK-0201", 1, project_id="PRJ-003", role_id="ROLE-QA"),
    ],
    task_dependencies=[dependency("DEP-001", "TSK-0002", "TSK-0001")],
    assignments=[
        assignment("ASG-0001", "TSK-0001", "RES-001"),
        assignment("ASG-0002", "TSK-0002", "RES-001"),
        assignment("ASG-0003", "TSK-0101", "RES-002"),
    ],
)
RESULT = simulate(WORLD)
CALENDAR = CapacityCalendar(WORLD)
HOLIDAYS: frozenset[date] = frozenset()


@pytest.mark.parametrize(
    ("earlier", "later", "expected"),
    [
        (d(5), d(9), 4),
        (d(9), d(5), -4),
        # Jumat ke Senin: satu hari kerja.
        (d(9), d(12), 1),
        (d(7), d(7), 0),
    ],
)
def test_signed_working_days(earlier, later, expected):
    assert signed_working_days(earlier, later, HOLIDAYS) == expected


def test_signed_working_days_skips_holidays():
    assert signed_working_days(d(5), d(9), frozenset({d(7)})) == 3


def test_delay_counts_working_days_after_the_target():
    # Target Jumat 16, selesai Selasa 20: Senin 19 dan Selasa 20.
    assert delay_after(d(16), d(20), HOLIDAYS) == 2
    assert delay_after(d(16), d(16), HOLIDAYS) == 0
    assert delay_after(d(16), d(12), HOLIDAYS) == 0


def test_project_outcomes():
    projects = project_outcomes(WORLD, RESULT)
    late = projects["PRJ-001"]
    assert (late.completion, late.delay_days, late.on_target, late.task_count) == (
        d(9),
        2,
        False,
        2,
    )
    on_time = projects["PRJ-002"]
    assert (on_time.completion, on_time.delay_days, on_time.on_target) == (d(6), 0, True)
    unscheduled = projects["PRJ-003"]
    assert (unscheduled.completion, unscheduled.unscheduled, unscheduled.on_target) == (
        None,
        1,
        False,
    )


def test_kpi_values_from_the_simulation_output():
    loads = member_loads(WORLD, RESULT, CALENDAR, RESULT.end_date)
    kpis = calculate_kpis(WORLD, RESULT, loads)
    assert kpis == {
        "completion_date": d(9),
        "total_delay_days": 2.0,
        # PRJ-001 terlambat dan PRJ-003 punya task tidak terjadwal.
        "projects_missing_target": 2.0,
        # TSK-0002 rencana mulai 05, dependency baru terpenuhi 08.
        "dependency_delayed_tasks": 1.0,
        "peak_load": 1.0,
        "overloaded_member_days": 0.0,
        "unscheduled_tasks": 1.0,
        "dropped_tasks": 0.0,
    }
    assert list(kpis) == [definition.key for definition in KPI_DEFINITIONS]


def test_member_loads_extend_to_a_common_end_date_with_non_project_load_only():
    loads = member_loads(WORLD, RESULT, CALENDAR, d(13))
    assert [m.day for m in loads["RES-001"]] == [d(5), d(6), d(7), d(8), d(9), d(12), d(13)]
    assert [m.load for m in loads["RES-001"]] == [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0]
    assert [m.load for m in loads["RES-002"]][:3] == [1.0, 1.0, 0.0]


def test_every_kpi_is_lower_is_better_with_a_definition():
    for definition in KPI_DEFINITIONS:
        assert definition.better == "lower"
        assert definition.definition and definition.formula
    assert KPI_BY_KEY["completion_date"].unit == "date"


@pytest.mark.parametrize(
    ("key", "baseline", "scenario", "delta", "status"),
    [
        ("completion_date", d(5), d(9), 4.0, KpiStatus.WORSE),
        ("completion_date", d(9), d(5), -4.0, KpiStatus.BETTER),
        ("completion_date", d(9), d(9), 0.0, KpiStatus.SAME),
        # Tanpa proyeksi selesai di skenario lebih buruk; di keduanya sama.
        ("completion_date", d(9), None, None, KpiStatus.WORSE),
        ("completion_date", None, d(9), None, KpiStatus.BETTER),
        ("completion_date", None, None, None, KpiStatus.SAME),
        ("total_delay_days", 2.0, 5.0, 3.0, KpiStatus.WORSE),
        ("unscheduled_tasks", 3.0, 1.0, -2.0, KpiStatus.BETTER),
        ("peak_load", 0.8, 0.8, 0.0, KpiStatus.SAME),
    ],
)
def test_compare_gives_delta_and_status(key, baseline, scenario, delta, status):
    assert compare_values(KPI_BY_KEY[key], baseline, scenario, HOLIDAYS) == (delta, status)


def _day(day: date, load: float | None) -> MemberDay:
    return MemberDay(day, 1.0, 1.0, 0.0, 0.0, 0.0, load, None, ())


def test_weekly_loads_average_working_days_and_count_overloaded_days():
    days = [
        _day(d(5), 1.0),
        _day(d(6), 1.0),
        _day(d(7), None),
        _day(d(8), 0.5),
        _day(d(9), 2.0),
        _day(d(12), 0.0),
    ]
    weeks = weekly_loads(days)
    first = weeks[d(5)]
    # (1.0 + 1.0 + 0.5 + 2.0) / 4 hari kerja; hari absen tidak dihitung.
    assert (first.average, first.peak, first.overloaded_days, first.working_days) == (
        1.125,
        2.0,
        1,
        4,
    )
    assert weeks[d(12)].average == 0.0
