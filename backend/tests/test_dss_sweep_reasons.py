"""SCRUM-67: alasan setiap kandidat sweep DSS, satu templat per situasi, tanpa basis data.

Baseline dan skenario dipakai ulang dari tes sebelumnya:

- `test_dss_sweep_scoring`: Member Absence RES-001 (01-05..01-09, target PRJ-001 01-09);
  angka kapasitas, keterlambatan, dan beban di sana dihitung manual.
- `test_dss_parameter_levers`: New Member FE bergabung 02-02 (onboarding 02-02..02-16 karena libur
  02-10), Critical Bug 2.5 hari sejak 01-05, dan Priority Change.
- `test_dss_reassignment_levers`: Rotation, Overrun, Blocker, dan Change Request.

Jumlah KPI yang membaik dan memburuk diambil dari pembanding KPI; bagian lain kalimat dihitung
manual.
"""

import pytest

from app.modules.dss import sweep_runner
from app.modules.dss.sweep_candidates import build_candidates
from app.modules.dss.sweep_reasons import explain
from app.modules.dss.sweep_runner import run_sweep
from app.modules.dss.sweep_scoring import Criterion, rank_sweep
from app.modules.simulasi.scenario_models import Situation
from tests import test_dss_parameter_levers as parameter
from tests import test_dss_reassignment_levers as reassignment
from tests import test_dss_sweep_scoring as scoring
from tests.sim_fixtures import START


def reasons(data, situation, params) -> dict[object, str]:
    candidates = build_candidates(situation, params, data, start_date=START)
    sweep = run_sweep(data, candidates, start_date=START)
    ranking = rank_sweep(sweep)
    texts = explain(sweep, ranking)
    by_value: dict[object, str] = {}
    for result, text in zip(sweep.results, texts):
        by_value.setdefault(result.candidate.value, text)
    by_value["_facts"] = {
        c.result.candidate.value: c.criteria[Criterion.KPI_EFFECT].facts for c in ranking.candidates
    }
    return by_value


def counts(facts: dict) -> str:
    return f"secara keseluruhan {facts['better']} KPI membaik dan {facts['worse']} KPI memburuk"


# --- Member Absence (angka dihitung manual) ----------------------------------------------------


def test_absence_reasons():
    text = reasons(scoring.ABSENCE_SNAPSHOT, Situation.MEMBER_ABSENCE, scoring.ABSENCE)
    facts = text["_facts"]

    assert text["RES-002"] == (
        "Anggota RES-002 (RES-002) sesuai peran ROLE-BE; kapasitas kosongnya 4 dari 4 hari kerja "
        "sejak pengalihan sampai task selesai (2026-01-05 s.d. 2026-01-08). Bila TSK-0001 "
        "dialihkan ke Anggota RES-002 (RES-002) mulai 2026-01-05, peluncuran 1 hari kerja sebelum "
        "target, dibandingkan terlambat 4 hari kerja dari target pada pilihan Anda; "
        f"{counts(facts['RES-002'])}. Dampak pada pekerjaan Anggota RES-002 (RES-002) sendiri "
        "dibandingkan pilihan Anda: RLI mingguan tertingginya naik 1."
    )
    assert text["RES-003"] == (
        "Anggota RES-003 (RES-003) sesuai peran ROLE-BE; kapasitas kosongnya 1 dari 4 hari kerja "
        "sejak pengalihan sampai task selesai (2026-01-05 s.d. 2026-01-08). Bila TSK-0001 "
        "dialihkan ke Anggota RES-003 (RES-003) mulai 2026-01-05, peluncuran terlambat 2 hari "
        "kerja dari target, dibandingkan terlambat 4 hari kerja dari target pada pilihan Anda; "
        f"{counts(facts['RES-003'])}. Dampak pada pekerjaan Anggota RES-003 (RES-003) sendiri "
        "dibandingkan pilihan Anda: task miliknya mundur total 4 hari kerja, minggu berbeban "
        "berlebihnya bertambah 1, dan RLI mingguan tertingginya naik 1."
    )
    assert text["RES-004"].startswith(
        "Anggota RES-004 (RES-004) memegang ROLE-BE sebagai peran sekunder; kapasitas kosongnya"
    )
    assert text[None].startswith(
        "Ini skenario pilihan Anda, tanpa pengalihan task. Dibandingkan baseline, peluncuran "
        "terlambat 4 hari kerja dari target, dibandingkan 1 hari kerja sebelum target pada "
        "baseline; "
    )


# --- New Member ---------------------------------------------------------------------------------


def test_mentor_reasons():
    text = reasons(parameter.BASELINE, Situation.NEW_MEMBER, parameter.NEW_MEMBER)

    # Onboarding sesudah task terakhir selesai: kapasitas dibaca dari kalender kapasitas.
    onboarding = "selama onboarding (2026-02-02 s.d. 2026-02-16)"
    assert text["RES-002"] == (
        "Ini skenario pilihan Anda, dengan Anggota RES-002 (RES-002) sebagai mentor. "
        "Anggota RES-002 (RES-002) tidak memegang peran ROLE-FE (mid); kapasitas kosongnya 10 dari "
        f"10 hari kerja {onboarding}. Dibandingkan baseline, jadwal peluncuran tidak berubah; "
        "KPI lain tidak berubah. Pekerjaan Anggota RES-002 (RES-002) sendiri tidak terdampak "
        "dibandingkan baseline."
    )
    assert text[None] == (
        "Tanpa mentor, jadwal peluncuran sama dengan pilihan Anda; KPI lain tidak berubah."
    )
    assert text["RES-001"] == (
        "Anggota RES-001 (RES-001) sesuai peran ROLE-FE (mid); kapasitas kosongnya 10 dari 10 hari "
        f"kerja {onboarding}. Dengan Anggota RES-001 (RES-001) sebagai mentor, jadwal peluncuran "
        "sama dengan pilihan Anda; KPI lain tidak berubah. Pekerjaan Anggota RES-001 (RES-001) "
        "sendiri tidak terdampak dibandingkan baseline."
    )
    # Cuti 02-02..02-13 menyisakan 02-16.
    assert "kapasitas kosongnya 1 dari 10 hari kerja" in text["RES-007"]
    assert text["RES-003"].startswith(
        "Anggota RES-003 (RES-003) memegang ROLE-FE sebagai peran sekunder (mid);"
    )
    assert text["RES-006"] == (
        "Tidak disimulasikan: tidak hadir sepanjang onboarding 2026-02-02 s.d. 2026-02-16: "
        "planned_leave 2026-02-02 s.d. 2026-02-16."
    )


