"""SCRUM-66: penilaian kandidat sweep DSS pada empat kriteria, dengan skor yang dihitung manual.

Bawaan `tests.sim_fixtures`: mulai Senin 2026-01-05, kapasitas dan bobot task 1.0 (satu task
sekaligus, beban harian 1.0 saat bekerja dan 0 saat kosong); RLI mingguan = jumlah task yang
dikerjakan pada minggu itu, berbeban berlebih bila lebih dari 1.0.

Pengalihan (Member Absence, RES-001 cuti 01-05 s.d. 01-09, PRJ-001 target 01-09):

    RES-001 BE pemilik TSK-0001 (4 hari); RES-003 BE pemilik TSK-0002 (3 hari, 01-05..01-07)
    RES-002 BE tanpa task; RES-004 FE, sekunder BE (cross-role), tanpa task

    Kandidat  TSK-0001         Peran  Kapasitas 01-05..01-08   Dampak pada pekerjaannya
    RES-002   01-05..01-08     1.0    4 dari 4 hari = 1.0      tidak ada
    RES-003   01-05..01-08     1.0    1 dari 4 hari = 0.25     TSK-0002 mundur ke 01-13 (4 hari),
                                                               minggu pertama 2 task (berlebih)
    RES-004   01-05..01-08     0.5    4 dari 4 hari = 1.0      tidak ada

    Kriteria 3 (min-max per komponen, dampak terkecil = 1):
      minggu berlebih [0, 1, 0] -> [1, 0, 1]; RLI tertinggi naik 1 untuk semua -> [1, 1, 1];
      keterlambatan [0, 4, 0] -> [1, 0, 1]; rata-rata: RES-002 1, RES-003 1/3, RES-004 1.
"""

from types import SimpleNamespace

import pytest

from app.modules.dss.sweep_candidates import build_candidates
from app.modules.dss.sweep_runner import run_sweep
from app.modules.dss.sweep_scoring import (
    DEFAULT_WEIGHTS,
    Criterion,
    ScoringWeights,
    kpi_effect_scores,
    rank_sweep,
)
from app.modules.evaluasi.kpi_definitions import (
    OVERLOAD_FLAGS,
    SCHEDULE_VARIANCE,
    WORKLOAD_BALANCE_INDEX,
    KpiStatus,
)
from app.modules.simulasi.scenario_models import Situation
from tests.sim_fixtures import START, assignment, project, resource, snapshot, task

# Kriteria 4 tidak ikut, supaya total dapat dihitung manual dari kriteria 1 s.d. 3.
WITHOUT_KPI = ScoringWeights(role_match=1, available_capacity=1, current_work=1, kpi_effect=0)

ABSENCE_SNAPSHOT = snapshot(
    projects=[project(target="2026-01-09")],
    resources=[
        resource("RES-001"),
        resource("RES-002"),
        resource("RES-003"),
        {**resource("RES-004", "ROLE-FE", secondary="ROLE-BE"), "cross_role_capable": True},
    ],
    tasks=[task("TSK-0001", 4), task("TSK-0002", 3)],
    assignments=[
        assignment("ASG-0001", "TSK-0001", "RES-001"),
        assignment("ASG-0002", "TSK-0002", "RES-003"),
    ],
)
ABSENCE = {
    "resource_id": "RES-001",
    "absence_type": "sudden_leave",
    "absence_period": {"start": "2026-01-05", "end": "2026-01-09"},
}


def ranked(snapshot_data, situation, params, weights=DEFAULT_WEIGHTS):
    candidates = build_candidates(situation, params, snapshot_data, start_date=START)
    return rank_sweep(run_sweep(snapshot_data, candidates, start_date=START), weights)


def scores(ranking) -> list[tuple]:
    return [
        (
            c.result.candidate.value,
            c.rank,
            c.score,
            *(c.criteria[k].score for k in Criterion if k != Criterion.KPI_EFFECT),
        )
        for c in ranking.candidates
    ]


# --- lever pengalihan --------------------------------------------------------------------------


def test_reassignment_scores_are_hand_calculated():
    ranking = ranked(ABSENCE_SNAPSHOT, Situation.MEMBER_ABSENCE, ABSENCE, WITHOUT_KPI)

    # (nilai, peringkat, total, peran, kapasitas, pekerjaan sendiri); tanpa pengalihan tidak
    # punya anggota, jadi hanya kriteria 4 yang berlaku dan bobotnya 0.
    assert scores(ranking) == [
        ("RES-002", 1, 1.0, 1.0, 1.0, 1.0),
        ("RES-004", 2, 0.833333, 0.5, 1.0, 1.0),
        ("RES-003", 3, 0.527778, 1.0, 0.25, 0.333333),
        (None, 4, 0.0, None, None, None),
    ]
    assert ranking.current.rank == 4
    assert not ranking.no_suitable_candidate
    # Skenario pengguna terlambat dibandingkan baseline.
    assert not ranking.no_action_needed


