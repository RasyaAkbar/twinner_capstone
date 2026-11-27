"""Aturan perubahan skenario untuk delapan situasi keputusan: langkah 2 §IV.2.3 (SCRUM-24).

`apply_scenario` menyalin dunia baseline secara dalam (deep copy) lalu menjalankan satu fungsi
aturan per situasi (Tabel 4.2, parameter Tabel 4.4) pada salinan itu. Dunia baseline tidak
pernah diubah, jadi baseline dan skenario dapat disimulasikan berdampingan.

Parameter sudah lolos `scenario_params.validate_parameters`, sehingga setiap ID yang dirujuk ada
di baseline. Setiap aturan mencatat proyek, task, dan anggota yang langsung disentuhnya
(`targets`) serta asumsi yang dipakai (`notes`) untuk layar hasil simulasi (UC-04).

Perubahan pada salinan baseline per situasi (rincian di docs/SIMULATION_ENGINE.md):

- Change Request: task dikeluarkan (dropped) atau ditambah dengan estimasinya, mulai awal
  periode terdampak dengan prioritas tertinggi di proyeknya; target rilis diundur ke target CR
  bila lebih lambat.
- Member Absence: ketidakhadiran ditambahkan; task anggota itu kehilangan kapasitas dan
  menunggu selama periode tersebut.
- New Member: anggota baru yang kapasitasnya naik linier selama onboarding dan dapat mengambil
  task belum mulai di perannya; waktu mentor opsional.
- Rotation / Withdrawal: penugasan di proyek asal dilepas mulai tanggal pindah; anggota
  menambah kapasitas proyek tujuan, atau keluar dari tim dan melepas semua task-nya.
- Task Overrun: estimasi diganti estimasi revisi dan sisa pekerjaan diganti sisa yang diisi.
- Task Blocker: task ditahan selama hari blokir sejak siap dikerjakan; pemiliknya beralih ke
  task independen.
- Priority Change: urutan prioritas proyek aktif disusun ulang, proyek on hold diaktifkan,
  serta estimasi task terdampak dan target proyek disesuaikan.
- Critical Bug: item perbaikan bug yang mendahului task proyek anggota yang ditugasi selama
  lama perbaikan.
"""

import math
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.modules.data.enums import ProjectStatus, TaskStatus
from app.modules.data.workdays import count_working_days
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import (
    DEFAULT_MENTOR_SHARE,
    ChangeRequestParams,
    CriticalBugParams,
    MemberAbsenceParams,
    MemberRotationParams,
    NewMemberParams,
    Params,
    PriorityChangeParams,
    TaskBlockerParams,
    TaskOverrunParams,
)
from app.modules.simulasi.sim_world import (
    MENTOR_GAIN,
    Absence,
    Help,
    Mentoring,
    NonProjectItem,
    Ramp,
    Release,
    SimResource,
    SimTask,
    SimWorld,
    item_share,
    mentor_role_factor,
    next_id,
    onboarding_working_days,
)

ONE_DAY = timedelta(days=1)
ADDED_TASK_PREFIX = "CR"
NEW_MEMBER_PREFIX = "NEW"


@dataclass
class Targets:
    """Yang langsung disentuh aturan skenario."""

    projects: set[str] = field(default_factory=set)
    tasks: set[str] = field(default_factory=set)
    resources: set[str] = field(default_factory=set)

    def add_tasks(self, world: SimWorld, task_ids: list[str] | set[str]) -> None:
        for task_id in task_ids:
            self.tasks.add(task_id)
            task = world.tasks.get(task_id)
            if task is not None:
                self.projects.add(task.project_id)


@dataclass
class AppliedScenario:
    world: SimWorld
    targets: Targets = field(default_factory=Targets)
    notes: list[str] = field(default_factory=list)


def apply_scenario(world: SimWorld, situation: Situation | str, params: Params) -> AppliedScenario:
    """Terapkan parameter skenario pada salinan dalam `world`; `world` tidak berubah."""
    applied = AppliedScenario(deepcopy(world))
    RULES[Situation(situation)](applied.world, params, applied)
    return applied


# --- aturan per situasi ----------------------------------------------------------------------


