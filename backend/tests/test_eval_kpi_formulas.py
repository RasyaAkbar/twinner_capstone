"""SCRUM-32: rumus KPI evaluasi dan pembandingnya, dengan contoh hitungan manual.

Setiap tes memberi masukan kecil langsung ke rumus (tanpa simulasi) dan membandingkan dengan
angka yang dihitung tangan di komentar.
"""

from datetime import date

import pytest

from app.modules.evaluasi.kpi_calculator import (
    Duration,
    cascade_delay,
    estimate_accuracy,
    launched_on_time,
    overload_flags,
    portfolio_sv,
    rli,
    sustained_overload,
    workload_balance,
)
from app.modules.evaluasi.kpi_comparator import Candidate, KpiOutcome, compare, rank
from app.modules.evaluasi.kpi_definitions import (
    BLOCKED_RATIO,
    ESTIMATE_ACCURACY,
    KPI_BY_KEY,
    KPI_DEFINITIONS,
    ON_TIME_PROBABILITY,
    REALLOCATED_RATIO,
    SCHEDULE_VARIANCE,
    WORKLOAD_BALANCE_INDEX,
    Direction,
    EvaluationConfig,
    KpiStatus,
)

WEEKS = [
    date(2026, 1, 5),
    date(2026, 1, 12),
    date(2026, 1, 19),
    date(2026, 1, 26),
    date(2026, 2, 2),
]
DEFAULT = EvaluationConfig()


def test_kpi_list_follows_the_agreed_fr_rows():
    assert [d.fr for d in KPI_DEFINITIONS] == [
        "FR-04.1",
        "FR-04.2",
        "FR-04.3",
        "FR-04.4",
        "FR-04.5",
        "FR-04.6",
        "FR-04.7",
        "FR-04.8",
        "FR-04.9",
        "FR-04.10",
        "FR-04.10",
    ]
    assert KPI_BY_KEY[ESTIMATE_ACCURACY].direction == Direction.TARGET
    assert KPI_BY_KEY[ESTIMATE_ACCURACY].target == 1.0


def test_default_parameters_follow_the_csv():
    assert (DEFAULT.wp, DEFAULT.wo, DEFAULT.wt) == (1.0, 0.5, 0.3)
    assert DEFAULT.overload_threshold == 1.0
    assert DEFAULT.soi_min_weeks == 2
    assert DEFAULT.hard_factor == 1.0
    # Kosong: faktor soft dari D-06 propagation_factor (bawaan 0.3); kapasitas dari D-03.
    assert DEFAULT.soft_factor is None and DEFAULT.capacity_reference is None


def test_config_rejects_unknown_or_negative_tolerances():
    with pytest.raises(ValueError, match="tidak dikenal"):
        EvaluationConfig(tolerances={"sprint_velocity": 1})
    with pytest.raises(ValueError, match="negatif"):
        EvaluationConfig(tolerances={SCHEDULE_VARIANCE: -1})
    resolved = EvaluationConfig(tolerances={SCHEDULE_VARIANCE: 2}).resolved()
    assert resolved.tolerances[SCHEDULE_VARIANCE] == 2
    assert resolved.tolerances[BLOCKED_RATIO] == KPI_BY_KEY[BLOCKED_RATIO].tolerance


# --- FR-04.1, FR-04.2 ----------------------------------------------------------------------------


def test_portfolio_sv_is_the_latest_project():
    assert portfolio_sv({"PRJ-001": -2, "PRJ-002": 3, "PRJ-003": 0}) == 3.0
    assert launched_on_time({"PRJ-001": -2, "PRJ-002": 0}) is True
    assert launched_on_time({"PRJ-001": -2, "PRJ-002": 1}) is False


def test_project_that_cannot_launch_has_no_portfolio_sv():
    assert portfolio_sv({"PRJ-001": -2, "PRJ-002": None}) is None
    assert launched_on_time({"PRJ-001": -2, "PRJ-002": None}) is False
    assert portfolio_sv({}) is None


