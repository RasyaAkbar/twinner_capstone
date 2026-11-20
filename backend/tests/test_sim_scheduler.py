"""SCRUM-26: penjadwal hari demi hari dengan propagasi keterlambatan dependency.

Setiap contoh dihitung manual (lihat komentar). Bawaan dari `sim_fixtures`: mulai Senin
2026-01-05 tanpa hari libur, kapasitas peran 1.0 dan bobot task 1.0, sehingga satu anggota
mengerjakan satu task dengan kecepatan satu hari effort per hari kerja.

    Sen 05  Sel 06  Rab 07  Kam 08  Jum 09  |  Sen 12  Sel 13  Rab 14
"""

import pytest

from app.modules.simulasi.sim_calendar import CapacityCalendar
from app.modules.simulasi.sim_scheduler import (
    TaskState,
    WarningCode,
    simulate,
)
from app.modules.simulasi.sim_world import Help, Release, SimResource
from tests.sim_fixtures import (
    absence,
    assignment,
    d,
    dependency,
    project,
    resource,
    role,
    task,
    workload,
    world,
)

ONE_MEMBER = [resource("RES-001")]
TWO_MEMBERS = [resource("RES-001"), resource("RES-002")]


def dates(result, task_id):
    outcome = result.tasks[task_id]
    return outcome.start, outcome.finish


# --- dependency --------------------------------------------------------------------------------


def test_hard_dependency_starts_the_working_day_after_the_predecessor_finishes():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 3), task("TSK-0002", 2)],
        task_dependencies=[dependency("DEP-001", "TSK-0002", "TSK-0001")],
        assignments=[
            assignment("ASG-0001", "TSK-0001", "RES-001"),
            assignment("ASG-0002", "TSK-0002", "RES-001"),
        ],
    )
    result = simulate(w)
    # TSK-0001: 05, 06, 07. TSK-0002 siap Kamis 08: 08, 09.
    assert dates(result, "TSK-0001") == (d(5), d(7))
    assert dates(result, "TSK-0002") == (d(8), d(9))
    assert result.tasks["TSK-0002"].dependency_ready == d(8)
    assert result.end_date == d(9)


def _chain(kind: str, factor: float, predecessor_days: float):
    """TSK-0001 (RES-001) <- TSK-0002 (RES-002, 2 hari) dengan dependency `kind`."""
    return world(
        resources=TWO_MEMBERS,
        tasks=[task("TSK-0001", predecessor_days), task("TSK-0002", 2)],
        task_dependencies=[dependency("DEP-001", "TSK-0002", "TSK-0001", kind, factor)],
        assignments=[
            assignment("ASG-0001", "TSK-0001", "RES-001"),
            assignment("ASG-0002", "TSK-0002", "RES-002"),
        ],
    )


@pytest.mark.parametrize(
    ("kind", "factor", "predecessor_days", "expected_start"),
    [
        # Soft 0.5 atas 4 hari: TSK-0001 selesai 2 hari pada akhir Selasa 06 -> mulai Rabu 07.
        ("soft", 0.5, 4, d(7)),
        # Soft 0.5 atas 6 hari: butuh 3 hari selesai (akhir Rabu 07) -> mulai Kamis 08.
        ("soft", 0.5, 6, d(8)),
        # Hard atas 4 hari: TSK-0001 selesai Kamis 08 -> mulai Jumat 09.
        ("hard", 1.0, 4, d(9)),
        # Hard atas 6 hari: TSK-0001 selesai Senin 12 -> mulai Selasa 13.
        ("hard", 1.0, 6, d(13)),
    ],
)
def test_dependent_start_follows_the_propagation_factor(
    kind, factor, predecessor_days, expected_start
):
    result = simulate(_chain(kind, factor, predecessor_days))
    assert result.tasks["TSK-0002"].start == expected_start
    assert result.tasks["TSK-0002"].dependency_ready == expected_start


