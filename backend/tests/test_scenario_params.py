"""SCRUM-22: skema dan aturan parameter delapan situasi keputusan, tanpa basis data.

Baseline uji dibuat tangan supaya setiap aturan dapat dilanggar tepat satu kali:

    PRJ-001 development, prioritas 1, mulai 2026-01-05
        TSK-0001 BE in_progress 5 hari   <- TSK-0002 FE (hard) <- TSK-0004 FE (soft)
        TSK-0003 BE done, TSK-0005 BE (tidak bergantung pada apa pun)
    PRJ-002 planning, prioritas 2, mulai 2026-03-02: TSK-0101 BE, TSK-0102 FE dropped
    PRJ-003 released, PRJ-004 on_hold
    RES-001: TSK-0001, TSK-0003 (dilepas 2026-01-06), TSK-0005, TSK-0101; cuti 2026-04-06..10
    RES-002: TSK-0002, TSK-0004. RES-003: TSK-0101.
"""

import copy
import importlib.util
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from app.modules.data.enums import AbsenceType
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import (
    HORIZON_SLACK_DAYS,
    LEAVE_TYPES,
    SITUATIONS,
    BaselineIndex,
    ParameterError,
    validate_parameters,
)
from app.modules.simulasi.scenario_service import COPY_SUFFIX, copy_name
from app.modules.simulasi.snapshot_schemas import BaselineSnapshotData

BACKEND_DIR = Path(__file__).resolve().parents[1]


def _project(pid, status, rank, start, target, actual=None):
    return {
        "project_id": pid,
        "project_name": f"Proyek {pid}",
        "process_owner": "Unit Uji",
        "priority_rank": rank,
        "start_date": start,
        "target_launch_date": target,
        "actual_launch_date": actual,
        "status": status,
        "dor_fulfilled": True,
    }


def _resource(rid, role, seniority):
    return {
        "resource_id": rid,
        "resource_name": f"Anggota {rid}",
        "primary_role_id": role,
        "secondary_role_ids": None,
        "cross_role_capable": False,
        "join_date": "2024-01-02",
        "seniority": seniority,
    }


def _task(tid, pid, role, status, days, start, finish):
    return {
        "task_id": tid,
        "project_id": pid,
        "feature_name": "Fitur uji",
        "task_name": f"Task {tid}",
        "required_role_id": role,
        "complexity_id": "CX-M",
        "priority_rank": 1,
        "estimated_days": days,
        "phase": "be_dev" if role == "ROLE-BE" else "fe_dev",
        "planned_start": start,
        "planned_finish": finish,
        "actual_start": None,
        "actual_finish": None,
        "status": status,
        "qa_reject_count": 0,
        "dod_fulfilled": status == "done",
    }


def _dependency(did, task, depends_on, kind, factor):
    return {
        "dependency_id": did,
        "task_id": task,
        "depends_on_task_id": depends_on,
        "dependency_type": kind,
        "propagation_factor": factor,
    }


def _assignment(aid, task, resource, role, released=None):
    return {
        "assignment_id": aid,
        "task_id": task,
        "resource_id": resource,
        "role_in_task": role,
        "assigned_date": "2026-01-05",
        "released_date": released,
        "is_takeover": False,
    }


def _availability(avid, resource, start, end, kind):
    return {
        "availability_id": avid,
        "resource_id": resource,
        "date_start": start,
        "date_end": end,
        "absence_type": kind,
        "is_known_at_planning": True,
    }