# --- FR-04.3 s.d. FR-04.6 ------------------------------------------------------------------------


def test_rli_weights_the_three_puti_load_categories():
    # (1.0 x 2 task + 0.5 x 1 operasional + 0.3 x 2 tiket) / 3.0 = 3.1 / 3 = 1.0333
    assert rli(2, 1, 2, 3.0, DEFAULT) == pytest.approx(3.1 / 3)
    # Bobot dapat dikonfigurasi: (2 x 2 + 0 + 0) / 3 = 1.3333
    assert rli(2, 1, 2, 3.0, EvaluationConfig(wp=2, wo=0, wt=0)) == pytest.approx(4 / 3)


def test_overload_flag_counts_member_weeks_above_the_threshold():
    loads = {"RES-001": {WEEKS[0]: 1.2, WEEKS[1]: 1.0}, "RES-002": {WEEKS[0]: 1.01}}
    # 1.0 tepat di threshold tidak dihitung.
    assert overload_flags(loads, 1.0) == 2
    assert overload_flags(loads, 1.1) == 1


def test_workload_balance_index_averages_weekly_one_minus_cv():
    loads = {
        "RES-001": {WEEKS[0]: 1.0, WEEKS[1]: 0.5, WEEKS[2]: 0.0},
        "RES-002": {WEEKS[0]: 1.0, WEEKS[1]: 1.5, WEEKS[2]: 0.0},
    }
    wbi, per_week = workload_balance(loads)
    # Minggu 1: [1.0, 1.0] CV 0 -> 1. Minggu 2: [0.5, 1.5] rata-rata 1, simpangan baku populasi
    # 0.5 -> 1 - 0.5 = 0.5. Minggu 3: semua 0, dianggap merata -> 1. Rata-rata = 2.5 / 3.
    assert per_week == {WEEKS[0]: 1.0, WEEKS[1]: 0.5, WEEKS[2]: 1.0}
    assert wbi == pytest.approx(2.5 / 3)
    assert workload_balance({}) == (None, {})


def test_sustained_overload_needs_consecutive_weeks():
    weeks = dict(zip(WEEKS, [1.2, 1.4, 0.8, 1.5, 1.1]))
    # Periode 1: minggu 1-2, ekses 0.2 dan 0.4 -> 2 x 0.3 = 0.6.
    # Periode 2: minggu 4-5, ekses 0.5 dan 0.1 -> 2 x 0.3 = 0.6. Jumlah 1.2.
    assert sustained_overload(weeks, 1.0, 2) == pytest.approx(1.2)
    # Minimum 3 minggu: tidak ada periode yang cukup panjang.
    assert sustained_overload(weeks, 1.0, 3) == 0.0
    # Minimum 1 minggu: semua minggu berlebih dihitung, 0.2 + 0.4 + 0.5 + 0.1.
    assert sustained_overload(weeks, 1.0, 1) == pytest.approx(1.2)


def test_week_without_working_days_breaks_the_overload_period():
    # Minggu 2 tidak ada (anggota cuti seminggu): minggu 1 dan 3 tidak berturut-turut.
    weeks = {WEEKS[0]: 1.5, WEEKS[2]: 1.5}
    assert sustained_overload(weeks, 1.0, 2) == 0.0


# --- FR-04.7 -------------------------------------------------------------------------------------


def test_cascade_delay_uses_the_strongest_path_to_each_downstream_task():
    # A -hard-> B -soft 0.3-> C, A -soft 0.5-> D -hard-> C.
    successors = {
        "A": [("B", 1.0), ("D", 0.5)],
        "B": [("C", 0.3)],
        "D": [("C", 1.0)],
    }
    impact = cascade_delay({"A": 4.0, "B": 2.0, "C": 0.0, "D": 0.0}, successors)
    # A: B menerima 1.0, D 0.5, C max(1.0 x 0.3, 0.5 x 1.0) = 0.5 -> 4 x 2.0 = 8.0.
    # B: C menerima 0.3 -> 2 x 0.3 = 0.6. Task tanpa keterlambatan tidak dihitung.
    assert impact == pytest.approx({"A": 8.0, "B": 0.6})


