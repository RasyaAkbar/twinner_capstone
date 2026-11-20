"""Pembuat baseline snapshot kecil untuk tes mesin simulasi (SCRUM-24 s.d. SCRUM-29).

Setiap tes menyusun dunianya sendiri dari baris D-01 s.d. D-11 yang dibuat tangan, supaya
hasil penjadwalan dapat dihitung manual. Bawaan yang memudahkan hitungan:

- Simulasi mulai Senin 2026-01-05 (`START`); tanpa hari libur kecuali disebut.
- Peran ROLE-BE dan ROLE-FE dengan `capacity_reference` 1.0, dan level CX-M dengan
  `load_weight` 1.0: satu task memenuhi kapasitas anggota, jadi anggota mengerjakan satu task
  pada satu waktu dengan kecepatan satu hari effort per hari kerja.
- Proyek PRJ-001 aktif (development), prioritas 1, mulai 2026-01-05, target 2026-01-30.

Minggu pertama: Sen 05, Sel 06, Rab 07, Kam 08, Jum 09; minggu kedua: Sen 12 s.d. Jum 16.
"""

from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.modules.simulasi.sim_world import SimWorld, build_world
from app.modules.simulasi.snapshot_schemas import BaselineSnapshotData

START = date(2026, 1, 5)


def d(day: int, month: int = 1, year: int = 2026) -> date:
    return date(year, month, day)


def role(role_id: str = "ROLE-BE", capacity: float = 1.0) -> dict[str, Any]:
    return {
        "role_id": role_id,
        "role_name": role_id,
        "capacity_reference": capacity,
        "description": None,
    }


def complexity(
    complexity_id: str = "CX-M", min_days: float = 2, max_days: float = 6, weight: float = 1.0
) -> dict[str, Any]:
    return {
        "complexity_id": complexity_id,
        "complexity_name": complexity_id,
        "min_days": min_days,
        "max_days": max_days,
        "load_weight": weight,
    }


def project(
    project_id: str = "PRJ-001",
    rank: int = 1,
    status: str = "development",
    start: str = "2026-01-05",
    target: str = "2026-01-30",
) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "project_name": f"Proyek {project_id}",
        "process_owner": "Unit Uji",
        "priority_rank": rank,
        "start_date": start,
        "target_launch_date": target,
        "actual_launch_date": None,
        "status": status,
        "dor_fulfilled": True,
    }


def resource(
    resource_id: str,
    role_id: str = "ROLE-BE",
    join: str = "2024-01-02",
    secondary: str | None = None,
) -> dict[str, Any]:
    return {
        "resource_id": resource_id,
        "resource_name": f"Anggota {resource_id}",
        "primary_role_id": role_id,
        "secondary_role_ids": secondary,
        "cross_role_capable": False,
        "join_date": join,
        "seniority": "mid",
    }


def task(
    task_id: str,
    days: float = 2,
    *,
    project_id: str = "PRJ-001",
    role_id: str = "ROLE-BE",
    start: str = "2026-01-05",
    finish: str | None = None,
    status: str = "not_started",
    rank: int = 1,
    complexity_id: str = "CX-M",
    actual_start: str | None = None,
    actual_finish: str | None = None,
) -> dict[str, Any]:
    planned_start = date.fromisoformat(start)
    return {
        "task_id": task_id,
        "project_id": project_id,
        "feature_name": "Fitur uji",
        "task_name": f"Task {task_id}",
        "required_role_id": role_id,
        "complexity_id": complexity_id,
        "priority_rank": rank,
        "estimated_days": days,
        "phase": "be_dev",
        "planned_start": start,
        "planned_finish": finish or (planned_start + timedelta(days=max(0, round(days) - 1))),
        "actual_start": actual_start,
        "actual_finish": actual_finish,
        "status": status,
        "qa_reject_count": 0,
        "dod_fulfilled": status == "done",
    }


def dependency(
    dependency_id: str, task_id: str, depends_on: str, kind: str = "hard", factor: float = 1.0
) -> dict[str, Any]:
    return {
        "dependency_id": dependency_id,
        "task_id": task_id,
        "depends_on_task_id": depends_on,
        "dependency_type": kind,
        "propagation_factor": factor,
    }


def assignment(
    assignment_id: str,
    task_id: str,
    resource_id: str,
    role_id: str = "ROLE-BE",
    assigned: str = "2026-01-02",
    released: str | None = None,
) -> dict[str, Any]:
    return {
        "assignment_id": assignment_id,
        "task_id": task_id,
        "resource_id": resource_id,
        "role_in_task": role_id,
        "assigned_date": assigned,
        "released_date": released,
        "is_takeover": False,
    }


def absence(
    availability_id: str,
    resource_id: str,
    start: str,
    end: str,
    kind: str = "planned_leave",
    planned: bool = True,
) -> dict[str, Any]:
    return {
        "availability_id": availability_id,
        "resource_id": resource_id,
        "date_start": start,
        "date_end": end,
        "absence_type": kind,
        "is_known_at_planning": planned,
    }


def workload(
    item_id: str,
    resource_id: str,
    received: str,
    started: str | None = None,
    resolved: str | None = None,
    severity: str | None = "medium",
    hotfix: bool = False,
) -> dict[str, Any]:
    return {
        "item_id": item_id,
        "resource_id": resource_id,
        "item_type": "ticket",
        "related_project_id": None,
        "received_date": received,
        "started_date": started,
        "resolved_date": resolved,
        "severity": severity,
        "is_hotfix": hotfix,
    }


def holiday(day: str, name: str = "Libur uji") -> dict[str, Any]:
    return {"holiday_date": day, "holiday_name": name}


def snapshot(**tables: list[dict[str, Any]]) -> BaselineSnapshotData:
    """Snapshot dengan bawaan di atas; tabel yang diisi menggantikan bawaannya."""
    data: dict[str, list[dict[str, Any]]] = {
        "roles": [role("ROLE-BE"), role("ROLE-FE")],
        "complexity_levels": [complexity()],
        "holidays": [],
        "projects": [project()],
        "resources": [],
        "tasks": [],
        "task_dependencies": [],
        "assignments": [],
        "workload_items": [],
        "availability": [],
        "change_events": [],
    }
    data.update(tables)
    return BaselineSnapshotData.model_validate(
        {
            "snapshot_id": 1,
            "data_source": "synthetic",
            "label": "baseline uji simulasi",
            "imported_by": "uji",
            "created_at": datetime(2026, 1, 2, tzinfo=timezone.utc),
            "row_counts": {table: len(rows) for table, rows in data.items()},
            "checksum": "0" * 64,
            "is_current": True,
            **data,
        }
    )


def world(start: date = START, **tables: list[dict[str, Any]]) -> SimWorld:
    return build_world(snapshot(**tables), start)