# --- Critical Bug -------------------------------------------------------------------------------


def test_fixer_reasons():
    text = reasons(parameter.BASELINE, Situation.CRITICAL_BUG, parameter.CRITICAL_BUG)

    # Perbaikan bug tercatat sebagai beban non-proyek, jadi RLI mingguan RES-005 naik sebesar
    # bobotnya; jadwal tidak berubah karena RES-005 tidak memegang task.
    assert text["RES-005"] == (
        "Anggota RES-005 (RES-005) sesuai peran ROLE-BE; kapasitas kosongnya 3 dari 3 hari kerja "
        "selama periode perbaikan (2026-01-05 s.d. 2026-01-07). Bila Anggota RES-005 (RES-005) "
        "yang memperbaiki bug, jadwal peluncuran sama dengan pilihan Anda; KPI lain tidak "
        "berubah. Dampak pada pekerjaan Anggota RES-005 (RES-005) sendiri dibandingkan baseline: "
        "RLI mingguan tertingginya naik 0.3."
    )
    assert text["RES-006"].startswith("Anggota RES-006 (RES-006) tidak memegang peran ROLE-BE;")
    assert text["RES-002"].startswith(
        "Ini skenario pilihan Anda, dengan Anggota RES-002 (RES-002) memperbaiki bug."
    )
    assert text["RES-001"] == (
        "Tidak disimulasikan: tidak hadir sepanjang perbaikan 2026-01-05 s.d. 2026-01-07: "
        "sudden_leave 2026-01-05 s.d. 2026-01-07."
    )


# --- Priority Change ----------------------------------------------------------------------------


def test_priority_reasons():
    params = {"project_id": "PRJ-003", "new_priority_rank": 1, "target_date": "2026-06-30"}

    text = reasons(parameter.BASELINE, Situation.PRIORITY_CHANGE, params)

    assert text[1] == (
        "Ini skenario pilihan Anda, dengan PRJ-003 di peringkat prioritas 1. Dibandingkan "
        "baseline, jadwal peluncuran tidak berubah; KPI lain tidak berubah."
    )
    assert text[2] == (
        "Dengan PRJ-003 di peringkat prioritas 2, jadwal peluncuran sama dengan pilihan Anda; "
        "KPI lain tidak berubah."
    )


# --- templat lever pengalihan lainnya -----------------------------------------------------------


@pytest.mark.parametrize(
    ("situation", "params", "value", "action"),
    [
        (
            Situation.MEMBER_ROTATION,
            reassignment.ROTATION,
            "RES-002",
            "Bila task yang dilepas (TSK-0001, TSK-0002, dan TSK-0004) dialihkan ke Anggota "
            "RES-002 (RES-002) mulai 2026-01-12, ",
        ),
        (
            Situation.MEMBER_ROTATION,
            {**reassignment.ROTATION, "destination_project_id": None},
            "RES-002",
            "Bila task yang dilepas (TSK-0001, TSK-0002, TSK-0004, dan 1 task lain) dialihkan ke "
            "Anggota RES-002 (RES-002) mulai 2026-01-12, ",
        ),
        (
            Situation.TASK_OVERRUN,
            reassignment.OVERRUN,
            "RES-002",
            "Bila sisa pekerjaan TSK-0001 dialihkan ke Anggota RES-002 (RES-002), ",
        ),
        (
            Situation.TASK_BLOCKER,
            reassignment.BLOCKER,
            "RES-003",
            "Bila Anggota RES-003 (RES-003) mengambil task independen TSK-0003 selama blokir, ",
        ),
        (
            Situation.CHANGE_REQUEST,
            reassignment.CHANGE_REQUEST,
            "RES-002",
            "Bila task tambahan CR-001 dan CR-003 dikerjakan Anggota RES-002 (RES-002) mulai "
            "2026-01-12, ",
        ),
    ],
)
def test_reassignment_templates(situation, params, value, action):
    text = reasons(reassignment.BASELINE, situation, params)

    assert action in text[value]
    assert text[value].startswith(f"Anggota {value} ({value}) ")
    assert f"Anggota {value} ({value}) sendiri" in text[value]
    assert text[None].startswith("Ini skenario pilihan Anda, tanpa pengalihan task. ")


# --- kandidat gagal ----------------------------------------------------------------------------


def test_failed_candidate_reason(monkeypatch):
    original = sweep_runner.simulate_scenario

    def failing(base, situation, params, actions=()):
        if actions and actions[0].to_resource_id == "RES-003":
            raise ValueError("Jadwal tidak dapat disusun")
        return original(base, situation, params, actions)

    monkeypatch.setattr(sweep_runner, "simulate_scenario", failing)

    text = reasons(scoring.ABSENCE_SNAPSHOT, Situation.MEMBER_ABSENCE, scoring.ABSENCE)

    assert text["RES-003"] == "Gagal disimulasikan: jadwal tidak dapat disusun."