def test_predecessor_overrun_passes_on_by_the_propagation_factor():
    # Pendahulu bertambah 2 hari kerja (4 -> 6, selesai 08 -> 12).
    soft = [simulate(_chain("soft", 0.5, days)).tasks["TSK-0002"].start for days in (4, 6)]
    hard = [simulate(_chain("hard", 1.0, days)).tasks["TSK-0002"].start for days in (4, 6)]
    # Soft 0.5 meneruskan 0.5 x 2 = 1 hari kerja (Rab 07 -> Kam 08) ...
    assert soft == [d(7), d(8)]
    # ... hard meneruskan seluruhnya, 2 hari kerja (Jum 09 -> Sel 13).
    assert hard == [d(9), d(13)]


# --- kapasitas ---------------------------------------------------------------------------------


def test_member_works_tasks_in_parallel_up_to_the_capacity_reference():
    w = world(
        roles=[role("ROLE-BE", 3.0)],
        resources=ONE_MEMBER,
        tasks=[task(f"TSK-000{i}", 2) for i in range(1, 5)],
        assignments=[assignment(f"ASG-000{i}", f"TSK-000{i}", "RES-001") for i in range(1, 5)],
    )
    result = simulate(w)
    # Bobot 1.0 x 3 task = kapasitas 3.0; task keempat menunggu kapasitas kosong (Rabu 07).
    assert [dates(result, f"TSK-000{i}") for i in range(1, 5)] == [
        (d(5), d(6)),
        (d(5), d(6)),
        (d(5), d(6)),
        (d(7), d(8)),
    ]
    days = {m.day: m for m in result.members["RES-001"]}
    assert days[d(5)].load == 1.0
    assert days[d(5)].tasks == ("TSK-0001", "TSK-0002", "TSK-0003")
    assert days[d(7)].load == pytest.approx(1 / 3)


def test_hotfix_preempts_the_member_and_overloads_the_day():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 2)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
        workload_items=[
            # Hotfix Selasa 06 (D-08 is_hotfix): seluruh kapasitas hari itu.
            workload(
                "WLI-0001", "RES-001", "2026-01-06", "2026-01-06", "2026-01-06", "critical", True
            )
        ],
    )
    result = simulate(w)
    # 05: 1 hari. 06: hotfix mengambil seluruh hari, task tidak maju. 07: selesai.
    assert dates(result, "TSK-0001") == (d(5), d(7))
    days = {m.day: m for m in result.members["RES-001"]}
    assert (days[d(5)].load, days[d(5)].overloaded) == (1.0, False)
    # Beban 06 = task 1.0/1.0 + hotfix 1.0.
    assert (days[d(6)].load, days[d(6)].overloaded) == (2.0, True)


def test_partial_non_project_load_slows_the_task():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 2)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
        workload_items=[
            workload("WLI-0001", "RES-001", "2026-01-05", "2026-01-05", "2026-01-05", "high")
        ],
    )
    result = simulate(w)
    # 05: tiket high memakai setengah hari -> maju 0.5. 06: 1.0 (sisa 0.5). 07: selesai.
    assert dates(result, "TSK-0001") == (d(5), d(7))
    assert result.members["RES-001"][0].load == 1.5


def test_tasks_wait_while_their_owner_is_absent():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 3)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
        availability=[absence("AVL-001", "RES-001", "2026-01-06", "2026-01-07")],
    )
    result = simulate(w)
    # 05 (sisa 2), absen 06-07, 08 (sisa 1), 09 selesai.
    assert dates(result, "TSK-0001") == (d(5), d(9))
    absent = {m.day: m for m in result.members["RES-001"]}[d(6)]
    assert (absent.load, absent.absence, absent.tasks) == (None, "planned_leave", ())


def test_task_is_not_started_before_its_planned_start():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 1, start="2026-01-07")],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    )
    assert dates(simulate(w), "TSK-0001") == (d(7), d(7))


def test_higher_priority_project_goes_first():
    w = world(
        projects=[project("PRJ-001", rank=2), project("PRJ-002", rank=1)],
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 1), task("TSK-0002", 1, project_id="PRJ-002")],
        assignments=[
            assignment("ASG-0001", "TSK-0001", "RES-001"),
            assignment("ASG-0002", "TSK-0002", "RES-001"),
        ],
    )
    result = simulate(w)
    assert dates(result, "TSK-0002") == (d(5), d(5))
    assert dates(result, "TSK-0001") == (d(6), d(6))


# --- pemilik task ------------------------------------------------------------------------------