BASELINE = BaselineSnapshotData.model_validate(
    {
        "snapshot_id": 1,
        "data_source": "synthetic",
        "label": "baseline uji",
        "imported_by": "uji",
        "created_at": datetime(2026, 1, 5, tzinfo=timezone.utc),
        "row_counts": {},
        "checksum": "0" * 64,
        "is_current": True,
        "roles": [
            {"role_id": r, "role_name": r, "capacity_reference": 1.0, "description": None}
            for r in ("ROLE-BE", "ROLE-FE")
        ],
        "complexity_levels": [
            {
                "complexity_id": "CX-M",
                "complexity_name": "Medium",
                "min_days": 2,
                "max_days": 5,
                "load_weight": 1.0,
            }
        ],
        "holidays": [],
        "projects": [
            _project("PRJ-001", "development", 1, "2026-01-05", "2026-06-30"),
            _project("PRJ-002", "planning", 2, "2026-03-02", "2026-09-30"),
            _project("PRJ-003", "released", 3, "2025-06-02", "2025-12-15", "2025-12-10"),
            _project("PRJ-004", "on_hold", 4, "2026-02-02", "2026-08-31"),
        ],
        "resources": [
            _resource("RES-001", "ROLE-BE", "senior"),
            _resource("RES-002", "ROLE-FE", "mid"),
            _resource("RES-003", "ROLE-BE", "junior"),
        ],
        "tasks": [
            _task("TSK-0001", "PRJ-001", "ROLE-BE", "in_progress", 5.0, "2026-01-05", "2026-01-09"),
            _task("TSK-0002", "PRJ-001", "ROLE-FE", "not_started", 3.0, "2026-01-12", "2026-01-14"),
            _task("TSK-0003", "PRJ-001", "ROLE-BE", "done", 2.0, "2026-01-05", "2026-01-06"),
            _task("TSK-0004", "PRJ-001", "ROLE-FE", "not_started", 2.0, "2026-01-15", "2026-01-16"),
            _task("TSK-0005", "PRJ-001", "ROLE-BE", "not_started", 4.0, "2026-01-12", "2026-01-15"),
            _task("TSK-0101", "PRJ-002", "ROLE-BE", "not_started", 6.0, "2026-03-02", "2026-03-09"),
            _task("TSK-0102", "PRJ-002", "ROLE-FE", "dropped", 1.0, "2026-03-02", "2026-03-02"),
        ],
        "task_dependencies": [
            _dependency("DEP-001", "TSK-0002", "TSK-0001", "hard", 1.0),
            _dependency("DEP-002", "TSK-0004", "TSK-0002", "soft", 0.5),
        ],
        "assignments": [
            _assignment("ASG-0001", "TSK-0001", "RES-001", "ROLE-BE"),
            _assignment("ASG-0002", "TSK-0002", "RES-002", "ROLE-FE"),
            _assignment("ASG-0003", "TSK-0003", "RES-001", "ROLE-BE", released="2026-01-06"),
            _assignment("ASG-0004", "TSK-0005", "RES-001", "ROLE-BE"),
            _assignment("ASG-0005", "TSK-0101", "RES-003", "ROLE-BE"),
            _assignment("ASG-0006", "TSK-0004", "RES-002", "ROLE-FE"),
            _assignment("ASG-0007", "TSK-0101", "RES-001", "ROLE-BE"),
        ],
        "workload_items": [],
        "availability": [
            _availability("AVL-001", "RES-001", "2026-04-06", "2026-04-10", "planned_leave"),
            _availability("AVL-002", "RES-001", "2026-05-04", "2026-05-08", "rotation_in"),
        ],
        "change_events": [],
    }
)
INDEX = BaselineIndex.build(BASELINE)

