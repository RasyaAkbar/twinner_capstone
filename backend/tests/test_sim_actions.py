"""SCRUM-63: respons `ReassignTasks` di atas skenario, tanpa basis data.

Bawaan `tests.sim_fixtures`: mulai Senin 2026-01-05, kapasitas 1.0 dan bobot task 1.0, jadi
setiap anggota mengerjakan satu task sekaligus dengan satu hari effort per hari kerja.

    RES-001 BE pemilik TSK-0001 (4 hari, belum mulai), RES-002 BE tanpa task.
    Skenario Member Absence: RES-001 sudden_leave 2026-01-05 s.d. 2026-01-09.

Tanpa respons TSK-0001 menunggu RES-001 dan dikerjakan 01-12 s.d. 01-15. Dialihkan ke RES-002
sejak 01-05, task itu selesai 01-08: lima hari kerja lebih awal.
"""

import pytest

from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import MemberAbsenceParams
from app.modules.simulasi.sim_actions import ReassignTasks, ResponseActionError, apply_actions
from app.modules.simulasi.sim_engine import run_engine
from app.modules.simulasi.sim_rules import AppliedScenario
from app.modules.simulasi.sim_scheduler import simulate
from app.modules.simulasi.sim_world import Transfer
from tests.sim_fixtures import START, assignment, d, resource, snapshot, task, world


def absence_params(start: str, end: str) -> MemberAbsenceParams:
    return MemberAbsenceParams.model_validate(
        {
            "resource_id": "RES-001",
            "absence_type": "sudden_leave",
            "absence_period": {"start": start, "end": end},
        }
    )


def team(*tasks: dict, **tables: list[dict]) -> dict:
    return {
        "resources": [resource("RES-001"), resource("RES-002")],
        "tasks": list(tasks),
        "assignments": [
            assignment(f"ASG-{i:04d}", t["task_id"], "RES-001") for i, t in enumerate(tasks, 1)
        ],
        **tables,
    }


def outcome(out, task_id: str) -> tuple:
    result = out.scenario.schedule.tasks[task_id]
    return result.owner, result.start, result.finish


# --- efek pada jadwal --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("from_date", "expected"),
    [
        # Sejak hari pertama: RES-002 langsung mengerjakannya.
        (START, ("RES-002", d(5), d(8))),
        # Sejak Rabu: task belum mulai pindah antrean, dikerjakan 01-07, 08, 09, 12.
        (d(7), ("RES-002", d(7), d(12))),
    ],
)
def test_absent_members_task_finishes_earlier_when_reassigned(from_date, expected):
    snap = snapshot(**team(task("TSK-0001", 4)))
    params = absence_params("2026-01-05", "2026-01-09")

    without = run_engine(snap, Situation.MEMBER_ABSENCE, params, start_date=START)
    action = ReassignTasks(("TSK-0001",), "RES-002", from_date)
    reassigned = run_engine(
        snap, Situation.MEMBER_ABSENCE, params, start_date=START, actions=[action]
    )

    assert outcome(without, "TSK-0001") == ("RES-001", d(12), d(15))
    assert outcome(reassigned, "TSK-0001") == expected
    # Baseline tidak tersentuh respons.
    assert reassigned.baseline.schedule.tasks == without.baseline.schedule.tasks
    assert "RES-002" in reassigned.targets.resources
    assert any("TSK-0001 dialihkan ke RES-002" in note for note in reassigned.notes)


def test_started_task_keeps_its_progress_with_the_new_owner():
    # Mulai Rabu 2025-12-31 dengan estimasi 6 hari: sudah 3 hari kerja (12-31, 01-01, 01-02),
    # sisa 3. RES-001 mengerjakan 01-05 dan 01-06 (sisa 1), lalu tidak hadir 01-07 s.d. 01-16.
    running = task(
        "TSK-0002", 6, start="2025-12-31", status="in_progress", actual_start="2025-12-31"
    )
    snap = snapshot(**team(running))
    params = absence_params("2026-01-07", "2026-01-16")

    without = run_engine(snap, Situation.MEMBER_ABSENCE, params, start_date=START)
    action = ReassignTasks(("TSK-0002",), "RES-002", d(7))
    reassigned = run_engine(
        snap, Situation.MEMBER_ABSENCE, params, start_date=START, actions=[action]
    )

    # Tanpa respons sisa satu hari menunggu RES-001 kembali 01-19.
    assert outcome(without, "TSK-0002") == ("RES-001", d(31, 12, 2025), d(19))
    # RES-002 melanjutkan sisa satu hari pada 01-07; tanggal mulai task tetap.
    assert outcome(reassigned, "TSK-0002") == ("RES-002", d(31, 12, 2025), d(7))
    member_days = {day.day: day.tasks for day in reassigned.scenario.loads["RES-002"]}
    assert member_days[d(7)] == ("TSK-0002",)


def test_pool_task_goes_to_the_new_owner():
    w = world(resources=[resource("RES-001"), resource("RES-002")], tasks=[task("TSK-0003", 2)])
    shared = simulate(w).tasks["TSK-0003"]
    w.tasks["TSK-0003"].transfers.append(Transfer(START, "RES-002"))
    transferred = simulate(w).tasks["TSK-0003"]

    # Task tanpa pemilik dibagikan dari pool ke RES-001 (kapasitas sama, resource_id lebih kecil).
    assert (shared.owner, shared.finish) == ("RES-001", d(6))
    assert (transferred.owner, transferred.start, transferred.finish) == ("RES-002", d(5), d(6))