def change_request(w: SimWorld, p: ChangeRequestParams, out: AppliedScenario) -> None:
    project = w.projects[p.project_id]
    out.targets.projects.add(project.project_id)
    for task_id in p.removed_task_ids:
        task = w.tasks[task_id]
        task.status = TaskStatus.DROPPED.value
        task.removed = True
        out.targets.tasks.add(task_id)

    earliest = max(p.affected_period.start, project.start_date)
    for new in p.added_tasks:
        task_id = next_id(w.tasks, ADDED_TASK_PREFIX)
        w.tasks[task_id] = SimTask(
            task_id=task_id,
            project_id=project.project_id,
            name=new.task_name,
            required_role_id=new.required_role_id,
            complexity_id=infer_complexity(w, new.estimated_days),
            priority_rank=1,
            estimated_days=new.estimated_days,
            status=TaskStatus.NOT_STARTED.value,
            planned_start=p.affected_period.start,
            planned_finish=p.target_date,
            earliest_start=earliest,
            effort_fixed=True,
            added=True,
        )
        out.targets.tasks.add(task_id)

    parts = []
    if p.added_tasks:
        days = sum(t.estimated_days for t in p.added_tasks)
        parts.append(f"{len(p.added_tasks)} task ditambahkan ({days:g} hari kerja)")
    if p.removed_task_ids:
        parts.append(f"{len(p.removed_task_ids)} task dikeluarkan dari scope")
    out.notes.append(f"Change request {project.project_id}: {', '.join(parts)}.")
    if p.target_date > project.target_launch_date:
        extension = count_working_days(
            project.target_launch_date + ONE_DAY, p.target_date, w.holidays
        )
        out.notes.append(
            f"Target rilis {project.project_id} diundur {extension} hari kerja dari "
            f"{project.target_launch_date.isoformat()} ke {p.target_date.isoformat()} "
            "(perpanjangan sprint)."
        )
        project.target_launch_date = p.target_date


def member_absence(w: SimWorld, p: MemberAbsenceParams, out: AppliedScenario) -> None:
    period = p.absence_period
    w.absences.append(
        Absence(
            resource_id=p.resource_id,
            start=period.start,
            end=period.end,
            absence_type=p.absence_type,
            planned=p.absence_type != "sudden_leave",
        )
    )
    out.targets.resources.add(p.resource_id)
    affected = p.affected_task_ids or [
        t.task_id for t in w.tasks.values() if t.owner == p.resource_id and w.schedulable(t)
    ]
    out.targets.add_tasks(w, affected)
    lost = 0
    if period.end >= w.start_date:
        lost = count_working_days(max(period.start, w.start_date), period.end, w.holidays)
    out.notes.append(
        f"{p.resource_id} tidak hadir ({p.absence_type}) {period.start.isoformat()} s.d. "
        f"{period.end.isoformat()}: {lost} hari kerja tanpa kapasitas; task-nya menunggu."
    )


def new_member(w: SimWorld, p: NewMemberParams, out: AppliedScenario) -> None:
    resource_id = next_id(w.resources, NEW_MEMBER_PREFIX)
    role = w.roles.get(p.role_id)
    days = onboarding_working_days(p.onboarding_weeks)
    w.resources[resource_id] = SimResource(
        resource_id=resource_id,
        name=f"Anggota baru {role.name if role else p.role_id} ({p.seniority.value})",
        primary_role_id=p.role_id,
        secondary_role_ids=(),
        capacity_reference=w.capacity_reference(p.role_id),
        join_date=p.join_date,
        ramp=Ramp(p.join_date, days, p.initial_capacity),
        helps=Help(p.join_date, None),
        added=True,
    )
    out.targets.resources.add(resource_id)
    out.notes.append(
        f"{resource_id} ({p.role_id}, {p.seniority.value}) bergabung {p.join_date.isoformat()}; "
        f"kapasitas naik dari {p.initial_capacity:.0%} ke 100% selama {days} hari kerja dan "
        f"dapat mengambil task {p.role_id} yang belum dimulai."
    )
    if p.mentor_resource_id is not None:
        share = p.mentor_share if p.mentor_share is not None else DEFAULT_MENTOR_SHARE
        mentor = w.resources[p.mentor_resource_id]
        boost = round(MENTOR_GAIN * share * mentor_role_factor(mentor, p.role_id), 6)
        w.mentoring.append(Mentoring(p.mentor_resource_id, resource_id, share, boost))
        out.targets.resources.add(p.mentor_resource_id)
        benefit = (
            f"; pada hari mentor hadir kapasitas {resource_id} bertambah {boost:.0%}"
            if boost > 0
            else f"; {p.mentor_resource_id} tidak memegang peran {p.role_id}, jadi bimbingannya "
            "tidak mempercepat onboarding"
        )
        out.notes.append(
            f"{p.mentor_resource_id} membimbing {resource_id} dengan {share:.0%} kapasitas "
            f"hariannya selama onboarding{benefit}."
        )


