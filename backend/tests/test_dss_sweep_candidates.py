"""SCRUM-61: kandidat DSS per skenario dan registri lever, tanpa basis data.

Baseline dan parameter valid per situasi dipakai bersama dengan tes parameter skenario
(`tests.test_scenario_params`): RES-001 BE senior, RES-002 FE mid, RES-003 BE junior;
PRJ-001 development dan PRJ-002 planning aktif, PRJ-004 on hold.

Generator lever baru dibuat di SCRUM-62 dan SCRUM-64, jadi tes perilaku sweep memasang generator
tiruan lewat `monkeypatch`.
"""

from dataclasses import replace
from datetime import date

import pytest

from app.core.config import Settings
from app.modules.dss.sweep_candidates import (
    MAX_CANDIDATES,
    SweepReferenceError,
    build_candidates,
)
from app.modules.dss.sweep_levers import LEVERS, LeverKind, LeverOption
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import SITUATIONS, validate_parameters
from app.modules.simulasi.sim_actions import ReassignTasks
from tests.test_scenario_params import INDEX, VALID, with_changes


def use_options(monkeypatch, situation: Situation, *options: LeverOption) -> None:
    """Pasang generator tiruan yang mengusulkan `options` untuk `situation`."""
    spec = replace(LEVERS[situation], options=lambda params, index: options)
    monkeypatch.setitem(LEVERS, situation, spec)


def summary(candidate_set) -> list[tuple]:
    return [(c.value, c.label, c.is_current, c.excluded_reason) for c in candidate_set.candidates]


# --- registri lever ----------------------------------------------------------------------------


def test_every_situation_has_one_lever():
    assert set(LEVERS) == set(Situation)
    for situation, spec in LEVERS.items():
        assert spec.situation == situation
        if spec.kind == LeverKind.PARAMETER:
            # Lever parameter mengganti field yang memang ada di parameter situasinya.
            assert spec.lever in SITUATIONS[situation].model.model_fields


def test_parameter_levers_follow_scrum_61_table():
    parameter_levers = {
        situation: spec.lever
        for situation, spec in LEVERS.items()
        if spec.kind == LeverKind.PARAMETER
    }
    assert parameter_levers == {
        Situation.NEW_MEMBER: "mentor_resource_id",
        Situation.PRIORITY_CHANGE: "new_priority_rank",
        Situation.CRITICAL_BUG: "resource_id",
    }


EXPECTED_CURRENT = {
    Situation.CHANGE_REQUEST: (None, "Tanpa pengalihan"),
    Situation.MEMBER_ABSENCE: (None, "Tanpa pengalihan"),
    Situation.NEW_MEMBER: (None, "Tanpa mentor"),
    Situation.MEMBER_ROTATION: (None, "Tanpa pengalihan"),
    Situation.TASK_OVERRUN: (None, "Tanpa pengalihan"),
    Situation.TASK_BLOCKER: (None, "Tanpa pengalihan"),
    Situation.PRIORITY_CHANGE: (1, "Peringkat prioritas 1"),
    Situation.CRITICAL_BUG: ("RES-001", "Anggota RES-001 (RES-001)"),
}


@pytest.mark.parametrize("situation", list(Situation))
def test_users_own_scenario_is_the_first_candidate(situation):
    result = build_candidates(situation, VALID[situation], INDEX)

    current = result.current
    assert current.is_current and current.eligible
    assert (current.value, current.label) == EXPECTED_CURRENT[situation]
    assert current.actions == ()
    # Parameter acuan adalah bentuk tervalidasi, sama dengan yang disimpan simulation_run.
    expected = validate_parameters(situation, VALID[situation], INDEX).params
    assert current.parameters == expected.model_dump(mode="json")
    assert [c for c in result.candidates if c.is_current] == [current]
    assert not result.truncated


def test_invalid_reference_raises_with_parameter_errors():
    params = with_changes(Situation.NEW_MEMBER, mentor_resource_id="RES-009")

    with pytest.raises(SweepReferenceError) as caught:
        build_candidates(Situation.NEW_MEMBER, params, INDEX)

    assert [error.type for error in caught.value.errors] == ["resource_not_found"]
    assert "RES-009" in str(caught.value)


# --- lever parameter ---------------------------------------------------------------------------


MENTORED = with_changes(Situation.NEW_MEMBER, mentor_resource_id="RES-001", mentor_share=0.3)


def test_parameter_candidates_are_validated_and_ordered(monkeypatch):
    use_options(
        monkeypatch,
        Situation.NEW_MEMBER,
        LeverOption("RES-002"),
        LeverOption("RES-001"),  # pilihan pengguna, sudah menjadi acuan
        LeverOption("RES-009"),  # tidak ada di baseline
        LeverOption(None),  # mentor_share tetap terisi tanpa mentor
        LeverOption("RES-003", excluded_reason="Cuti sepanjang onboarding"),
        LeverOption("RES-002"),  # usulan ganda
    )

    result = build_candidates(Situation.NEW_MEMBER, MENTORED, INDEX)

    assert summary(result) == [
        ("RES-001", "Anggota RES-001 (RES-001)", True, None),
        ("RES-002", "Anggota RES-002 (RES-002)", False, None),
        ("RES-009", "Mentor RES-009", False, "Anggota RES-009 tidak ada di baseline"),
        (None, "Tanpa mentor", False, "Pilih mentor bila bagian waktu mentor diisi"),
        ("RES-003", "Anggota RES-003 (RES-003)", False, "Cuti sepanjang onboarding"),
    ]
    alternative = result.candidates[1]
    assert alternative.parameters["mentor_resource_id"] == "RES-002"
    assert alternative.parameters["mentor_share"] == 0.3
    assert [c.value for c in result.eligible] == ["RES-001", "RES-002"]
    assert [c.value for c in result.excluded] == ["RES-009", None, "RES-003"]
    # Parameter pengguna tidak ikut berubah oleh kandidat lain.
    assert result.current.parameters["mentor_resource_id"] == "RES-001"


