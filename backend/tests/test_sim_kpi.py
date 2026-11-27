"""Keluaran simulasi bersama (UC-03/SCRUM-28) untuk KPI evaluasi, dengan contoh hitungan manual.

PRJ-001 target Rabu 07: TSK-0001 3 hari 05-07, TSK-0002 hard <- TSK-0001, 08-09
    -> selesai 09, terlambat 2 hari kerja (08, 09)
PRJ-002 target 30: TSK-0101 2 hari 05-06 -> tepat waktu
PRJ-003 target 30: TSK-0201 butuh ROLE-QA yang tidak dimiliki siapa pun -> tidak terjadwal
"""

from datetime import date

import pytest

from app.modules.evaluasi.kpi_calculator import schedule_variances
from app.modules.simulasi import sim_kpi
from app.modules.simulasi.sim_calendar import CapacityCalendar
from app.modules.simulasi.sim_kpi import (
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


def test_project_delay_and_on_target_agree_with_schedule_variance():
    """Keterlambatan proyek di layar hasil simulasi = max(SV, 0) KPI evaluasi (FR-04.1)."""
    variances = schedule_variances(WORLD, RESULT)
    assert variances["PRJ-001"] == 2
    assert variances["PRJ-002"] < 0
    # Tidak dapat diluncurkan: SV kosong.
    assert variances["PRJ-003"] is None
    for project_id, outcome in project_outcomes(WORLD, RESULT).items():
        sv = variances[project_id]
        if sv is None:
            assert not outcome.on_target
            continue
        assert outcome.delay_days == max(sv, 0)
        assert outcome.on_target == (sv <= 0)


def test_kpis_are_only_defined_by_the_evaluasi_module():
    for name in ("KPI_DEFINITIONS", "KPI_BY_KEY", "calculate_kpis", "compare_values"):
        assert not hasattr(sim_kpi, name)


def test_member_loads_extend_to_a_common_end_date_with_non_project_load_only():
    loads = member_loads(WORLD, RESULT, CALENDAR, d(13))
    assert [m.day for m in loads["RES-001"]] == [d(5), d(6), d(7), d(8), d(9), d(12), d(13)]
    assert [m.load for m in loads["RES-001"]] == [1.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0]
    assert [m.load for m in loads["RES-002"]][:3] == [1.0, 1.0, 0.0]


def _day(day: date, load: float | None) -> MemberDay:
    return MemberDay(day, 1.0, 1.0, 0.0, 0.0, 0.0, load, None, ())


def test_weekly_loads_average_working_days_and_count_over_capacity_days():
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
    assert (first.average, first.peak, first.over_capacity_days, first.working_days) == (
        1.125,
        2.0,
        1,
        4,
    )
    assert weeks[d(12)].average == 0.0
