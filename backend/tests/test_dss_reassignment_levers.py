"""SCRUM-64: opsi lever pengalihan Absence, Rotation, Overrun, Blocker, dan Change Request.

Baseline uji (mulai Senin 2026-01-05, PRJ-001 dan PRJ-002 development):

    RES-001 BE, sekunder FE (cross-role)  pemilik TSK-0001..TSK-0006
    RES-002 BE
    RES-003 FE, sekunder BE (cross-role)
    RES-004 FE, sekunder BE (bukan cross-role: tidak boleh mengambil task BE)
    RES-005 BE, bergabung 2026-01-19
    RES-006 FE                            pemilik TSK-0007
    RES-007 BE                            planned_leave 2026-01-12 s.d. 2026-01-16

    TSK-0001 BE 3 hari, TSK-0002 BE in_progress, TSK-0003 FE, TSK-0005 BE di PRJ-002,
    TSK-0004 BE yang baru dimulai 2026-03-02, TSK-0006 done, TSK-0007 FE.
"""

import pytest

from app.modules.dss.sweep_candidates import build_candidates
from app.modules.dss.sweep_levers import (
    LEVERS,
    absence_options,
    blocker_options,
    change_request_options,
    overrun_options,
    rotation_options,
)
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import BaselineIndex, validate_parameters
from app.modules.simulasi.sim_actions import apply_actions
from app.modules.simulasi.sim_rules import apply_scenario
from app.modules.simulasi.sim_world import build_world
from tests.sim_fixtures import START, absence, assignment, d, project, resource, snapshot, task

BASELINE = snapshot(
    projects=[project("PRJ-001"), project("PRJ-002", rank=2)],
    resources=[
        {**resource("RES-001", secondary="ROLE-FE"), "cross_role_capable": True},
        resource("RES-002"),
        {**resource("RES-003", "ROLE-FE", secondary="ROLE-BE"), "cross_role_capable": True},
        resource("RES-004", "ROLE-FE", secondary="ROLE-BE"),
        resource("RES-005", join="2026-01-19"),
        resource("RES-006", "ROLE-FE"),
        resource("RES-007"),
    ],
    tasks=[
        task("TSK-0001", 3),
        task("TSK-0002", 2, start="2026-01-02", status="in_progress", actual_start="2026-01-02"),
        task("TSK-0003", 2, role_id="ROLE-FE"),
        task("TSK-0004", 3, start="2026-03-02"),
        task("TSK-0005", 2, project_id="PRJ-002"),
        task("TSK-0006", 2, status="done"),
        task("TSK-0007", 2, role_id="ROLE-FE"),
    ],
    assignments=[
        assignment("ASG-0001", "TSK-0001", "RES-001"),
        assignment("ASG-0002", "TSK-0002", "RES-001"),
        assignment("ASG-0003", "TSK-0003", "RES-001", role_id="ROLE-FE"),
        assignment("ASG-0004", "TSK-0004", "RES-001"),
        assignment("ASG-0005", "TSK-0005", "RES-001"),
        assignment("ASG-0006", "TSK-0006", "RES-001"),
        assignment("ASG-0007", "TSK-0007", "RES-006", role_id="ROLE-FE"),
    ],
    availability=[absence("AVL-001", "RES-007", "2026-01-12", "2026-01-16")],
)
INDEX = BaselineIndex.build(BASELINE)

ABSENCE = {
    "resource_id": "RES-001",
    "absence_type": "sudden_leave",
    "absence_period": {"start": "2026-01-12", "end": "2026-01-16"},
}
ROTATION = {
    "resource_id": "RES-001",
    "origin_project_id": "PRJ-001",
    "destination_project_id": "PRJ-002",
    "transfer_date": "2026-01-12",
}
OVERRUN = {"task_id": "TSK-0001", "original_days": 3, "revised_days": 5, "remaining_days": 5}
BLOCKER = {"task_id": "TSK-0007", "blocking_days": 3, "independent_task_ids": ["TSK-0003"]}
PERIOD = {"start": "2026-01-12", "end": "2026-01-23"}
CHANGE_REQUEST = {
    "project_id": "PRJ-001",
    "added_tasks": [
        {"task_name": "API baru", "required_role_id": "ROLE-BE", "estimated_days": 2},
        {"task_name": "Layar baru", "required_role_id": "ROLE-FE", "estimated_days": 1},
        {"task_name": "Validasi", "required_role_id": "ROLE-BE", "estimated_days": 1},
    ],
    "affected_period": PERIOD,
    "target_date": "2026-01-30",
}

BE_TASKS = ("TSK-0001", "TSK-0002", "TSK-0005")