# Parameter yang lolos semua aturan, satu per situasi. Kasus salah di bawah mengubah sebagian.
VALID: dict[Situation, dict[str, Any]] = {
    Situation.CHANGE_REQUEST: {
        "project_id": "PRJ-001",
        "added_tasks": [
            {"task_name": "Validasi NIK", "required_role_id": "ROLE-BE", "estimated_days": 2.5}
        ],
        "removed_task_ids": ["TSK-0005"],
        "affected_period": {"start": "2026-01-12", "end": "2026-01-23"},
        "target_date": "2026-01-30",
    },
    Situation.MEMBER_ABSENCE: {
        "resource_id": "RES-001",
        "absence_type": "sudden_leave",
        "absence_period": {"start": "2026-01-12", "end": "2026-01-16"},
        "affected_task_ids": ["TSK-0001", "TSK-0005"],
    },
    Situation.NEW_MEMBER: {
        "role_id": "ROLE-FE",
        "seniority": "junior",
        "join_date": "2026-02-02",
        "onboarding_weeks": 2,
        "initial_capacity": 0.5,
    },
    Situation.MEMBER_ROTATION: {
        "resource_id": "RES-001",
        "origin_project_id": "PRJ-001",
        "destination_project_id": "PRJ-002",
        "transfer_date": "2026-01-19",
        "existing_assignment_ids": ["ASG-0001", "ASG-0004"],
    },
    Situation.TASK_OVERRUN: {
        "task_id": "TSK-0001",
        "original_days": 5,
        "revised_days": 8,
        "remaining_days": 4,
    },
    Situation.TASK_BLOCKER: {
        "task_id": "TSK-0002",
        "blocking_task_id": "TSK-0001",
        "blocking_days": 3,
        "independent_task_ids": ["TSK-0005"],
    },
    Situation.PRIORITY_CHANGE: {
        "project_id": "PRJ-002",
        "new_priority_rank": 1,
        "affected_task_ids": ["TSK-0101"],
        "estimated_days": 6,
        "target_date": "2026-08-31",
    },
    Situation.CRITICAL_BUG: {
        "severity": "critical",
        "fixing_days": 2,
        "resource_id": "RES-001",
        "affected_task_ids": ["TSK-0003", "TSK-0001"],
    },
}


def with_changes(situation: Situation, **changes: Any) -> dict[str, Any]:
    params = copy.deepcopy(VALID[situation])
    params.update(changes)
    return params


def errors_of(situation: Situation, params: dict[str, Any]) -> list[tuple[tuple, str]]:
    outcome = validate_parameters(situation, params, INDEX)
    assert outcome.ok == (not outcome.errors)
    return [(error.loc, error.type) for error in outcome.errors]


# --- registri dan kontrak ----------------------------------------------------------------------


def test_eight_situations_in_table_4_2_order():
    assert list(SITUATIONS) == list(Situation)
    assert [spec.label for spec in SITUATIONS.values()] == [
        "Mid-sprint Change Request",
        "Member Absence",
        "New Member",
        "Member Rotation or Withdrawal",
        "Task Overrun",
        "Task Blocker",
        "Priority Change",
        "Critical Bug",
    ]


def test_migration_lists_the_same_situations():
    path = BACKEND_DIR / "migrations" / "versions" / "0022_twinner_scenario.py"
    spec = importlib.util.spec_from_file_location("migration_0022", path)
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    assert migration.SITUATIONS == tuple(Situation)


def test_leave_types_are_d09_absence_types_without_rotation():
    assert set(LEAVE_TYPES) < {t.value for t in AbsenceType}
    assert not any(t.startswith("rotation") for t in LEAVE_TYPES)


@pytest.mark.parametrize("situation", list(Situation))
def test_json_schema_for_the_form_lists_required_fields(situation):
    model = SITUATIONS[situation].model
    schema = model.model_json_schema()
    required = [name for name, info in model.model_fields.items() if info.is_required()]
    assert schema["required"] == required
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == set(model.model_fields)


def test_index_horizon_and_downstream():
    first, last = INDEX.horizon
    assert first == date(2025, 6, 2)  # mulai proyek paling awal (PRJ-003)
    assert (last - date(2026, 9, 30)).days == HORIZON_SLACK_DAYS  # target terakhir (PRJ-002)
    assert INDEX.downstream("TSK-0001") == {"TSK-0002", "TSK-0004"}
    assert INDEX.downstream("TSK-0005") == set()


def test_error_detail_uses_the_fastapi_shape():
    error = ParameterError(("absence_period", "end"), "Tidak boleh sebelum tanggal mulai", "x")
    assert error.as_detail() == {
        "loc": ["body", "parameters", "absence_period", "end"],
        "msg": "Tidak boleh sebelum tanggal mulai",
        "type": "x",
    }