def test_unassigned_task_goes_to_the_member_with_most_free_capacity():
    w = world(
        resources=TWO_MEMBERS,
        tasks=[task("TSK-0001", 3), task("TSK-0002", 2)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-002")],
    )
    result = simulate(w)
    # RES-002 penuh oleh TSK-0001; RES-001 menganggur dan mengambil TSK-0002.
    assert result.tasks["TSK-0002"].owner == "RES-001"
    assert dates(result, "TSK-0002") == (d(5), d(6))


def test_released_task_is_continued_by_another_member():
    w = world(
        resources=TWO_MEMBERS,
        tasks=[task("TSK-0001", 4)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    )
    w.tasks["TSK-0001"].releases.append(Release(d(7), "RES-001"))
    w.resources["RES-001"].excluded_projects["PRJ-001"] = d(7)
    result = simulate(w)
    # RES-001 mengerjakan 05-06 (sisa 2); RES-002 melanjutkan 07-08.
    assert dates(result, "TSK-0001") == (d(5), d(8))
    assert result.tasks["TSK-0001"].owner == "RES-002"
    assert {m.day: m.tasks for m in result.members["RES-002"]}[d(7)] == ("TSK-0001",)


def test_helper_takes_a_task_its_owner_has_no_capacity_for():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 2), task("TSK-0002", 2)],
        assignments=[
            assignment("ASG-0001", "TSK-0001", "RES-001"),
            assignment("ASG-0002", "TSK-0002", "RES-001"),
        ],
    )
    w.resources["NEW-001"] = SimResource(
        resource_id="NEW-001",
        name="Penolong",
        primary_role_id="ROLE-BE",
        secondary_role_ids=(),
        capacity_reference=1.0,
        join_date=d(5),
        helps=Help(d(5), None),
    )
    result = simulate(w)
    # Tanpa penolong TSK-0002 baru mulai Rabu 07.
    assert result.tasks["TSK-0002"].owner == "NEW-001"
    assert dates(result, "TSK-0002") == (d(5), d(6))


# --- blokir ------------------------------------------------------------------------------------


def test_blocked_task_is_held_and_the_owner_switches_to_an_independent_task():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 2), task("TSK-0002", 1, rank=2)],
        assignments=[
            assignment("ASG-0001", "TSK-0001", "RES-001"),
            assignment("ASG-0002", "TSK-0002", "RES-001"),
        ],
    )
    w.tasks["TSK-0001"].block_days = 2
    result = simulate(w)
    # TSK-0001 tertahan 05-06; anggota mengerjakan TSK-0002 pada 05; TSK-0001 07-08.
    assert dates(result, "TSK-0002") == (d(5), d(5))
    assert dates(result, "TSK-0001") == (d(7), d(8))


# --- status awal -------------------------------------------------------------------------------


def test_started_tasks_continue_with_their_remaining_work():
    w = world(
        resources=TWO_MEMBERS,
        tasks=[
            # Sudah 3 hari kerja (Rab 31 Des, Kam 01, Jum 02) dari 5 -> sisa 2.
            task(
                "TSK-0001",
                5,
                start="2025-12-31",
                status="in_progress",
                actual_start="2025-12-31",
            ),
            # in_qa: tinggal 0.5 hari.
            task("TSK-0002", 3, start="2025-12-29", status="in_qa", actual_start="2025-12-29"),
            task(
                "TSK-0003",
                2,
                start="2025-12-29",
                status="done",
                actual_start="2025-12-29",
                actual_finish="2025-12-30",
            ),
        ],
        assignments=[
            assignment("ASG-0001", "TSK-0001", "RES-001"),
            assignment("ASG-0002", "TSK-0002", "RES-002"),
            assignment("ASG-0003", "TSK-0003", "RES-002"),
        ],
    )
    result = simulate(w)
    assert dates(result, "TSK-0001") == (d(31, 12, 2025), d(6))
    assert dates(result, "TSK-0002") == (d(29, 12, 2025), d(5))
    done = result.tasks["TSK-0003"]
    assert done.state == TaskState.DONE
    assert (done.start, done.finish) == (d(29, 12, 2025), d(30, 12, 2025))


# --- tidak terjadwal ---------------------------------------------------------------------------


