"""SCRUM-24: aturan perubahan skenario untuk delapan situasi keputusan, tanpa basis data.

Baseline uji (kapasitas 1.0, bobot 1.0; satu anggota satu task pada satu waktu):

    PRJ-001 prioritas 1, target 2026-01-16
        TSK-0001 BE 3 hari (RES-001)          05-07
        TSK-0002 BE 2 hari, hard <- TSK-0001   08-09  (RES-001, rencana mulai 08)
        TSK-0003 FE 2 hari (RES-003)          05-06
        TSK-0004 BE 1 hari, prioritas task 2  12     (RES-002, rencana mulai 12)
    PRJ-002 prioritas 2: TSK-0101 BE 2 hari (RES-002)   05-06
    PRJ-003 on hold, prioritas 3: TSK-0201 BE 1 hari (RES-002), on hold

    Sen 05  Sel 06  Rab 07  Kam 08  Jum 09  |  Sen 12  Sel 13
"""

from copy import deepcopy

import pytest

from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import SITUATIONS
from app.modules.simulasi.sim_rules import RULES, apply_scenario, infer_complexity
from app.modules.simulasi.sim_scheduler import TaskState, WarningCode, simulate
from app.modules.simulasi.sim_world import Help, Mentoring, Ramp, Release
from tests.sim_fixtures import (
    assignment,
    complexity,
    d,
    dependency,
    project,
    resource,
    task,
    world,
)

BASE = world(
    projects=[
        project("PRJ-001", rank=1, target="2026-01-16"),
        project("PRJ-002", rank=2, target="2026-01-30"),
        project("PRJ-003", rank=3, status="on_hold", target="2026-01-30"),
    ],
    resources=[resource("RES-001"), resource("RES-002"), resource("RES-003", "ROLE-FE")],
    tasks=[
        task("TSK-0001", 3),
        task("TSK-0002", 2, start="2026-01-08"),
        task("TSK-0003", 2, role_id="ROLE-FE"),
        task("TSK-0004", 1, start="2026-01-12", rank=2),
        task("TSK-0101", 2, project_id="PRJ-002"),
        task("TSK-0201", 1, project_id="PRJ-003", status="on_hold"),
    ],
    task_dependencies=[dependency("DEP-001", "TSK-0002", "TSK-0001")],
    assignments=[
        assignment("ASG-0001", "TSK-0001", "RES-001"),
        assignment("ASG-0002", "TSK-0002", "RES-001"),
        assignment("ASG-0003", "TSK-0003", "RES-003", "ROLE-FE"),
        assignment("ASG-0004", "TSK-0004", "RES-002"),
        assignment("ASG-0005", "TSK-0101", "RES-002"),
        assignment("ASG-0006", "TSK-0201", "RES-002"),
    ],
)

VALID_PARAMS = {
    Situation.CHANGE_REQUEST: {
        "project_id": "PRJ-001",
        "added_tasks": [
            {"task_name": "Ekspor laporan", "required_role_id": "ROLE-BE", "estimated_days": 2}
        ],
        "removed_task_ids": ["TSK-0003"],
        "affected_period": {"start": "2026-01-05", "end": "2026-01-09"},
        "target_date": "2026-01-23",
    },
    Situation.MEMBER_ABSENCE: {
        "resource_id": "RES-001",
        "absence_type": "sudden_leave",
        "absence_period": {"start": "2026-01-06", "end": "2026-01-07"},
    },
    Situation.NEW_MEMBER: {
        "role_id": "ROLE-BE",
        "seniority": "junior",
        "join_date": "2026-01-05",
        "onboarding_weeks": 1,
        "initial_capacity": 0.5,
        "mentor_resource_id": "RES-002",
    },
    Situation.MEMBER_ROTATION: {
        "resource_id": "RES-001",
        "origin_project_id": "PRJ-001",
        "destination_project_id": "PRJ-002",
        "transfer_date": "2026-01-07",
    },
    Situation.TASK_OVERRUN: {
        "task_id": "TSK-0001",
        "original_days": 3,
        "revised_days": 5,
        "remaining_days": 5,
    },
    Situation.TASK_BLOCKER: {
        "task_id": "TSK-0002",
        "blocking_task_id": "TSK-0001",
        "blocking_days": 2,
        "independent_task_ids": ["TSK-0004"],
    },
    Situation.PRIORITY_CHANGE: {
        "project_id": "PRJ-003",
        "new_priority_rank": 1,
        "estimated_days": 3,
        "target_date": "2026-01-23",
    },
    Situation.CRITICAL_BUG: {
        "severity": "critical",
        "fixing_days": 2,
        "resource_id": "RES-001",
        "affected_task_ids": ["TSK-0002"],
    },
}


