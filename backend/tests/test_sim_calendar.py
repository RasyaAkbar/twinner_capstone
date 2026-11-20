"""SCRUM-25: kalender kapasitas hari kerja per anggota, tanpa basis data.

Kalender uji (Januari 2026), simulasi mulai Senin 05:

    Sen 05  Sel 06  [Rab 07 libur]  Kam 08  Jum 09  [Sab 10, Min 11]  Sen 12 ... Jum 16

    RES-001 BE (capacity_reference 3.0)
        tiket high 05..06 (0.5/hari), hotfix 08 (1.0) + tiket low 08 (0.25)
        planned_leave 12..13 (terencana), sudden_leave 15 (tidak terencana)
    RES-002 TW (2.5), bergabung Kamis 08
    RES-003 BE (3.0): tiket medium belum selesai sejak 02 (dianggap selesai 06),
        rotation_in 12..16 (kedatangan, bukan ketidakhadiran)
"""

from copy import deepcopy

import pytest

from app.modules.simulasi.sim_calendar import LEFT, NOT_JOINED, OFF, CapacityCalendar
from app.modules.simulasi.sim_scheduler import daily_load
from app.modules.simulasi.sim_world import (
    Absence,
    Mentoring,
    NonProjectItem,
    Ramp,
    SimResource,
)
from tests.sim_fixtures import absence, d, holiday, resource, role, workload, world

WORLD = world(
    holidays=[holiday("2026-01-07")],
    roles=[role("ROLE-BE", 3.0), role("ROLE-TW", 2.5)],
    resources=[
        resource("RES-001"),
        resource("RES-002", "ROLE-TW", join="2026-01-08"),
        resource("RES-003"),
    ],
    availability=[
        absence("AVL-001", "RES-001", "2026-01-12", "2026-01-13", "planned_leave", True),
        absence("AVL-002", "RES-001", "2026-01-15", "2026-01-15", "sudden_leave", False),
        absence("AVL-003", "RES-003", "2026-01-12", "2026-01-16", "rotation_in", False),
    ],
    workload_items=[
        workload("WLI-0001", "RES-001", "2026-01-05", "2026-01-05", "2026-01-06", "high"),
        workload("WLI-0002", "RES-001", "2026-01-08", "2026-01-08", "2026-01-08", "critical", True),
        workload("WLI-0003", "RES-001", "2026-01-08", "2026-01-08", "2026-01-08", "low"),
        workload("WLI-0004", "RES-003", "2026-01-02", "2026-01-02", None, "medium"),
        workload("WLI-0005", "RES-003", "2025-12-01", "2025-12-01", "2025-12-02", "medium"),
    ],
)
CALENDAR = CapacityCalendar(WORLD)


def test_only_weekdays_outside_holidays_are_working_days():
    assert CALENDAR.days[:7] == [d(5), d(6), d(8), d(9), d(12), d(13), d(14)]
    for day in (d(7), d(10), d(11)):
        assert CALENDAR.day("RES-001", day) == OFF
        assert CALENDAR.free("RES-001", day) == 0


def test_full_capacity_is_the_role_capacity_reference():
    capacity = CALENDAR.day("RES-001", d(9))
    assert (capacity.availability, capacity.capacity, capacity.free) == (1.0, 3.0, 3.0)
    assert CALENDAR.day("RES-002", d(9)).capacity == 2.5


@pytest.mark.parametrize(
    ("day", "absence_type", "planned"),
    [
        (d(12), "planned_leave", True),
        (d(13), "planned_leave", True),
        (d(15), "sudden_leave", False),
    ],
)
def test_planned_and_unplanned_absences_have_zero_capacity(day, absence_type, planned):
    capacity = CALENDAR.day("RES-001", day)
    assert (capacity.capacity, capacity.free, capacity.working) == (0.0, 0.0, False)
    assert (capacity.absence, capacity.planned) == (absence_type, planned)


def test_day_between_absences_is_available_again():
    assert CALENDAR.day("RES-001", d(14)).capacity == 3.0


def test_rotation_in_is_an_arrival_not_an_absence():
    capacity = CALENDAR.day("RES-003", d(12))
    assert (capacity.capacity, capacity.absence) == (3.0, None)


def test_no_capacity_before_join_date():
    assert CALENDAR.day("RES-002", d(6)).absence == NOT_JOINED
    assert CALENDAR.day("RES-002", d(6)).capacity == 0
    assert CALENDAR.day("RES-002", d(8)).capacity == 2.5