def sweep(situation: Situation, params: dict):
    return build_candidates(situation, params, BASELINE, start_date=START)


def summary(candidate_set) -> list[tuple]:
    """(nilai, label, task yang dialihkan, mulai, alasan dikecualikan) per kandidat."""
    rows = []
    for c in candidate_set.candidates:
        action = c.actions[0] if c.actions else None
        rows.append(
            (
                c.value,
                c.label,
                action.task_ids if action else (),
                action.from_date if action else None,
                c.excluded_reason,
            )
        )
    return rows


def label(resource_id: str, role: str | None = None) -> str:
    text = f"Anggota {resource_id} ({resource_id})"
    return f"{text} untuk {role}" if role else text


def test_reassignment_levers_have_generators():
    assert LEVERS[Situation.MEMBER_ABSENCE].options is absence_options
    assert LEVERS[Situation.MEMBER_ROTATION].options is rotation_options
    assert LEVERS[Situation.TASK_OVERRUN].options is overrun_options
    assert LEVERS[Situation.TASK_BLOCKER].options is blocker_options
    assert LEVERS[Situation.CHANGE_REQUEST].options is change_request_options


# --- Member Absence ----------------------------------------------------------------------------


def test_absence_replacements_per_role_group():
    result = sweep(Situation.MEMBER_ABSENCE, ABSENCE)

    # Task RES-001 yang terdampak: yang berjalan atau dapat dimulai sebelum 01-16. TSK-0004
    # (mulai Maret) dan TSK-0006 (done) tidak ikut. Kelompok BE (3 task) lebih dulu dari FE.
    # RES-004 tidak cross-role dan RES-005 belum bergabung, jadi tidak menjadi calon BE.
    absent = (
        "Tidak hadir sepanjang periode ketidakhadiran RES-001 2026-01-12 s.d. 2026-01-16: "
        "planned_leave 2026-01-12 s.d. 2026-01-16"
    )
    assert summary(result) == [
        (None, "Tanpa pengalihan", (), None, None),
        ("RES-002", label("RES-002", "ROLE-BE"), BE_TASKS, d(12), None),
        ("RES-003", label("RES-003", "ROLE-BE"), BE_TASKS, d(12), None),
        ("RES-003", label("RES-003", "ROLE-FE"), ("TSK-0003",), d(12), None),
        ("RES-004", label("RES-004", "ROLE-FE"), ("TSK-0003",), d(12), None),
        ("RES-006", label("RES-006", "ROLE-FE"), ("TSK-0003",), d(12), None),
        ("RES-007", label("RES-007", "ROLE-BE"), BE_TASKS, d(12), absent),
    ]
    assert result.no_lever is None
    assert all(c.parameters == result.current.parameters for c in result.candidates)


def test_absence_with_affected_tasks_moves_only_those():
    result = sweep(Situation.MEMBER_ABSENCE, {**ABSENCE, "affected_task_ids": ["TSK-0004"]})

    # Satu kelompok peran: label tanpa nama peran. RES-007 dikecualikan (cuti pekan itu).
    assert [(c.value, c.label) for c in result.eligible] == [
        (None, "Tanpa pengalihan"),
        ("RES-002", label("RES-002")),
        ("RES-003", label("RES-003")),
    ]
    assert {c.actions[0].task_ids for c in result.candidates if c.actions} == {("TSK-0004",)}


# --- Member Rotation or Withdrawal -------------------------------------------------------------


def test_rotation_replacements_for_released_tasks():
    result = sweep(Situation.MEMBER_ROTATION, ROTATION)

    # Task RES-001 di proyek asal yang dilepas mulai 01-12; TSK-0005 (PRJ-002) tetap miliknya.
    # Tanpa syarat kehadiran, RES-007 tetap calon walau cuti pekan itu.
    be = ("TSK-0001", "TSK-0002", "TSK-0004")
    assert summary(result) == [
        (None, "Tanpa pengalihan", (), None, None),
        ("RES-002", label("RES-002", "ROLE-BE"), be, d(12), None),
        ("RES-007", label("RES-007", "ROLE-BE"), be, d(12), None),
        ("RES-003", label("RES-003", "ROLE-BE"), be, d(12), None),
        ("RES-003", label("RES-003", "ROLE-FE"), ("TSK-0003",), d(12), None),
        ("RES-004", label("RES-004", "ROLE-FE"), ("TSK-0003",), d(12), None),
        ("RES-006", label("RES-006", "ROLE-FE"), ("TSK-0003",), d(12), None),
    ]