def test_copy_name_fits_the_name_column():
    assert copy_name("Cuti mendadak RES-001") == "Cuti mendadak RES-001 (salinan)"
    long = copy_name("x" * 200)
    assert len(long) == 200 and long.endswith(COPY_SUFFIX)


# --- aturan bersama untuk semua situasi --------------------------------------------------------


@pytest.mark.parametrize("situation", list(Situation))
def test_valid_parameters_pass(situation):
    outcome = validate_parameters(situation, VALID[situation], INDEX)
    assert outcome.errors == []
    assert outcome.params is not None


@pytest.mark.parametrize("situation", list(Situation))
def test_empty_parameters_report_every_required_field(situation):
    model = SITUATIONS[situation].model
    required = {name for name, info in model.model_fields.items() if info.is_required()}
    outcome = validate_parameters(situation, {}, INDEX)
    assert outcome.params is None
    assert {error.loc for error in outcome.errors} == {(name,) for name in required}
    assert {(error.type, error.message) for error in outcome.errors} == {("missing", "Wajib diisi")}


@pytest.mark.parametrize("situation", list(Situation))
def test_unknown_field_is_rejected(situation):
    params = with_changes(situation, catatan="bukan parameter situasi ini")
    assert errors_of(situation, params) == [(("catatan",), "extra_forbidden")]


@pytest.mark.parametrize("situation", list(Situation))
def test_null_required_field_is_reported_as_missing(situation):
    model = SITUATIONS[situation].model
    name = next(n for n, info in model.model_fields.items() if info.is_required())
    outcome = validate_parameters(situation, with_changes(situation, **{name: None}), INDEX)
    assert [(e.loc, e.message) for e in outcome.errors] == [((name,), "Wajib diisi")]


def test_rules_are_checked_only_after_the_shape_is_valid():
    """Bentuk yang salah dilaporkan sendiri; aturan isi butuh nilai bertipe."""
    params = with_changes(Situation.TASK_OVERRUN, task_id="TSK-9999", revised_days="banyak")
    assert errors_of(Situation.TASK_OVERRUN, params) == [(("revised_days",), "float_parsing")]


def test_dates_must_be_iso_format():
    params = with_changes(Situation.NEW_MEMBER, join_date="02/02/2026")
    outcome = validate_parameters(Situation.NEW_MEMBER, params, INDEX)
    assert [(e.loc, e.message) for e in outcome.errors] == [
        (("join_date",), "Tanggal harus berformat YYYY-MM-DD")
    ]


def test_id_format_error_shows_an_example():
    params = with_changes(Situation.CRITICAL_BUG, resource_id="RES1")
    outcome = validate_parameters(Situation.CRITICAL_BUG, params, INDEX)
    assert [(e.loc, e.message) for e in outcome.errors] == [
        (("resource_id",), "Format tidak sah, contoh RES-001")
    ]


def test_canonical_form_fills_defaults_and_trims_text():
    params = {
        "project_id": "PRJ-001",
        "added_tasks": [
            {"task_name": "  Validasi NIK  ", "required_role_id": "ROLE-BE", "estimated_days": 1}
        ],
        "affected_period": {"start": "2026-01-12", "end": "2026-01-23"},
        "target_date": "2026-01-30",
    }
    outcome = validate_parameters(Situation.CHANGE_REQUEST, params, INDEX)
    assert outcome.errors == []
    assert outcome.params.model_dump(mode="json") == {
        **params,
        "added_tasks": [
            {"task_name": "Validasi NIK", "required_role_id": "ROLE-BE", "estimated_days": 1.0}
        ],
        "removed_task_ids": [],
    }


# --- aturan per situasi ------------------------------------------------------------------------