def test_cascade_delay_ignores_cycles_back_to_the_source():
    impact = cascade_delay({"A": 1.0}, {"A": [("B", 1.0)], "B": [("A", 1.0)]})
    assert impact == {"A": 1.0}


# --- FR-04.8 -------------------------------------------------------------------------------------


def test_estimate_accuracy_aggregates_per_complexity_and_role():
    overall, by_complexity, by_role = estimate_accuracy(
        [
            Duration("CX-M", "ROLE-BE", actual=6, estimate=4),
            Duration("CX-M", "ROLE-FE", actual=3, estimate=3),
            Duration("CX-H", "ROLE-BE", actual=5, estimate=10),
        ]
    )
    # Keseluruhan 14 / 17; CX-M 9 / 7; CX-H 5 / 10; BE 11 / 14; FE 3 / 3.
    assert overall == pytest.approx(14 / 17, abs=1e-6)
    assert by_complexity == pytest.approx({"CX-H": 0.5, "CX-M": 9 / 7}, abs=1e-6)
    assert by_role == pytest.approx({"ROLE-BE": 11 / 14, "ROLE-FE": 1.0}, abs=1e-6)
    assert estimate_accuracy([]) == (None, {}, {})


# --- status --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("baseline", "scenario", "tolerance", "expected"),
    [
        (5, 3, 0, (-2, KpiStatus.BETTER)),
        (5, 5, 0, (0, KpiStatus.SAME)),
        (5, 6, 0, (1, KpiStatus.WORSE)),
        # Selisih 1 masih dalam toleransi 1.
        (5, 6, 1, (1, KpiStatus.SAME)),
        (5, 7, 1, (2, KpiStatus.WORSE)),
    ],
)
def test_lower_is_better(baseline, scenario, tolerance, expected):
    assert compare(KPI_BY_KEY[SCHEDULE_VARIANCE], baseline, scenario, tolerance) == expected


def test_higher_is_better_with_default_tolerance():
    wbi = KPI_BY_KEY[WORKLOAD_BALANCE_INDEX]  # toleransi 0.02
    assert compare(wbi, 0.80, 0.90) == (pytest.approx(0.1), KpiStatus.BETTER)
    assert compare(wbi, 0.80, 0.81)[1] == KpiStatus.SAME
    assert compare(wbi, 0.80, 0.70)[1] == KpiStatus.WORSE


def test_target_direction_compares_the_distance_to_one():
    ear = KPI_BY_KEY[ESTIMATE_ACCURACY]  # target 1.0, toleransi 0.02
    # |1.3 - 1| = 0.3 menjadi |0.9 - 1| = 0.1: lebih dekat 0.2 -> better, walau nilainya turun.
    assert compare(ear, 1.3, 0.9) == (pytest.approx(-0.4), KpiStatus.BETTER)
    # Tepat 1.0 menjadi 0.95: menjauh 0.05 -> worse.
    assert compare(ear, 1.0, 0.95)[1] == KpiStatus.WORSE
    # Menjauh 0.01, dalam toleransi.
    assert compare(ear, 1.0, 1.01)[1] == KpiStatus.SAME


def test_missing_value_that_means_not_achievable_is_worse():
    sv = KPI_BY_KEY[SCHEDULE_VARIANCE]
    assert compare(sv, 2, None) == (None, KpiStatus.WORSE)
    assert compare(sv, None, 2) == (None, KpiStatus.BETTER)
    assert compare(sv, None, None) == (None, KpiStatus.SAME)


def test_missing_value_that_means_nothing_to_measure_is_same():
    # Tidak ada hari tunggu di baseline: rasio pengalihan tidak dapat dibandingkan.
    assert compare(KPI_BY_KEY[REALLOCATED_RATIO], None, 0.5) == (None, KpiStatus.SAME)