def test_non_project_workload_reduces_free_capacity():
    # Tiket high = 0.5 hari: bebas 3.0 x (1 - 0.5) = 1.5.
    for day in (d(5), d(6)):
        capacity = CALENDAR.day("RES-001", day)
        assert (capacity.non_project, capacity.free) == (0.5, 1.5)
    # Hotfix (1.0) + tiket low (0.25): seluruh hari habis dan beban 125%.
    hotfix_day = CALENDAR.day("RES-001", d(8))
    assert (hotfix_day.non_project, hotfix_day.free) == (1.25, 0.0)
    assert daily_load(hotfix_day, 0.0) == 1.25
    assert CALENDAR.day("RES-001", d(9)).non_project == 0


def test_open_item_is_assumed_resolved_after_two_working_days():
    assert [CALENDAR.day("RES-003", day).non_project for day in (d(5), d(6), d(8))] == [
        0.25,
        0.25,
        0.0,
    ]


def test_items_resolved_before_the_start_are_ignored():
    assert not any("WLI-0005" in item.label for item in WORLD.items)


def test_total_free_capacity_over_a_period():
    # 1.5 + 1.5 + 0 (hotfix) + 3.0; Rabu 07 libur.
    assert CALENDAR.total_free("RES-001", d(5), d(9)) == 6.0


def _with_new_member() -> CapacityCalendar:
    changed = deepcopy(WORLD)
    changed.resources["NEW-001"] = SimResource(
        resource_id="NEW-001",
        name="Anggota baru",
        primary_role_id="ROLE-BE",
        secondary_role_ids=(),
        capacity_reference=3.0,
        join_date=d(5),
        ramp=Ramp(d(5), 4, 0.5),
    )
    changed.mentoring.append(Mentoring("RES-003", "NEW-001", 0.2))
    return CapacityCalendar(changed)


def test_onboarding_ramps_capacity_up_linearly():
    calendar = _with_new_member()
    # Hari kerja ke-0..3 onboarding: 0.5 + 0.5 x k/4; Rabu 07 libur tidak dihitung.
    expected = {d(5): 0.5, d(6): 0.625, d(8): 0.75, d(9): 0.875, d(12): 1.0}
    for day, factor in expected.items():
        capacity = calendar.day("NEW-001", day)
        assert capacity.availability == pytest.approx(factor)
        assert capacity.capacity == pytest.approx(3.0 * factor)


def test_mentor_spends_part_of_the_day_during_onboarding():
    calendar = _with_new_member()
    # 05: tiket medium 0.25 + mentoring 0.2 -> bebas 3.0 x 0.55.
    first = calendar.day("RES-003", d(5))
    assert (first.mentoring, first.free) == (0.2, pytest.approx(1.65))
    assert calendar.day("RES-003", d(9)).free == pytest.approx(2.4)
    # Onboarding selesai setelah 4 hari kerja.
    assert calendar.day("RES-003", d(12)).mentoring == 0


def test_bug_fix_item_uses_the_members_own_working_days():
    changed = deepcopy(WORLD)
    changed.resources["RES-004"] = deepcopy(changed.resources["RES-003"])
    changed.resources["RES-004"].resource_id = "RES-004"
    changed.absences.append(Absence("RES-004", d(6), d(6), "sudden_leave", False))
    changed.items.append(
        NonProjectItem("RES-004", start=d(5), share=1.0, label="Perbaikan bug", working_days=2.5)
    )
    calendar = CapacityCalendar(changed)
    # 2.5 hari: 05 penuh, 06 absen (dilewati), 07 libur, 08 penuh, 09 setengah.
    shares = [calendar.day("RES-004", day).non_project for day in (d(5), d(6), d(8), d(9), d(12))]
    assert shares == [1.0, 0.0, 1.0, 0.5, 0.0]


def test_member_who_left_has_no_capacity_from_the_leave_date():
    changed = deepcopy(WORLD)
    changed.resources["RES-003"].leave_date = d(9)
    calendar = CapacityCalendar(changed)
    assert calendar.day("RES-003", d(8)).capacity == 3.0
    assert calendar.day("RES-003", d(9)).absence == LEFT
    assert calendar.day("RES-003", d(12)).capacity == 0