def test_withdrawal_replacements_cover_every_project():
    result = sweep(Situation.MEMBER_ROTATION, {**ROTATION, "destination_project_id": None})

    be_moves = {c.actions[0].task_ids for c in result.candidates if c.label.endswith("ROLE-BE")}
    assert be_moves == {("TSK-0001", "TSK-0002", "TSK-0004", "TSK-0005")}
    assert "RES-001" not in {c.value for c in result.candidates}


# --- Task Overrun ------------------------------------------------------------------------------


def test_overrun_replacements_for_the_remaining_work():
    result = sweep(Situation.TASK_OVERRUN, OVERRUN)

    assert summary(result) == [
        (None, "Tanpa pengalihan", (), None, None),
        ("RES-002", label("RES-002"), ("TSK-0001",), START, None),
        ("RES-007", label("RES-007"), ("TSK-0001",), START, None),
        ("RES-003", label("RES-003"), ("TSK-0001",), START, None),
    ]


# --- Task Blocker ------------------------------------------------------------------------------


def test_blocker_takers_for_independent_tasks():
    result = sweep(Situation.TASK_BLOCKER, BLOCKER)

    # Di skenario pengguna RES-006 (pemilik task terblokir) mengambil TSK-0003; DSS mencoba
    # anggota FE lain, termasuk RES-001 lewat peran sekundernya.
    assert summary(result) == [
        (None, "Tanpa pengalihan", (), None, None),
        ("RES-003", label("RES-003"), ("TSK-0003",), START, None),
        ("RES-004", label("RES-004"), ("TSK-0003",), START, None),
        ("RES-001", label("RES-001"), ("TSK-0003",), START, None),
    ]


@pytest.mark.parametrize("independent", [[], ["TSK-0002"]])
def test_blocker_without_unstarted_independent_tasks_has_no_lever(independent):
    result = sweep(Situation.TASK_BLOCKER, {**BLOCKER, "independent_task_ids": independent})

    assert [c.is_current for c in result.candidates] == [True]
    assert result.no_lever == "Tidak ada task independen yang belum mulai untuk dialihkan"


# --- Mid-sprint Change Request -----------------------------------------------------------------


def test_change_request_owners_per_role_group():
    result = sweep(Situation.CHANGE_REQUEST, CHANGE_REQUEST)

    # CR-001 dan CR-003 (BE), CR-002 (FE); satu kandidat per anggota per kelompok.
    be, fe = ("CR-001", "CR-003"), ("CR-002",)
    assert summary(result) == [
        (None, "Tanpa pengalihan", (), None, None),
        ("RES-001", label("RES-001", "ROLE-BE"), be, d(12), None),
        ("RES-002", label("RES-002", "ROLE-BE"), be, d(12), None),
        ("RES-007", label("RES-007", "ROLE-BE"), be, d(12), None),
        ("RES-003", label("RES-003", "ROLE-BE"), be, d(12), None),
        ("RES-003", label("RES-003", "ROLE-FE"), fe, d(12), None),
        ("RES-004", label("RES-004", "ROLE-FE"), fe, d(12), None),
        ("RES-006", label("RES-006", "ROLE-FE"), fe, d(12), None),
        ("RES-001", label("RES-001", "ROLE-FE"), fe, d(12), None),
    ]


def test_change_request_without_added_tasks_has_no_lever():
    params = {
        "project_id": "PRJ-001",
        "removed_task_ids": ["TSK-0004"],
        "affected_period": PERIOD,
        "target_date": "2026-01-30",
    }

    result = sweep(Situation.CHANGE_REQUEST, params)

    assert len(result.candidates) == 1
    assert result.no_lever == "Change request ini tidak menambah task"


# --- setiap kandidat dapat diterapkan mesin ----------------------------------------------------


@pytest.mark.parametrize(
    ("situation", "params"),
    [
        (Situation.MEMBER_ABSENCE, ABSENCE),
        (Situation.MEMBER_ROTATION, ROTATION),
        (Situation.MEMBER_ROTATION, {**ROTATION, "destination_project_id": None}),
        (Situation.TASK_OVERRUN, OVERRUN),
        (Situation.TASK_BLOCKER, BLOCKER),
        (Situation.CHANGE_REQUEST, CHANGE_REQUEST),
    ],
)
def test_every_candidate_action_is_accepted_by_the_engine(situation, params):
    result = sweep(situation, params)
    typed = validate_parameters(situation, params, INDEX).params
    world = build_world(BASELINE, START)

    assert len(result.candidates) > 1
    for candidate in result.candidates[1:]:
        applied = apply_scenario(world, situation, typed)
        apply_actions(applied, candidate.actions)
        for task_id in candidate.actions[0].task_ids:
            assert applied.world.tasks[task_id].transfers, (candidate.label, task_id)
