"""SCRUM-62: opsi lever parameter New Member, Critical Bug, dan Priority Change, tanpa basis data.

Baseline uji (hari kerja Senin s.d. Jumat, libur Selasa 2026-02-10):

    PRJ-001 development prioritas 1, PRJ-002 planning prioritas 2, PRJ-003 on hold prioritas 3
    TSK-0001 BE di PRJ-001

    RES-001 FE   sudden_leave 01-05..01-07, rotation_in 02-02..02-16 (kedatangan, bukan absen)
    RES-002 BE
    RES-003 BE, sekunder FE (cross-role)   planned_leave 01-05, sudden_leave 01-06..01-09
    RES-004 FE   bergabung 2026-02-16
    RES-005 BE   rotation_out 02-09..03-31
    RES-006 FE   planned_leave 02-02..02-16
    RES-007 BE   planned_leave 01-05..01-06, sudden_leave 02-02..02-13

Onboarding dua minggu sejak Senin 2026-02-02 adalah 10 hari kerja: karena libur 02-10, periodenya
2026-02-02 s.d. 2026-02-16. Perbaikan 2.5 hari sejak Senin 2026-01-05: 01-05 s.d. 01-07.
"""

import pytest

from app.modules.dss.sweep_candidates import build_candidates
from app.modules.dss.sweep_levers import LEVERS, fixer_options, mentor_options, rank_options
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import BaselineIndex, validate_parameters
from tests.sim_fixtures import START, absence, holiday, project, resource, snapshot, task

BASELINE = snapshot(
    projects=[
        project("PRJ-001", rank=1, status="development"),
        project("PRJ-002", rank=2, status="planning"),
        project("PRJ-003", rank=3, status="on_hold"),
    ],
    resources=[
        resource("RES-001", "ROLE-FE"),
        resource("RES-002", "ROLE-BE"),
        {**resource("RES-003", "ROLE-BE", secondary="ROLE-FE"), "cross_role_capable": True},
        resource("RES-004", "ROLE-FE", join="2026-02-16"),
        resource("RES-005", "ROLE-BE"),
        resource("RES-006", "ROLE-FE"),
        resource("RES-007", "ROLE-BE"),
    ],
    tasks=[task("TSK-0001", 3)],
    holidays=[holiday("2026-02-10")],
    availability=[
        absence("AVL-001", "RES-001", "2026-01-05", "2026-01-07", "sudden_leave", planned=False),
        absence("AVL-002", "RES-001", "2026-02-02", "2026-02-16", "rotation_in"),
        absence("AVL-003", "RES-003", "2026-01-05", "2026-01-05"),
        absence("AVL-004", "RES-003", "2026-01-06", "2026-01-09", "sudden_leave", planned=False),
        absence("AVL-005", "RES-005", "2026-02-09", "2026-03-31", "rotation_out"),
        absence("AVL-006", "RES-006", "2026-02-02", "2026-02-16"),
        absence("AVL-007", "RES-007", "2026-01-05", "2026-01-06"),
        absence("AVL-008", "RES-007", "2026-02-02", "2026-02-13", "sudden_leave", planned=False),
    ],
)
INDEX = BaselineIndex.build(BASELINE)

NEW_MEMBER = {
    "role_id": "ROLE-FE",
    "seniority": "junior",
    "join_date": "2026-02-02",
    "onboarding_weeks": 2,
    "initial_capacity": 0.5,
    "mentor_resource_id": "RES-002",
    "mentor_share": 0.3,
}
CRITICAL_BUG = {
    "severity": "critical",
    "fixing_days": 2.5,
    "resource_id": "RES-002",
    "affected_task_ids": ["TSK-0001"],
}


def summary(candidate_set) -> list[tuple]:
    return [(c.value, c.is_current, c.excluded_reason) for c in candidate_set.candidates]


def test_parameter_levers_have_generators():
    assert LEVERS[Situation.NEW_MEMBER].options is mentor_options
    assert LEVERS[Situation.CRITICAL_BUG].options is fixer_options
    assert LEVERS[Situation.PRIORITY_CHANGE].options is rank_options


# --- New Member: mentor ------------------------------------------------------------------------


def test_mentor_candidates():
    result = build_candidates(Situation.NEW_MEMBER, NEW_MEMBER, BASELINE)

    # Tanpa mentor lebih dulu; lalu peran FE utama, FE sekunder (cross-role), dan lainnya.
    # RES-004 belum bergabung saat anggota baru masuk, jadi tidak diusulkan.
    assert summary(result) == [
        ("RES-002", True, None),
        (None, False, None),
        ("RES-001", False, None),  # rotation_in bukan ketidakhadiran
        ("RES-003", False, None),  # cuti Januari tidak menyentuh onboarding
        ("RES-007", False, None),  # 02-16 masih hadir karena libur 02-10 menggeser onboarding
        (
            "RES-006",
            False,
            "Tidak hadir sepanjang onboarding 2026-02-02 s.d. 2026-02-16: "
            "planned_leave 2026-02-02 s.d. 2026-02-16",
        ),
        (
            "RES-005",
            False,
            "Rotasi keluar 2026-02-09 s.d. 2026-03-31, bertumpuk dengan onboarding "
            "2026-02-02 s.d. 2026-02-16",
        ),
    ]
    by_value = {c.value: c for c in result.candidates}
    # Mentor lain memakai bagian waktu mentor pengguna; tanpa mentor mengosongkannya.
    assert by_value["RES-001"].parameters["mentor_share"] == 0.3
    assert by_value[None].parameters["mentor_resource_id"] is None
    assert by_value[None].parameters["mentor_share"] is None
    assert by_value[None].label == "Tanpa mentor"