def apply(situation: Situation, base=BASE, **changes):
    params = SITUATIONS[situation].model.model_validate({**VALID_PARAMS[situation], **changes})
    return apply_scenario(base, situation, params)


def dates(result, task_id):
    outcome = result.tasks[task_id]
    return outcome.start, outcome.finish


def test_baseline_schedule_of_the_fixture():
    result = simulate(BASE)
    assert dates(result, "TSK-0001") == (d(5), d(7))
    assert dates(result, "TSK-0002") == (d(8), d(9))
    assert dates(result, "TSK-0003") == (d(5), d(6))
    assert dates(result, "TSK-0004") == (d(12), d(12))
    assert dates(result, "TSK-0101") == (d(5), d(6))
    assert result.tasks["TSK-0201"].state == TaskState.ON_HOLD


def test_one_rule_per_situation():
    assert set(RULES) == set(Situation)


@pytest.mark.parametrize("situation", list(Situation))
def test_rules_work_on_a_deep_copy_and_never_change_the_baseline(situation):
    before = deepcopy(BASE)
    applied = apply(situation)
    assert BASE == before
    assert applied.world is not BASE
    assert applied.world != BASE
    assert applied.notes


# --- 1. Change Request ---------------------------------------------------------------------


def test_change_request_removes_and_adds_tasks_and_extends_the_target():
    applied = apply(Situation.CHANGE_REQUEST)
    w = applied.world
    removed = w.tasks["TSK-0003"]
    assert (removed.status, removed.removed) == ("dropped", True)

    added = w.tasks["CR-001"]
    assert (added.project_id, added.required_role_id, added.estimated_days) == (
        "PRJ-001",
        "ROLE-BE",
        2,
    )
    assert (added.earliest_start, added.planned_finish, added.priority_rank) == (d(5), d(23), 1)
    assert (added.effort_fixed, added.added, added.owner, added.complexity_id) == (
        True,
        True,
        None,
        "CX-M",
    )
    # Target 16 -> 23: Senin 19 s.d. Jumat 23 = 5 hari kerja perpanjangan.
    assert w.projects["PRJ-001"].target_launch_date == d(23)
    assert any("diundur 5 hari kerja" in note for note in applied.notes)
    assert applied.targets.tasks == {"TSK-0003", "CR-001"}
    assert applied.targets.projects == {"PRJ-001"}


def test_change_request_schedule():
    result = simulate(apply(Situation.CHANGE_REQUEST).world)
    assert result.tasks["TSK-0003"].state == TaskState.DROPPED
    # CR-001 di pool; anggota BE baru longgar Rabu 07 (RES-002 selesai TSK-0101 Selasa 06).
    assert dates(result, "CR-001") == (d(7), d(8))
    assert result.tasks["CR-001"].owner == "RES-002"


def test_change_request_keeps_a_later_target():
    applied = apply(Situation.CHANGE_REQUEST, target_date="2026-01-12")
    assert applied.world.projects["PRJ-001"].target_launch_date == d(16)