def test_median_delta_decides_the_status_of_replications():
    # Median baseline dan skenario sama, tetapi median selisih per replikasi +1 hari.
    assert compare(KPI_BY_KEY[SCHEDULE_VARIANCE], 0, 0, 0, delta=1) == (1, KpiStatus.WORSE)


# --- skenario terbaik ----------------------------------------------------------------------------


def outcome(baseline, scenario, status):
    return KpiOutcome(baseline, scenario, round(scenario - baseline, 6), status)


def test_best_scenario_has_the_highest_status_score():
    B, S, W = KpiStatus.BETTER, KpiStatus.SAME, KpiStatus.WORSE
    candidates = [
        # SV membaik 2 hari, WBI memburuk: skor 1 - 1 = 0.
        Candidate(
            1, {SCHEDULE_VARIANCE: outcome(2, 0, B), WORKLOAD_BALANCE_INDEX: outcome(0.5, 0.4, W)}
        ),
        # Keduanya membaik: skor 2.
        Candidate(
            2, {SCHEDULE_VARIANCE: outcome(2, 1, B), WORKLOAD_BALANCE_INDEX: outcome(0.5, 0.6, B)}
        ),
        # SV tetap, WBI membaik: skor 1.
        Candidate(
            3, {SCHEDULE_VARIANCE: outcome(2, 2, S), WORKLOAD_BALANCE_INDEX: outcome(0.5, 0.7, B)}
        ),
    ]
    ranked = rank(candidates)
    assert [(r.run_id, r.rank, r.status_score) for r in ranked] == [(2, 1, 2), (3, 2, 1), (1, 3, 0)]
    assert [(r.better, r.same, r.worse) for r in ranked] == [(2, 0, 0), (1, 1, 0), (1, 0, 1)]
    # Perbaikan SV: 2, 1, 0 -> ternormalisasi 1, 0.5, 0. WBI: -0.1, 0.1, 0.2 -> 0, 2/3, 1.
    magnitude = {r.run_id: r.magnitude_score for r in ranked}
    assert magnitude == pytest.approx({1: 1.0, 2: 0.5 + 2 / 3, 3: 1.0}, abs=1e-6)
    assert [r.improves_on_baseline for r in ranked] == [True, True, False]


def test_ties_are_broken_by_magnitude_then_run_id_and_weights_change_the_winner():
    B, S = KpiStatus.BETTER, KpiStatus.SAME
    candidates = [
        Candidate(
            7, {SCHEDULE_VARIANCE: outcome(2, 2, S), WORKLOAD_BALANCE_INDEX: outcome(0.5, 0.6, B)}
        ),
        Candidate(
            4, {SCHEDULE_VARIANCE: outcome(2, 0, B), WORKLOAD_BALANCE_INDEX: outcome(0.5, 0.5, S)}
        ),
    ]
    # Skor status 1 dan 1, skor besaran 1 dan 1: run_id lebih kecil menang.
    assert [r.run_id for r in rank(candidates)] == [4, 7]
    # WBI berbobot 2: run 7 berskor 2.
    assert [r.run_id for r in rank(candidates, {WORKLOAD_BALANCE_INDEX: 2})] == [7, 4]
    # SV tidak dinilai (bobot 0): run 7 satu-satunya yang membaik.
    ranked = rank(candidates, {SCHEDULE_VARIANCE: 0})
    assert [(r.run_id, r.status_score, r.better + r.same + r.worse) for r in ranked] == [
        (7, 1, 1),
        (4, 0, 1),
    ]


def test_missing_values_still_count_by_status():
    candidates = [
        Candidate(1, {SCHEDULE_VARIANCE: KpiOutcome(2, None, None, KpiStatus.WORSE)}),
        Candidate(2, {ON_TIME_PROBABILITY: KpiOutcome(0.5, 0.5, 0.0, KpiStatus.SAME)}),
    ]
    assert [(r.run_id, r.status_score) for r in rank(candidates)] == [(2, 0), (1, -1)]