CASES = [
    # 1. Mid-sprint Change Request
    (
        Situation.CHANGE_REQUEST,
        {"added_tasks": [], "removed_task_ids": []},
        [(("added_tasks",), "no_task_change")],
    ),
    (Situation.CHANGE_REQUEST, {"project_id": "PRJ-999"}, [(("project_id",), "project_not_found")]),
    (
        Situation.CHANGE_REQUEST,
        {"added_tasks": [{"task_name": "QA", "required_role_id": "ROLE-QA", "estimated_days": 1}]},
        [(("added_tasks", 0, "required_role_id"), "role_not_found")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"added_tasks": [{"task_name": "QA", "required_role_id": "ROLE-BE", "estimated_days": 0}]},
        [(("added_tasks", 0, "estimated_days"), "greater_than")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"added_tasks": [{"task_name": " ", "required_role_id": "ROLE-BE", "estimated_days": 1}]},
        [(("added_tasks", 0, "task_name"), "string_too_short")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"removed_task_ids": ["TSK-0101"]},
        [(("removed_task_ids", 0), "task_other_project")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"removed_task_ids": ["TSK-0003"]},
        [(("removed_task_ids", 0), "task_closed")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"removed_task_ids": ["TSK-9999"]},
        [(("removed_task_ids", 0), "task_not_found")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"removed_task_ids": ["TSK-0005", "TSK-0005"]},
        [(("removed_task_ids", 1), "duplicate")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"affected_period": {"start": "2026-01-23", "end": "2026-01-12"}},
        [(("affected_period", "end"), "end_before_start")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"affected_period": {"start": "2025-12-29", "end": "2026-01-09"}},
        [(("affected_period", "start"), "before_project_start")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"target_date": "2026-01-09"},
        [(("target_date",), "target_before_period")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"target_date": "2030-01-01"},
        [(("target_date",), "date_out_of_range")],
    ),
    (
        Situation.CHANGE_REQUEST,
        {"affected_period": {"start": "2026-01-12"}},
        [(("affected_period", "end"), "missing")],
    ),
    # 2. Member Absence
    (
        Situation.MEMBER_ABSENCE,
        {"resource_id": "RES-999"},
        [(("resource_id",), "resource_not_found")],
    ),
    (
        Situation.MEMBER_ABSENCE,
        {"absence_type": "rotation_out"},
        [(("absence_type",), "literal_error")],
    ),
    (
        Situation.MEMBER_ABSENCE,
        {"absence_period": {"start": "2026-04-08", "end": "2026-04-15"}},
        [(("absence_period",), "absence_overlap")],
    ),
    (
        Situation.MEMBER_ABSENCE,
        {"absence_period": {"start": "2026-01-16", "end": "2026-01-12"}},
        [(("absence_period", "end"), "end_before_start")],
    ),
    (
        Situation.MEMBER_ABSENCE,
        {"absence_period": {"start": "2026-01-01", "end": "2027-01-05"}},
        [(("absence_period", "end"), "period_too_long")],
    ),
    (
        Situation.MEMBER_ABSENCE,
        {"absence_period": {"start": "2025-01-06", "end": "2025-01-10"}},
        [
            (("absence_period", "start"), "date_out_of_range"),
            (("absence_period", "end"), "date_out_of_range"),
        ],
    ),
    (
        Situation.MEMBER_ABSENCE,
        {"affected_task_ids": ["TSK-0002"]},
        [(("affected_task_ids", 0), "task_not_assigned")],
    ),
    (
        Situation.MEMBER_ABSENCE,
        {"affected_task_ids": ["TSK-0003"]},
        [(("affected_task_ids", 0), "task_closed")],
    ),
    # 3. New Member
    (Situation.NEW_MEMBER, {"role_id": "ROLE-QA"}, [(("role_id",), "role_not_found")]),
    (Situation.NEW_MEMBER, {"role_id": "be"}, [(("role_id",), "string_pattern_mismatch")]),
    (Situation.NEW_MEMBER, {"seniority": "lead"}, [(("seniority",), "enum")]),
    (Situation.NEW_MEMBER, {"onboarding_weeks": 3}, [(("onboarding_weeks",), "less_than_equal")]),
    (
        Situation.NEW_MEMBER,
        {"onboarding_weeks": 0.5},
        [(("onboarding_weeks",), "greater_than_equal")],
    ),
    (Situation.NEW_MEMBER, {"initial_capacity": 0}, [(("initial_capacity",), "greater_than")]),
    (Situation.NEW_MEMBER, {"initial_capacity": 1.5}, [(("initial_capacity",), "less_than_equal")]),
    (Situation.NEW_MEMBER, {"join_date": "2024-01-01"}, [(("join_date",), "date_out_of_range")]),
    # 4. Member Rotation or Withdrawal
    (
        Situation.MEMBER_ROTATION,
        {"destination_project_id": "PRJ-001"},
        [(("destination_project_id",), "same_project")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"destination_project_id": "PRJ-003"},
        [(("destination_project_id",), "project_released")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"destination_project_id": "PRJ-999"},
        [(("destination_project_id",), "project_not_found")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"origin_project_id": "PRJ-999", "existing_assignment_ids": []},
        [(("origin_project_id",), "project_not_found")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"resource_id": "RES-003", "existing_assignment_ids": []},
        [(("origin_project_id",), "no_assignment_in_origin")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"transfer_date": "2026-01-02"},
        [(("transfer_date",), "before_project_start")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"existing_assignment_ids": ["ASG-0002"]},
        [(("existing_assignment_ids", 0), "assignment_other_member")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"existing_assignment_ids": ["ASG-0007"]},
        [(("existing_assignment_ids", 0), "assignment_other_project")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"existing_assignment_ids": ["ASG-0003"]},
        [(("existing_assignment_ids", 0), "assignment_released")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"existing_assignment_ids": ["ASG-9999"]},
        [(("existing_assignment_ids", 0), "assignment_not_found")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"existing_assignment_ids": ["ASG-0001", "ASG-0001"]},
        [(("existing_assignment_ids", 1), "duplicate")],
    ),
    (
        Situation.MEMBER_ROTATION,
        {"existing_assignment_ids": ["ASG-1"]},
        [(("existing_assignment_ids", 0), "string_pattern_mismatch")],
    ),
    # 5. Task Overrun
    (Situation.TASK_OVERRUN, {"task_id": "TSK-0003"}, [(("task_id",), "task_closed")]),
    (Situation.TASK_OVERRUN, {"task_id": "TSK-0102"}, [(("task_id",), "task_closed")]),
    (Situation.TASK_OVERRUN, {"task_id": "TSK-9999"}, [(("task_id",), "task_not_found")]),
    (Situation.TASK_OVERRUN, {"original_days": 4}, [(("original_days",), "original_mismatch")]),
    (Situation.TASK_OVERRUN, {"revised_days": 5}, [(("revised_days",), "not_an_overrun")]),
    (
        Situation.TASK_OVERRUN,
        {"remaining_days": 9},
        [(("remaining_days",), "remaining_exceeds_revised")],
    ),
    (Situation.TASK_OVERRUN, {"revised_days": 400}, [(("revised_days",), "less_than_equal")]),
    (Situation.TASK_OVERRUN, {"original_days": 0}, [(("original_days",), "greater_than")]),
    # 6. Task Blocker
    (
        Situation.TASK_BLOCKER,
        {"blocking_task_id": "TSK-0002"},
        [(("blocking_task_id",), "self_block")],
    ),
    (
        Situation.TASK_BLOCKER,
        {"blocking_task_id": "TSK-0005"},
        [(("blocking_task_id",), "not_a_dependency")],
    ),
    (
        Situation.TASK_BLOCKER,
        {"blocking_task_id": "TSK-9999"},
        [(("blocking_task_id",), "task_not_found")],
    ),
    (
        Situation.TASK_BLOCKER,
        {"independent_task_ids": ["TSK-0002"]},
        [(("independent_task_ids", 0), "blocked_task_listed")],
    ),
    (
        Situation.TASK_BLOCKER,
        {"independent_task_ids": ["TSK-0005", "TSK-0004"]},
        [(("independent_task_ids", 1), "depends_on_blocked")],
    ),
    (
        # TSK-0004 bergantung pada TSK-0001 secara tidak langsung (lewat TSK-0002).
        Situation.TASK_BLOCKER,
        {"task_id": "TSK-0001", "blocking_task_id": None, "independent_task_ids": ["TSK-0004"]},
        [(("independent_task_ids", 0), "depends_on_blocked")],
    ),
    (
        Situation.TASK_BLOCKER,
        {"independent_task_ids": ["TSK-0003"]},
        [(("independent_task_ids", 0), "task_closed")],
    ),
    (
        Situation.TASK_BLOCKER,
        {"task_id": "TSK-0003", "blocking_task_id": None, "independent_task_ids": []},
        [(("task_id",), "task_closed")],
    ),
    (Situation.TASK_BLOCKER, {"blocking_days": 0}, [(("blocking_days",), "greater_than")]),
    # 7. Priority Change
    (
        Situation.PRIORITY_CHANGE,
        {"project_id": "PRJ-003", "affected_task_ids": []},
        [(("project_id",), "project_released")],
    ),
    (
        Situation.PRIORITY_CHANGE,
        {"project_id": "PRJ-999"},
        [(("project_id",), "project_not_found")],
    ),
    (
        Situation.PRIORITY_CHANGE,
        {"new_priority_rank": 2},
        [(("new_priority_rank",), "rank_unchanged")],
    ),
    (
        Situation.PRIORITY_CHANGE,
        {"new_priority_rank": 3},
        [(("new_priority_rank",), "rank_out_of_range")],
    ),
    (
        # Proyek on_hold yang diaktifkan boleh mengambil urutan setelah dua proyek aktif ...
        Situation.PRIORITY_CHANGE,
        {"project_id": "PRJ-004", "new_priority_rank": 4, "affected_task_ids": []},
        [(("new_priority_rank",), "rank_out_of_range")],
    ),
    (
        Situation.PRIORITY_CHANGE,
        {"new_priority_rank": 0},
        [(("new_priority_rank",), "greater_than_equal")],
    ),
    (
        Situation.PRIORITY_CHANGE,
        {"target_date": "2026-02-27"},
        [(("target_date",), "before_project_start")],
    ),
    (
        Situation.PRIORITY_CHANGE,
        {"affected_task_ids": ["TSK-0001"]},
        [(("affected_task_ids", 0), "task_other_project")],
    ),
    (
        Situation.PRIORITY_CHANGE,
        {"affected_task_ids": ["TSK-0102"]},
        [(("affected_task_ids", 0), "task_closed")],
    ),
    (Situation.PRIORITY_CHANGE, {"estimated_days": -1}, [(("estimated_days",), "greater_than")]),
    # 8. Critical Bug
    (
        Situation.CRITICAL_BUG,
        {"resource_id": "RES-999"},
        [(("resource_id",), "resource_not_found")],
    ),
    (
        Situation.CRITICAL_BUG,
        {"affected_task_ids": ["TSK-9999"]},
        [(("affected_task_ids", 0), "task_not_found")],
    ),
    (
        Situation.CRITICAL_BUG,
        {"affected_task_ids": ["TSK-0001", "TSK-0001"]},
        [(("affected_task_ids", 1), "duplicate")],
    ),
    (Situation.CRITICAL_BUG, {"severity": "blocker"}, [(("severity",), "enum")]),
    (Situation.CRITICAL_BUG, {"fixing_days": -1}, [(("fixing_days",), "greater_than")]),
    (Situation.CRITICAL_BUG, {"fixing_days": "dua"}, [(("fixing_days",), "float_parsing")]),
]