# --- 2. Member Absence ---------------------------------------------------------------------


def test_member_absence_adds_an_absence_and_targets_the_members_tasks():
    applied = apply(Situation.MEMBER_ABSENCE)
    absence = applied.world.absences[-1]
    assert (absence.resource_id, absence.start, absence.end) == ("RES-001", d(6), d(7))
    assert (absence.absence_type, absence.planned) == ("sudden_leave", False)
    # Kosong berarti semua task terbuka anggota itu.
    assert applied.targets.tasks == {"TSK-0001", "TSK-0002"}
    assert applied.targets.resources == {"RES-001"}


def test_planned_leave_is_known_at_planning():
    applied = apply(Situation.MEMBER_ABSENCE, absence_type="planned_leave")
    assert applied.world.absences[-1].planned is True


def test_member_absence_delays_the_members_tasks_and_their_dependents():
    result = simulate(apply(Situation.MEMBER_ABSENCE).world)
    # TSK-0001: 05, absen 06-07, 08, 09. TSK-0002 menunggu: 12-13 (baseline 08-09).
    assert dates(result, "TSK-0001") == (d(5), d(9))
    assert dates(result, "TSK-0002") == (d(12), d(13))


def test_member_absence_with_selected_tasks_targets_only_those():
    applied = apply(Situation.MEMBER_ABSENCE, affected_task_ids=["TSK-0002"])
    assert applied.targets.tasks == {"TSK-0002"}


# --- 3. New Member -------------------------------------------------------------------------

NEW_MEMBER_BASE = world(
    resources=[resource("RES-001"), resource("RES-002")],
    tasks=[task("TSK-0001", 2), task("TSK-0002", 2, rank=2), task("TSK-0003", 2)],
    assignments=[
        assignment("ASG-0001", "TSK-0001", "RES-001"),
        assignment("ASG-0002", "TSK-0002", "RES-001"),
        assignment("ASG-0003", "TSK-0003", "RES-002"),
    ],
)


def test_new_member_is_added_with_onboarding_ramp_and_mentor():
    applied = apply(Situation.NEW_MEMBER)
    member = applied.world.resources["NEW-001"]
    assert (member.primary_role_id, member.capacity_reference, member.join_date) == (
        "ROLE-BE",
        1.0,
        d(5),
    )
    # 1 minggu = 5 hari kerja onboarding, mulai 50%.
    assert member.ramp == Ramp(d(5), 5, 0.5)
    assert member.helps == Help(d(5), None)
    assert member.added is True
    # Tanpa mentor_share dipakai bawaan 20%.
    assert applied.world.mentoring == [Mentoring("RES-002", "NEW-001", 0.2)]
    assert applied.targets.resources == {"NEW-001", "RES-002"}


def test_new_member_at_full_capacity_takes_a_waiting_task():
    baseline = simulate(NEW_MEMBER_BASE)
    # Tanpa anggota baru TSK-0002 menunggu RES-001: 07-08.
    assert dates(baseline, "TSK-0002") == (d(7), d(8))
    applied = apply(
        Situation.NEW_MEMBER,
        base=NEW_MEMBER_BASE,
        initial_capacity=1,
        mentor_resource_id=None,
    )
    result = simulate(applied.world)
    assert result.tasks["TSK-0002"].owner == "NEW-001"
    assert dates(result, "TSK-0002") == (d(5), d(6))


def test_new_member_ramp_up_and_mentor_time_slow_work_down():
    result = simulate(apply(Situation.NEW_MEMBER, base=NEW_MEMBER_BASE).world)
    # NEW-001 maju 0.5, 0.6, 0.7, 0.8 per hari (kapasitas naik 10% per hari kerja).
    assert result.tasks["TSK-0002"].owner == "NEW-001"
    assert dates(result, "TSK-0002") == (d(5), d(8))
    # Mentor RES-002 hanya punya 80% kapasitas: TSK-0003 maju 0.8 per hari, selesai 07
    # (baseline 06).
    assert dates(result, "TSK-0003") == (d(5), d(7))