def test_without_actions_the_result_is_unchanged():
    snap = snapshot(**team(task("TSK-0001", 4)))
    params = absence_params("2026-01-05", "2026-01-09")

    default = run_engine(snap, Situation.MEMBER_ABSENCE, params, start_date=START)
    empty = run_engine(snap, Situation.MEMBER_ABSENCE, params, start_date=START, actions=[])

    assert default.scenario.schedule.tasks == empty.scenario.schedule.tasks
    assert default.scenario.kpis.values == empty.scenario.kpis.values
    assert default.notes == empty.notes


def test_reassignment_applies_to_replications():
    snap = snapshot(**team(task("TSK-0001", 4)))
    params = absence_params("2026-01-05", "2026-01-09")
    action = ReassignTasks(("TSK-0001",), "RES-002", START)

    out = run_engine(
        snap, Situation.MEMBER_ABSENCE, params, start_date=START, runs=20, seed=7, actions=[action]
    )

    assert out.replication is not None
    assert outcome(out, "TSK-0001")[0] == "RES-002"


# --- penerapan dan penolakan -------------------------------------------------------------------


def applied_world(**tables: list[dict]) -> AppliedScenario:
    base = {
        "resources": [
            resource("RES-001"),
            resource("RES-002"),
            resource("RES-003", "ROLE-FE"),
            resource("RES-004", "ROLE-FE", secondary="ROLE-BE"),
            {**resource("RES-005", "ROLE-FE", secondary="ROLE-BE"), "cross_role_capable": True},
        ],
        "tasks": [
            task("TSK-0001", 4),
            task("TSK-0003", 2, status="done"),
            task("TSK-0004", 1, status="dropped"),
        ],
        "assignments": [assignment("ASG-0001", "TSK-0001", "RES-001")],
    }
    base.update(tables)
    return AppliedScenario(world(**base))


def test_closed_tasks_are_skipped_and_noted():
    applied = applied_world()

    apply_actions(applied, [ReassignTasks(("TSK-0001", "TSK-0003", "TSK-0004"), "RES-002", START)])

    tasks = applied.world.tasks
    assert tasks["TSK-0001"].transfers == [Transfer(START, "RES-002")]
    assert tasks["TSK-0003"].transfers == [] and tasks["TSK-0004"].transfers == []
    assert applied.notes == [
        "Respons: TSK-0001 dialihkan ke RES-002 mulai 2026-01-05; task yang sudah berjalan "
        "dilanjutkan dengan sisa effort-nya.",
        "Respons: TSK-0003 (done), TSK-0004 (dropped) tidak dialihkan karena tidak lagi "
        "dijadwalkan.",
    ]
    assert applied.targets.tasks == {"TSK-0001"}
    assert applied.targets.resources == {"RES-002"}
    assert applied.targets.projects == {"PRJ-001"}


def test_task_already_held_by_the_new_owner_is_not_moved():
    applied = applied_world()

    apply_actions(applied, [ReassignTasks(("TSK-0001",), "RES-001", START)])

    assert applied.world.tasks["TSK-0001"].transfers == []
    assert applied.notes == [] and applied.targets.resources == set()


def test_secondary_role_counts_only_when_cross_role_capable():
    applied = applied_world()

    apply_actions(applied, [ReassignTasks(("TSK-0001",), "RES-005", START)])
    assert applied.world.tasks["TSK-0001"].transfers == [Transfer(START, "RES-005")]

    with pytest.raises(ResponseActionError, match="RES-004 tidak memegang peran ROLE-BE"):
        apply_actions(applied_world(), [ReassignTasks(("TSK-0001",), "RES-004", START)])


@pytest.mark.parametrize(
    ("action", "change", "message"),
    [
        (ReassignTasks(("TSK-0001",), "RES-003", START), None, "RES-003 tidak memegang peran"),
        (ReassignTasks(("TSK-0001",), "RES-999", START), None, "Anggota RES-999 tidak ada"),
        (ReassignTasks(("TSK-9999",), "RES-002", START), None, "Task TSK-9999 tidak ada"),
        (
            ReassignTasks(("TSK-0001",), "RES-002", d(12)),
            ("leave_date", d(9)),
            "RES-002 sudah keluar tim pada 2026-01-09",
        ),
        (
            ReassignTasks(("TSK-0001",), "RES-002", d(12)),
            ("excluded_projects", {"PRJ-001": d(12)}),
            "RES-002 tidak lagi mengerjakan proyek PRJ-001 mulai 2026-01-12",
        ),
    ],
)
def test_invalid_reassignment_is_rejected_without_changes(action, change, message):
    applied = applied_world()
    if change is not None:
        setattr(applied.world.resources["RES-002"], *change)

    with pytest.raises(ResponseActionError, match=message):
        apply_actions(applied, [action])

    assert applied.world.tasks["TSK-0001"].transfers == []
    assert applied.notes == []


def test_rejection_happens_before_any_task_moves():
    applied = applied_world()

    with pytest.raises(ResponseActionError):
        apply_actions(applied, [ReassignTasks(("TSK-0001", "TSK-9999"), "RES-002", START)])

    assert applied.world.tasks["TSK-0001"].transfers == []


def test_baseline_world_keeps_cross_role_flag():
    w = applied_world().world

    assert w.resources["RES-005"].cross_role_capable is True
    assert w.resources["RES-004"].cross_role_capable is False