def test_option_changes_other_fields_with_the_lever(monkeypatch):
    use_options(
        monkeypatch, Situation.NEW_MEMBER, LeverOption(None, changes={"mentor_share": None})
    )

    result = build_candidates(Situation.NEW_MEMBER, MENTORED, INDEX)

    without_mentor = result.candidates[1]
    assert without_mentor.eligible
    assert without_mentor.label == "Tanpa mentor"
    assert without_mentor.parameters["mentor_resource_id"] is None
    assert without_mentor.parameters["mentor_share"] is None


def test_priority_rank_candidates(monkeypatch):
    params = {"project_id": "PRJ-004", "new_priority_rank": 1, "target_date": "2026-08-31"}
    use_options(
        monkeypatch,
        Situation.PRIORITY_CHANGE,
        *(LeverOption(rank) for rank in (1, 2, 3, 4)),
    )

    result = build_candidates(Situation.PRIORITY_CHANGE, params, INDEX)

    # Dua proyek aktif ditambah PRJ-004 yang diaktifkan: peringkat paling besar 3.
    assert summary(result) == [
        (1, "Peringkat prioritas 1", True, None),
        (2, "Peringkat prioritas 2", False, None),
        (3, "Peringkat prioritas 3", False, None),
        (4, "Peringkat prioritas 4", False, "Paling besar 3 (jumlah proyek aktif)"),
    ]
    assert result.candidates[2].parameters["new_priority_rank"] == 3


def test_parameter_option_cannot_carry_actions(monkeypatch):
    action = ReassignTasks(("TSK-0001",), "RES-003", date(2026, 1, 12))
    use_options(monkeypatch, Situation.NEW_MEMBER, LeverOption("RES-002", actions=(action,)))

    with pytest.raises(ValueError, match="tidak membawa respons"):
        build_candidates(Situation.NEW_MEMBER, MENTORED, INDEX)


# --- lever pengalihan --------------------------------------------------------------------------


def test_reassignment_candidates_keep_parameters_and_carry_actions(monkeypatch):
    action = ReassignTasks(("TSK-0001", "TSK-0005"), "RES-003", date(2026, 1, 12))
    use_options(
        monkeypatch,
        Situation.MEMBER_ABSENCE,
        LeverOption(None),  # tanpa pengalihan = skenario pengguna
        LeverOption("RES-003", actions=(action,)),
        LeverOption("RES-002", excluded_reason="Peran ROLE-FE tidak cocok"),
    )

    result = build_candidates(Situation.MEMBER_ABSENCE, VALID[Situation.MEMBER_ABSENCE], INDEX)

    assert summary(result) == [
        (None, "Tanpa pengalihan", True, None),
        ("RES-003", "Anggota RES-003 (RES-003)", False, None),
        ("RES-002", "Anggota RES-002 (RES-002)", False, "Peran ROLE-FE tidak cocok"),
    ]
    replacement = result.candidates[1]
    assert replacement.parameters == result.current.parameters
    assert replacement.actions == (action,)
    assert replacement.as_json()["actions"] == [
        {
            "type": "reassign_tasks",
            "task_ids": ["TSK-0001", "TSK-0005"],
            "to_resource_id": "RES-003",
            "from_date": "2026-01-12",
        }
    ]


def test_reassignment_option_needs_an_action(monkeypatch):
    use_options(monkeypatch, Situation.MEMBER_ABSENCE, LeverOption("RES-003"))

    with pytest.raises(ValueError, match="harus membawa respons"):
        build_candidates(Situation.MEMBER_ABSENCE, VALID[Situation.MEMBER_ABSENCE], INDEX)


# --- batas kandidat dan urutan -----------------------------------------------------------------


def test_cap_keeps_reference_and_reports_truncation(monkeypatch):
    use_options(
        monkeypatch,
        Situation.NEW_MEMBER,
        LeverOption("RES-002"),
        LeverOption("RES-009"),
        LeverOption("RES-003"),
    )

    capped = build_candidates(Situation.NEW_MEMBER, MENTORED, INDEX, max_candidates=2)
    only_current = build_candidates(Situation.NEW_MEMBER, MENTORED, INDEX, max_candidates=1)

    # Kandidat dikecualikan tidak disimulasikan, jadi tidak ikut dibatasi.
    assert [c.value for c in capped.candidates] == ["RES-001", "RES-002", "RES-009"]
    assert capped.truncated and capped.omitted == 1
    assert [c.value for c in only_current.candidates] == ["RES-001", "RES-009"]
    assert only_current.omitted == 2
    with pytest.raises(ValueError):
        build_candidates(Situation.NEW_MEMBER, MENTORED, INDEX, max_candidates=0)


def test_same_input_gives_same_candidates(monkeypatch):
    use_options(
        monkeypatch,
        Situation.NEW_MEMBER,
        LeverOption("RES-003"),
        LeverOption("RES-002"),
        LeverOption("RES-009"),
    )

    first = build_candidates(Situation.NEW_MEMBER, MENTORED, INDEX)
    second = build_candidates(Situation.NEW_MEMBER, MENTORED, INDEX)

    assert first.candidates == second.candidates
    # Urutan generator dipertahankan; tidak diurutkan ulang menurut nilai.
    assert [c.value for c in first.eligible] == ["RES-001", "RES-003", "RES-002"]


def test_default_cap_matches_setting():
    assert MAX_CANDIDATES == 50
    assert Settings.model_fields["dss_max_candidates"].default == MAX_CANDIDATES