def member_rotation(w: SimWorld, p: MemberRotationParams, out: AppliedScenario) -> None:
    resource = w.resources[p.resource_id]
    out.targets.resources.add(resource.resource_id)
    out.targets.projects.add(p.origin_project_id)
    if p.destination_project_id is None:
        resource.leave_date = p.transfer_date
        released = [
            t for t in w.tasks.values() if t.owner == resource.resource_id and w.schedulable(t)
        ]
        _release(released, resource.resource_id, p.transfer_date, out, w)
        out.notes.append(
            f"{resource.resource_id} keluar dari tim mulai {p.transfer_date.isoformat()}; "
            f"{len(released)} task-nya dilepas dan dapat diambil anggota lain dengan peran sama."
        )
        return

    if p.existing_assignment_ids:
        task_ids = [w.assignment_tasks[a] for a in p.existing_assignment_ids]
        released = [
            w.tasks[t]
            for t in task_ids
            if w.tasks[t].owner == resource.resource_id and not w.tasks[t].closed
        ]
    else:
        released = [
            t
            for t in w.tasks.values()
            if t.owner == resource.resource_id
            and t.project_id == p.origin_project_id
            and w.schedulable(t)
        ]
    _release(released, resource.resource_id, p.transfer_date, out, w)
    resource.excluded_projects[p.origin_project_id] = p.transfer_date
    resource.helps = Help(p.transfer_date, p.destination_project_id)
    out.targets.projects.add(p.destination_project_id)
    out.notes.append(
        f"{resource.resource_id} pindah dari {p.origin_project_id} ke "
        f"{p.destination_project_id} mulai {p.transfer_date.isoformat()}: {len(released)} task "
        f"di proyek asal dilepas, dan kapasitasnya menambah {p.destination_project_id}."
    )


def task_overrun(w: SimWorld, p: TaskOverrunParams, out: AppliedScenario) -> None:
    task = w.tasks[p.task_id]
    task.estimated_days = p.revised_days
    task.remaining_days = p.remaining_days
    task.effort_fixed = True
    out.targets.add_tasks(w, [task.task_id])
    out.notes.append(
        f"Estimasi {task.task_id} naik dari {p.original_days:g} menjadi {p.revised_days:g} hari "
        f"kerja; sisa pekerjaan {p.remaining_days:g} hari kerja."
    )


def task_blocker(w: SimWorld, p: TaskBlockerParams, out: AppliedScenario) -> None:
    task = w.tasks[p.task_id]
    task.block_days = math.ceil(p.blocking_days - 1e-9)
    out.targets.add_tasks(w, [task.task_id])
    cause = f"oleh {p.blocking_task_id}" if p.blocking_task_id else "oleh pihak di luar baseline"
    out.notes.append(
        f"{task.task_id} tertahan {task.block_days} hari kerja {cause} sejak siap dikerjakan."
    )
    owner = w.resources.get(task.owner or "")
    switched = []
    for task_id in p.independent_task_ids:
        other = w.tasks[task_id]
        out.targets.add_tasks(w, [task_id])
        if other.started:
            continue
        project = w.projects[other.project_id]
        other.earliest_start = max(w.start_date, project.start_date)
        if owner is not None and owner.can_do(other.required_role_id):
            other.owner = owner.resource_id
            switched.append(task_id)
    if owner is not None and switched:
        out.notes.append(
            f"Selama blokir, {owner.resource_id} beralih ke task independen {', '.join(switched)}."
        )