def test_criterion_facts_for_reasons():
    ranking = ranked(ABSENCE_SNAPSHOT, Situation.MEMBER_ABSENCE, ABSENCE, WITHOUT_KPI)
    by_value = {c.result.candidate.value: c for c in ranking.candidates}

    busy = by_value["RES-003"].criteria
    assert busy[Criterion.ROLE_MATCH].facts == {"roles": ["ROLE-BE"], "match": "primary"}
    assert busy[Criterion.AVAILABLE_CAPACITY].facts == {
        "available_days": 1.0,
        "working_days": 4,
        "start": "2026-01-05",
        "end": "2026-01-08",
    }
    assert busy[Criterion.CURRENT_WORK].facts == {
        "overloaded_weeks": 1,
        "peak_rli": 1.0,
        "own_delay_days": 4,
    }
    assert by_value["RES-004"].criteria[Criterion.ROLE_MATCH].facts["match"] == "secondary"


def test_default_weights_rank_the_kpi_effect_too():
    ranking = ranked(ABSENCE_SNAPSHOT, Situation.MEMBER_ABSENCE, ABSENCE)
    by_value = {c.result.candidate.value: c for c in ranking.candidates}

    assert ranking.candidates[0].result.candidate.value == "RES-002"
    assert ranking.current.rank == 4
    # RES-002 dan RES-004 menghasilkan jadwal yang sama, jadi efek KPI-nya sama.
    effect = Criterion.KPI_EFFECT
    assert by_value["RES-002"].criteria[effect] == by_value["RES-004"].criteria[effect]
    sv = by_value["RES-002"].criteria[effect].facts["changes"][SCHEDULE_VARIANCE]
    assert sv == -5  # SV +4 menjadi -1
    for candidate in ranking.candidates:
        applicable = [
            (DEFAULT_WEIGHTS.of(c), v.score)
            for c, v in candidate.criteria.items()
            if v.score is not None
        ]
        total = sum(w * s for w, s in applicable) / sum(w for w, _ in applicable)
        assert candidate.score == pytest.approx(total, abs=1e-6)
    assert [c.result.candidate.value for c in ranking.candidates[:3]] == [
        c.candidate.value for c in ranking.top()
    ]


# --- lever parameter: mentor -------------------------------------------------------------------