def test_task_without_a_member_of_its_role_is_unscheduled_and_the_run_continues():
    w = world(
        resources=ONE_MEMBER,
        tasks=[
            task("TSK-0001", 2, role_id="ROLE-QA"),
            task("TSK-0002", 1),
            task("TSK-0003", 1, rank=2),
        ],
        task_dependencies=[dependency("DEP-001", "TSK-0002", "TSK-0001")],
        assignments=[
            assignment("ASG-0002", "TSK-0002", "RES-001"),
            assignment("ASG-0003", "TSK-0003", "RES-001"),
        ],
    )
    result = simulate(w)
    assert result.tasks["TSK-0001"].state == TaskState.UNSCHEDULED
    assert result.tasks["TSK-0002"].state == TaskState.UNSCHEDULED
    assert [(x.code, x.task_id) for x in result.warnings] == [
        (WarningCode.NO_MEMBER_WITH_ROLE, "TSK-0001"),
        (WarningCode.WAITS_UNSCHEDULED, "TSK-0002"),
    ]
    assert "ROLE-QA" in result.tasks["TSK-0001"].reason
    # Task lain tetap dijadwalkan.
    assert dates(result, "TSK-0003") == (d(5), d(5))


def test_on_hold_and_dropped_tasks_are_not_scheduled():
    w = world(
        projects=[project("PRJ-001"), project("PRJ-002", rank=2, status="on_hold")],
        resources=ONE_MEMBER,
        tasks=[
            task("TSK-0001", 1, project_id="PRJ-002"),
            task("TSK-0002", 1, status="dropped"),
            task("TSK-0003", 1),
        ],
        task_dependencies=[dependency("DEP-001", "TSK-0003", "TSK-0001")],
        assignments=[assignment("ASG-0003", "TSK-0003", "RES-001")],
    )
    result = simulate(w)
    assert result.tasks["TSK-0001"].state == TaskState.ON_HOLD
    assert result.tasks["TSK-0002"].state == TaskState.DROPPED
    assert result.tasks["TSK-0003"].state == TaskState.UNSCHEDULED
    assert result.warnings[0].code == WarningCode.WAITS_ON_HOLD


def test_dropped_predecessor_does_not_hold_its_dependent():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 1, status="dropped"), task("TSK-0002", 1)],
        task_dependencies=[dependency("DEP-001", "TSK-0002", "TSK-0001")],
        assignments=[assignment("ASG-0002", "TSK-0002", "RES-001")],
    )
    assert dates(simulate(w), "TSK-0002") == (d(5), d(5))


def test_task_not_finished_within_the_horizon_is_unscheduled():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 10)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    )
    result = simulate(w, CapacityCalendar(w, end=d(9)))
    outcome = result.tasks["TSK-0001"]
    assert (outcome.state, outcome.start) == (TaskState.UNSCHEDULED, d(5))
    assert result.warnings[0].code == WarningCode.HORIZON


# --- keluaran ----------------------------------------------------------------------------------


def test_effort_override_replaces_the_estimate_unless_fixed():
    w = world(
        resources=ONE_MEMBER,
        tasks=[task("TSK-0001", 2)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    )
    assert dates(simulate(w, efforts={"TSK-0001": 4}), "TSK-0001") == (d(5), d(8))
    w.tasks["TSK-0001"].effort_fixed = True
    assert dates(simulate(w, efforts={"TSK-0001": 4}), "TSK-0001") == (d(5), d(6))


def test_daily_load_is_recorded_for_every_member_and_working_day():
    w = world(
        resources=TWO_MEMBERS,
        tasks=[task("TSK-0001", 3)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    )
    result = simulate(w)
    assert [m.day for m in result.members["RES-001"]] == [d(5), d(6), d(7)]
    assert [m.load for m in result.members["RES-001"]] == [1.0, 1.0, 1.0]
    assert [m.load for m in result.members["RES-002"]] == [0.0, 0.0, 0.0]


def test_same_input_gives_the_same_schedule():
    w = _chain("soft", 0.5, 4)
    first, second = simulate(w), simulate(w)
    assert first.tasks == second.tasks
    assert first.members == second.members


def test_empty_world_ends_on_the_start_date():
    result = simulate(world())
    assert (result.tasks, result.end_date) == ({}, d(5))