# --- 4. Member Rotation or Withdrawal -------------------------------------------------------


def test_rotation_releases_origin_assignments_and_adds_capacity_to_the_destination():
    applied = apply(Situation.MEMBER_ROTATION)
    w = applied.world
    member = w.resources["RES-001"]
    assert member.excluded_projects == {"PRJ-001": d(7)}
    assert member.helps == Help(d(7), "PRJ-002")
    assert member.leave_date is None
    for task_id in ("TSK-0001", "TSK-0002"):
        assert w.tasks[task_id].releases == [Release(d(7), "RES-001")]
    assert applied.targets.projects == {"PRJ-001", "PRJ-002"}


def test_rotated_members_tasks_are_continued_by_a_colleague():
    result = simulate(apply(Situation.MEMBER_ROTATION).world)
    # RES-001 mengerjakan TSK-0001 05-06; RES-002 melanjutkan 07, lalu TSK-0002 08-09.
    assert dates(result, "TSK-0001") == (d(5), d(7))
    assert dates(result, "TSK-0002") == (d(8), d(9))
    assert result.tasks["TSK-0001"].owner == "RES-002"
    assert result.tasks["TSK-0002"].owner == "RES-002"


def test_rotation_with_selected_assignments_releases_only_those():
    applied = apply(Situation.MEMBER_ROTATION, existing_assignment_ids=["ASG-0002"])
    assert applied.world.tasks["TSK-0001"].releases == []
    assert applied.world.tasks["TSK-0002"].releases == [Release(d(7), "RES-001")]


def test_withdrawal_of_the_only_member_with_a_role_leaves_the_task_unscheduled():
    applied = apply(
        Situation.MEMBER_ROTATION,
        resource_id="RES-003",
        destination_project_id=None,
        transfer_date="2026-01-05",
    )
    assert applied.world.resources["RES-003"].leave_date == d(5)
    result = simulate(applied.world)
    assert result.tasks["TSK-0003"].state == TaskState.UNSCHEDULED
    assert [(w.code, w.task_id) for w in result.warnings] == [
        (WarningCode.NO_MEMBER_WITH_ROLE, "TSK-0003")
    ]
    # Simulasi tetap berjalan untuk task lain.
    assert dates(result, "TSK-0002") == (d(8), d(9))


# --- 5. Task Overrun -----------------------------------------------------------------------


def test_task_overrun_replaces_the_estimate_and_remaining_work():
    applied = apply(Situation.TASK_OVERRUN)
    overrun = applied.world.tasks["TSK-0001"]
    assert (overrun.estimated_days, overrun.remaining_days, overrun.effort_fixed) == (5, 5, True)
    result = simulate(applied.world)
    # 5 hari: 05-09; dependency hard meneruskan 2 hari: TSK-0002 12-13.
    assert dates(result, "TSK-0001") == (d(5), d(9))
    assert dates(result, "TSK-0002") == (d(12), d(13))


# --- 6. Task Blocker -----------------------------------------------------------------------


def test_task_blocker_holds_the_task_and_switches_the_owner_to_independent_tasks():
    applied = apply(Situation.TASK_BLOCKER)
    w = applied.world
    assert w.tasks["TSK-0002"].block_days == 2
    independent = w.tasks["TSK-0004"]
    assert (independent.owner, independent.earliest_start) == ("RES-001", d(5))

    result = simulate(w)
    # TSK-0002 siap Kamis 08 lalu tertahan 08-09; RES-001 mengerjakan TSK-0004 pada 08.
    assert dates(result, "TSK-0004") == (d(8), d(8))
    assert result.tasks["TSK-0004"].owner == "RES-001"
    assert dates(result, "TSK-0002") == (d(12), d(13))