@pytest.mark.parametrize(
    ("situation", "changes", "expected"),
    CASES,
    ids=[f"{s.value}-{'-'.join(map(str, e[0][0]))}-{e[0][1]}" for s, _, e in CASES],
)
def test_rule_violation(situation, changes, expected):
    assert errors_of(situation, with_changes(situation, **changes)) == expected


def test_every_situation_has_rule_cases():
    assert {situation for situation, _, _ in CASES} == set(Situation)


# Variasi yang sah: aturan tidak boleh terlalu ketat.
ALSO_VALID = [
    # CR yang hanya menambah task, tanpa task yang dikeluarkan.
    (Situation.CHANGE_REQUEST, {"removed_task_ids": []}),
    # CR yang hanya mengeluarkan task.
    (Situation.CHANGE_REQUEST, {"added_tasks": []}),
    # Ketidakhadiran boleh bertumpuk dengan rotation_in (kedatangan, bukan ketidakhadiran).
    (Situation.MEMBER_ABSENCE, {"absence_period": {"start": "2026-05-04", "end": "2026-05-05"}}),
    # Ketidakhadiran tepat sehari setelah cuti yang sudah ada.
    (Situation.MEMBER_ABSENCE, {"absence_period": {"start": "2026-04-11", "end": "2026-04-11"}}),
    # Task terdampak kosong: simulasi memakai semua task aktif anggota.
    (Situation.MEMBER_ABSENCE, {"affected_task_ids": []}),
    (Situation.MEMBER_ABSENCE, {"absence_type": "maternity_leave"}),
    (Situation.NEW_MEMBER, {"onboarding_weeks": 1, "initial_capacity": 1}),
    (Situation.NEW_MEMBER, {"onboarding_weeks": 1.5}),
    # Withdrawal: keluar dari tim tanpa proyek tujuan.
    (Situation.MEMBER_ROTATION, {"destination_project_id": None, "existing_assignment_ids": []}),
    # Pindah ke proyek on_hold diperbolehkan; hanya proyek yang sudah rilis yang ditolak.
    (Situation.MEMBER_ROTATION, {"destination_project_id": "PRJ-004"}),
    # Sisa pekerjaan boleh sama dengan estimasi baru (belum dikerjakan sama sekali).
    (Situation.TASK_OVERRUN, {"remaining_days": 8}),
    (Situation.TASK_OVERRUN, {"original_days": 5.0, "revised_days": 5.5, "remaining_days": 0.5}),
    # Penghambat di luar baseline (vendor, misalnya).
    (Situation.TASK_BLOCKER, {"blocking_task_id": None}),
    # Task penghambat sendiri boleh dikerjakan selama blokir.
    (Situation.TASK_BLOCKER, {"independent_task_ids": ["TSK-0001", "TSK-0005"]}),
    (Situation.PRIORITY_CHANGE, {"estimated_days": None, "affected_task_ids": []}),
    # Proyek on_hold boleh diaktifkan kembali dengan prioritas setelah proyek aktif.
    (
        Situation.PRIORITY_CHANGE,
        {"project_id": "PRJ-004", "new_priority_rank": 3, "affected_task_ids": []},
    ),
    # Bug boleh ditemukan di task yang sudah selesai atau dibatalkan.
    (Situation.CRITICAL_BUG, {"affected_task_ids": ["TSK-0003", "TSK-0102"]}),
    (Situation.CRITICAL_BUG, {"severity": "low", "affected_task_ids": []}),
]


@pytest.mark.parametrize(("situation", "changes"), ALSO_VALID)
def test_rules_are_not_too_strict(situation, changes):
    assert errors_of(situation, with_changes(situation, **changes)) == []


def test_baseline_without_projects_skips_the_date_range_check():
    empty = BaselineIndex.build(
        BASELINE.model_copy(update={"projects": [], "tasks": [], "task_dependencies": []})
    )
    assert empty.horizon is None
    params = with_changes(Situation.NEW_MEMBER, join_date="2099-01-01")
    assert validate_parameters(Situation.NEW_MEMBER, params, empty).errors == []