def test_mentor_scores_use_seniority_and_the_baseline_as_reference():
    """New Member BE bergabung 01-05, onboarding satu minggu (01-05..01-09), mentor RES-002.

    RES-001 BE senior memegang TSK-0001 (4 hari, selesai 01-08 di baseline); RES-002 BE mid,
    RES-003 FE senior, dan RES-004 BE junior tanpa task. Membimbing memakai 20% kapasitas, jadi
    TSK-0001 RES-001 maju 0.8 hari per hari dan selesai 01-09 (1 hari terlambat).

        Kandidat       Peran x senioritas   Kapasitas onboarding    Pekerjaan sendiri
        RES-002 (pilihan)  1 x 0.75          5/5 = 1.0              1.0
        RES-004            1 x 0.5           1.0                    1.0
        RES-003            0 x 1.0           1.0                    1.0
        RES-001            1 x 1.0           1 hari kosong = 0.2    (1 + 1 + 0) / 3
    """
    data = snapshot(
        resources=[
            {**resource("RES-001"), "seniority": "senior"},
            resource("RES-002"),
            {**resource("RES-003", "ROLE-FE"), "seniority": "senior"},
            {**resource("RES-004"), "seniority": "junior"},
        ],
        tasks=[task("TSK-0001", 4)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    )
    params = {
        "role_id": "ROLE-BE",
        "seniority": "junior",
        "join_date": "2026-01-05",
        "onboarding_weeks": 1,
        "initial_capacity": 0.5,
        "mentor_resource_id": "RES-002",
        "mentor_share": 0.2,
    }

    ranking = ranked(data, Situation.NEW_MEMBER, params, WITHOUT_KPI)

    assert scores(ranking) == [
        ("RES-002", 1, 0.916667, 0.75, 1.0, 1.0),
        ("RES-004", 2, 0.833333, 0.5, 1.0, 1.0),
        ("RES-003", 3, 0.666667, 0.0, 1.0, 1.0),
        ("RES-001", 4, 0.622222, 1.0, 0.2, 0.666667),
        (None, 5, 0.0, None, None, None),
    ]
    assert ranking.current.rank == 1
    assert ranking.no_suitable_candidate
    own = {c.result.candidate.value: c for c in ranking.candidates}["RES-001"]
    assert own.criteria[Criterion.CURRENT_WORK].facts["own_delay_days"] == 1
    assert own.criteria[Criterion.ROLE_MATCH].facts["seniority"] == "senior"


# --- kriteria 4 --------------------------------------------------------------------------------


def item(key, current, candidate, delta, status):
    return SimpleNamespace(
        key=key, baseline=current, scenario=candidate, delta=delta, status=status
    )


def test_kpi_effect_normalises_each_kpi_that_differs():
    """SV terhadap pilihan pengguna (+4): A menjadi -1 (perbaikan 5), B menjadi +2 (perbaikan
    2): [0, 5, 2] -> [0, 1, 0.4]. Overload Flag: B bertambah 1: [0, 0, -1] -> [1, 1, 0]. WBI
    berubah di dalam toleransi untuk semua kandidat, jadi tidak ikut dirata-rata."""
    same = KpiStatus.SAME
    comparisons = [
        [
            item(SCHEDULE_VARIANCE, 4, 4, 0, same),
            item(OVERLOAD_FLAGS, 0, 0, 0, same),
            item(WORKLOAD_BALANCE_INDEX, 0.8, 0.8, 0, same),
        ],
        [
            item(SCHEDULE_VARIANCE, 4, -1, -5, KpiStatus.BETTER),
            item(OVERLOAD_FLAGS, 0, 0, 0, same),
            item(WORKLOAD_BALANCE_INDEX, 0.8, 0.81, 0.01, same),
        ],
        [
            item(SCHEDULE_VARIANCE, 4, 2, -2, KpiStatus.BETTER),
            item(OVERLOAD_FLAGS, 0, 1, 1, KpiStatus.WORSE),
            item(WORKLOAD_BALANCE_INDEX, 0.8, 0.79, -0.01, same),
        ],
    ]

    current, a, b = kpi_effect_scores(comparisons)

    assert (current.score, a.score, b.score) == (0.5, 1.0, 0.2)
    assert b.facts == {
        "better": 1,
        "same": 1,
        "worse": 1,
        "changes": {SCHEDULE_VARIANCE: -2, OVERLOAD_FLAGS: 1},
    }


def test_kpi_effect_for_missing_values_follows_status():
    """SV kosong berarti proyek tidak dapat diluncurkan: lebih buruk dari nilai apa pun."""
    comparisons = [
        [item(SCHEDULE_VARIANCE, 4, 4, 0, KpiStatus.SAME)],
        [item(SCHEDULE_VARIANCE, 4, None, None, KpiStatus.WORSE)],
        [item(SCHEDULE_VARIANCE, 4, 1, -3, KpiStatus.BETTER)],
    ]

    assert [v.score for v in kpi_effect_scores(comparisons)] == [0.0, 0.0, 1.0]


def test_kpi_effect_is_full_when_nothing_differs():
    comparisons = [[item(SCHEDULE_VARIANCE, 4, 4, 0, KpiStatus.SAME)]] * 2

    assert [v.score for v in kpi_effect_scores(comparisons)] == [1.0, 1.0]


# --- penanda dan bobot -------------------------------------------------------------------------


def test_no_action_needed_and_no_suitable_candidate():
    # RES-002 tanpa task tidak hadir: tidak ada yang memburuk dan tidak ada yang dialihkan.
    params = {**ABSENCE, "resource_id": "RES-002"}

    ranking = ranked(ABSENCE_SNAPSHOT, Situation.MEMBER_ABSENCE, params)

    assert ranking.no_action_needed
    assert ranking.no_suitable_candidate
    assert [(c.is_current, c.rank) for c in ranking.candidates] == [(True, 1)]
    assert ranking.top() == []


@pytest.mark.parametrize(
    "weights",
    [
        {"role_match": -0.1},
        {"role_match": 0, "available_capacity": 0, "current_work": 0, "kpi_effect": 0},
        {"kpi_effect": float("nan")},
    ],
)
def test_invalid_weights_are_rejected(weights):
    with pytest.raises(ValueError):
        ScoringWeights(**weights)


def test_default_weights_are_equal():
    assert DEFAULT_WEIGHTS.as_json() == {
        "role_match": 0.25,
        "available_capacity": 0.25,
        "current_work": 0.25,
        "kpi_effect": 0.25,
    }