def test_fractional_blocking_days_round_up_to_whole_working_days():
    applied = apply(Situation.TASK_BLOCKER, blocking_days=1.5)
    assert applied.world.tasks["TSK-0002"].block_days == 2


# --- 7. Priority Change --------------------------------------------------------------------


def test_priority_change_reactivates_and_reranks_projects():
    applied = apply(Situation.PRIORITY_CHANGE)
    w = applied.world
    assert w.projects["PRJ-003"].status == "development"
    ranks = {pid: p.priority_rank for pid, p in w.projects.items()}
    assert ranks == {"PRJ-003": 1, "PRJ-001": 2, "PRJ-002": 3}
    reactivated = w.tasks["TSK-0201"]
    assert (reactivated.status, reactivated.estimated_days, reactivated.effort_fixed) == (
        "not_started",
        3,
        True,
    )
    assert w.projects["PRJ-003"].target_launch_date == d(23)
    assert applied.targets.tasks == {"TSK-0201"}


def test_priority_change_moves_the_project_ahead_in_the_queue():
    result = simulate(apply(Situation.PRIORITY_CHANGE).world)
    # RES-002 mendahulukan TSK-0201 (3 hari, 05-07); TSK-0101 bergeser ke 08-09.
    assert dates(result, "TSK-0201") == (d(5), d(7))
    assert dates(result, "TSK-0101") == (d(8), d(9))


def test_priority_change_between_active_projects_leaves_on_hold_projects_alone():
    applied = apply(
        Situation.PRIORITY_CHANGE,
        project_id="PRJ-002",
        new_priority_rank=1,
        estimated_days=None,
        target_date="2026-01-30",
    )
    ranks = {pid: p.priority_rank for pid, p in applied.world.projects.items()}
    assert ranks == {"PRJ-001": 2, "PRJ-002": 1, "PRJ-003": 3}
    assert applied.world.projects["PRJ-003"].status == "on_hold"
    assert applied.world.tasks["TSK-0101"].effort_fixed is False


# --- 8. Critical Bug -----------------------------------------------------------------------


def test_critical_bug_inserts_a_preemptive_bug_fix_item():
    applied = apply(Situation.CRITICAL_BUG)
    item = applied.world.items[-1]
    assert (item.resource_id, item.start, item.share, item.working_days, item.source) == (
        "RES-001",
        d(5),
        1.0,
        2,
        "scenario",
    )
    assert applied.targets.tasks == {"TSK-0002"}
    assert applied.targets.resources == {"RES-001"}


@pytest.mark.parametrize(
    ("severity", "first", "second"),
    [
        # Critical: 05-06 habis untuk perbaikan; TSK-0001 07-09, TSK-0002 12-13.
        ("critical", (d(7), d(9)), (d(12), d(13))),
        # High: setengah hari 05-06 -> TSK-0001 maju 0.5, 0.5, 1, 1: selesai 08; TSK-0002 09-12.
        ("high", (d(5), d(8)), (d(9), d(12))),
    ],
)
def test_bug_fix_preempts_the_members_project_work(severity, first, second):
    result = simulate(apply(Situation.CRITICAL_BUG, severity=severity).world)
    assert dates(result, "TSK-0001") == first
    assert dates(result, "TSK-0002") == second


# --- bantuan -------------------------------------------------------------------------------


def test_infer_complexity_uses_the_level_whose_range_contains_the_estimate():
    w = world(
        complexity_levels=[
            complexity("CX-L", 1, 3, 0.6),
            complexity("CX-M", 3.5, 5, 0.9),
            complexity("CX-H", 5.5, 8, 1.2),
        ]
    )
    assert infer_complexity(w, 2) == "CX-L"
    assert infer_complexity(w, 4) == "CX-M"
    # Di luar semua rentang: level terdekat.
    assert infer_complexity(w, 12) == "CX-H"
    assert infer_complexity(w, 0.5) == "CX-L"