def test_mentor_candidates_when_user_chose_no_mentor():
    params = {**NEW_MEMBER, "mentor_resource_id": None, "mentor_share": None}

    result = build_candidates(Situation.NEW_MEMBER, params, BASELINE)

    assert [c.value for c in result.eligible] == [None, "RES-001", "RES-003", "RES-002", "RES-007"]
    assert result.current.label == "Tanpa mentor"
    # Bagian waktu mentor pengguna kosong, jadi mesin memakai bawaan 20%.
    assert result.candidates[1].parameters["mentor_share"] is None


# --- Critical Bug: anggota yang memperbaiki ----------------------------------------------------


def test_fixer_candidates():
    result = build_candidates(Situation.CRITICAL_BUG, CRITICAL_BUG, BASELINE, start_date=START)

    # Peran task terdampak (BE) lebih dulu. RES-004 belum bergabung pada tanggal mulai.
    assert summary(result) == [
        ("RES-002", True, None),
        ("RES-005", False, None),
        ("RES-007", False, None),  # tidak hadir 2 dari 3 hari: tetap layak
        ("RES-006", False, None),
        (
            "RES-003",
            False,
            "Tidak hadir sepanjang perbaikan 2026-01-05 s.d. 2026-01-07: "
            "planned_leave 2026-01-05 s.d. 2026-01-05; sudden_leave 2026-01-06 s.d. 2026-01-09",
        ),
        (
            "RES-001",
            False,
            "Tidak hadir sepanjang perbaikan 2026-01-05 s.d. 2026-01-07: "
            "sudden_leave 2026-01-05 s.d. 2026-01-07",
        ),
    ]
    assert result.candidates[1].parameters["resource_id"] == "RES-005"
    assert result.candidates[1].parameters["fixing_days"] == 2.5


def test_fixing_period_follows_fixing_days():
    params = {**CRITICAL_BUG, "fixing_days": 2}

    result = build_candidates(Situation.CRITICAL_BUG, params, BASELINE, start_date=START)

    # Dua hari kerja (01-05..01-06) tertutup seluruhnya oleh cuti RES-007.
    assert [c.value for c in result.excluded] == ["RES-003", "RES-007", "RES-001"]


def test_fixing_period_starts_at_simulation_start():
    later = build_candidates(
        Situation.CRITICAL_BUG, CRITICAL_BUG, BASELINE, start_date=START.replace(day=12)
    )
    default = build_candidates(Situation.CRITICAL_BUG, CRITICAL_BUG, BASELINE)

    # Mulai 01-12 semua cuti Januari sudah lewat.
    assert later.excluded == []
    # Tanpa tanggal mulai: bawaan mesin, hari kerja pertama proyek paling awal (01-05).
    assert summary(default) == summary(
        build_candidates(Situation.CRITICAL_BUG, CRITICAL_BUG, BASELINE, start_date=START)
    )


# --- Priority Change: peringkat ----------------------------------------------------------------


def test_rank_candidates_for_active_project():
    params = {"project_id": "PRJ-002", "new_priority_rank": 1, "target_date": "2026-06-30"}

    result = build_candidates(Situation.PRIORITY_CHANGE, params, BASELINE)

    # Dua proyek aktif; peringkat sekarang PRJ-002 ditolak check_priority_change.
    assert summary(result) == [
        (1, True, None),
        (2, False, "Sama dengan prioritas sekarang (2)"),
    ]


def test_rank_candidates_for_on_hold_project():
    params = {"project_id": "PRJ-003", "new_priority_rank": 1, "target_date": "2026-06-30"}

    result = build_candidates(Situation.PRIORITY_CHANGE, params, BASELINE)

    # Proyek on hold ikut menjadi aktif, jadi peringkat paling besar 3.
    assert summary(result) == [(1, True, None), (2, False, None), (3, False, None)]
    assert [c.label for c in result.candidates] == [
        "Peringkat prioritas 1",
        "Peringkat prioritas 2",
        "Peringkat prioritas 3",
    ]
    assert result.candidates[2].parameters["new_priority_rank"] == 3


# --- semua kandidat layak lolos validasi -------------------------------------------------------


@pytest.mark.parametrize(
    ("situation", "params"),
    [
        (Situation.NEW_MEMBER, NEW_MEMBER),
        (Situation.CRITICAL_BUG, CRITICAL_BUG),
        (
            Situation.PRIORITY_CHANGE,
            {"project_id": "PRJ-003", "new_priority_rank": 2, "target_date": "2026-06-30"},
        ),
    ],
)
def test_every_eligible_candidate_passes_validation(situation, params):
    result = build_candidates(situation, params, BASELINE, start_date=START)

    assert len(result.eligible) > 1
    for candidate in result.eligible:
        assert validate_parameters(situation, candidate.parameters, INDEX).ok, candidate.value