def priority_change(w: SimWorld, p: PriorityChangeParams, out: AppliedScenario) -> None:
    project = w.projects[p.project_id]
    out.targets.projects.add(project.project_id)
    if not project.active:
        project.status = ProjectStatus.DEVELOPMENT.value
        for task in w.tasks.values():
            if task.project_id == project.project_id and task.status == TaskStatus.ON_HOLD:
                task.status = (
                    TaskStatus.IN_PROGRESS.value if task.started else TaskStatus.NOT_STARTED.value
                )
        out.notes.append(f"Proyek {project.project_id} diaktifkan kembali dari on hold.")
    old_rank = project.priority_rank
    _rerank(w, project.project_id, p.new_priority_rank)
    out.notes.append(
        f"Prioritas {project.project_id} berubah dari {old_rank} menjadi {p.new_priority_rank}; "
        "urutan proyek aktif lain bergeser."
    )

    affected = p.affected_task_ids or [
        t.task_id
        for t in w.tasks.values()
        if t.project_id == project.project_id and w.schedulable(t)
    ]
    out.targets.add_tasks(w, affected)
    if p.estimated_days is not None:
        for task_id in affected:
            task = w.tasks[task_id]
            task.estimated_days = p.estimated_days
            task.effort_fixed = True
        out.notes.append(
            f"Estimasi {len(affected)} task terdampak menjadi {p.estimated_days:g} hari kerja."
        )
    if p.target_date != project.target_launch_date:
        out.notes.append(
            f"Target rilis {project.project_id} berubah dari "
            f"{project.target_launch_date.isoformat()} ke {p.target_date.isoformat()}."
        )
        project.target_launch_date = p.target_date


def critical_bug(w: SimWorld, p: CriticalBugParams, out: AppliedScenario) -> None:
    share = item_share(p.severity.value)
    w.items.append(
        NonProjectItem(
            resource_id=p.resource_id,
            start=w.start_date,
            share=share,
            label=f"Perbaikan bug {p.severity.value}",
            working_days=p.fixing_days,
            source="scenario",
        )
    )
    out.targets.resources.add(p.resource_id)
    out.targets.add_tasks(w, p.affected_task_ids)
    out.notes.append(
        f"Perbaikan bug {p.severity.value} oleh {p.resource_id} selama {p.fixing_days:g} hari "
        f"kerja mulai {w.start_date.isoformat()}, memakai {share:.0%} kapasitas hariannya "
        "sebelum task proyek."
    )


RULES: dict[Situation, Callable[[SimWorld, Any, AppliedScenario], None]] = {
    Situation.CHANGE_REQUEST: change_request,
    Situation.MEMBER_ABSENCE: member_absence,
    Situation.NEW_MEMBER: new_member,
    Situation.MEMBER_ROTATION: member_rotation,
    Situation.TASK_OVERRUN: task_overrun,
    Situation.TASK_BLOCKER: task_blocker,
    Situation.PRIORITY_CHANGE: priority_change,
    Situation.CRITICAL_BUG: critical_bug,
}


# --- bantuan ---------------------------------------------------------------------------------


def infer_complexity(w: SimWorld, days: float) -> str | None:
    """Level kompleksitas dengan rentang hari yang memuat `days`, atau yang paling dekat."""
    if not w.complexity:
        return None

    def distance(level) -> tuple[float, float]:
        gap = max(level.min_days - days, 0.0, days - level.max_days)
        return (gap, level.max_days - level.min_days)

    return min(w.complexity.values(), key=distance).complexity_id


def _release(
    tasks: list[SimTask], resource_id: str, on: date, out: AppliedScenario, w: SimWorld
) -> None:
    for task in tasks:
        task.releases.append(Release(on, resource_id))
    out.targets.add_tasks(w, [t.task_id for t in tasks])


def _rerank(w: SimWorld, project_id: str, new_rank: int) -> None:
    """Sisipkan proyek pada `new_rank` di antara proyek aktif dan nomori ulang 1..n."""
    others = sorted(
        (p for p in w.projects.values() if p.active and p.project_id != project_id),
        key=lambda p: (p.priority_rank, p.project_id),
    )
    ordered = others[: new_rank - 1] + [w.projects[project_id]] + others[new_rank - 1 :]
    for rank, project in enumerate(ordered, start=1):
        project.priority_rank = rank
