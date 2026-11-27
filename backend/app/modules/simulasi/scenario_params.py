"""Skema dan aturan parameter skenario per situasi keputusan (SCRUM-22, UC-02/SCRUM-20).

Sumber: Tabel 4.2 (delapan situasi) dan Tabel 4.4 (masukan per situasi). Satu model Pydantic
per situasi; nama field-nya mengikuti kolom Data Dictionary bila merujuk data baseline
(`project_id`, `resource_id`, `task_id`, `role_id`). Tabel lengkapnya ada di
docs/SCENARIO_MANAGER.md.

Validasi berjalan dua tahap dan dipakai bersama oleh API dan formulir UC-02 (skema JSON-nya
dikirim lewat `GET /simulasi/scenarios/situations`):

1. Bentuk (Pydantic): field wajib, tipe, format ID dan tanggal, nilai enum, batas angka.
2. Isi terhadap baseline snapshot skenario (`check_*`): anggota, task, proyek, peran, dan
   penugasan yang dipilih ada di snapshot; periode berurutan; tanggal dalam rentang baseline;
   serta aturan khusus situasi (misalnya durasi revisi overrun harus lebih dari durasi awal).

Tahap 2 tidak menyentuh basis data. `BaselineIndex` dibangun dari `BaselineSnapshotData`,
sehingga aturan dapat dites tanpa PostgreSQL.
"""

import math
from collections import defaultdict, deque
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Annotated, Any, Literal, get_args

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, ValidationError

from app.modules.data.enums import (
    ACTIVE_PROJECT_STATUSES,
    ASSIGNMENT_ID_PATTERN,
    AbsenceType,
    ProjectStatus,
    Seniority,
    Severity,
    TaskStatus,
)
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.snapshot_schemas import (
    ID_EXAMPLES,
    AssignmentRow,
    AvailabilityRow,
    BaselineSnapshotData,
    IsoDate,
    ProjectId,
    ProjectRow,
    ResourceId,
    ResourceRow,
    RoleId,
    RoleRow,
    TaskId,
    TaskRow,
)

# Batas jumlah hari untuk durasi dan estimasi: satu tahun kalender sudah jauh di atas task
# terbesar di baseline, dan mencegah salah ketik (misalnya 3000) masuk ke simulasi.
MAX_DAYS = 365
# Periode terpanjang (ketidakhadiran, periode terdampak CR), termasuk cuti melahirkan.
MAX_PERIOD_DAYS = 366
# Tanggal skenario boleh melewati tanggal akhir baseline sejauh ini (target baru, misalnya).
HORIZON_SLACK_DAYS = 365
MAX_ITEMS = 200
# Waktu mentor untuk anggota baru (SCRUM-24): bagian kapasitas hariannya selama onboarding.
DEFAULT_MENTOR_SHARE = 0.2
MAX_MENTOR_SHARE = 0.5

# Task yang sudah selesai atau dibatalkan tidak dapat lagi dikurangi, terlambat, atau terblokir.
CLOSED_TASK_STATUSES = frozenset({TaskStatus.DONE, TaskStatus.DROPPED})
# Jenis ketidakhadiran (D-09) untuk situasi Member Absence. Rotasi punya situasinya sendiri.
LeaveType = Literal["planned_leave", "sudden_leave", "maternity_leave"]
LEAVE_TYPES: tuple[str, ...] = get_args(LeaveType)

Loc = tuple[str | int, ...]


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


Days = Annotated[float, Field(gt=0, le=MAX_DAYS)]
AssignmentId = Annotated[str, StringConstraints(pattern=ASSIGNMENT_ID_PATTERN, max_length=20)]
TaskName = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=300)]


def _ids(description: str) -> Any:
    """Daftar ID opsional (bawaan kosong)."""
    return Field(default_factory=list, max_length=MAX_ITEMS, description=description)


class DateRange(Params):
    start: IsoDate = Field(description="Tanggal mulai (YYYY-MM-DD)")
    end: IsoDate = Field(description="Tanggal selesai (YYYY-MM-DD), tidak sebelum mulai")


class NewTask(Params):
    task_name: TaskName
    required_role_id: RoleId = Field(description="Peran yang mengerjakan, dari baseline")
    estimated_days: Days = Field(description="Estimasi hari kerja")


class ChangeRequestParams(Params):
    """Mid-sprint Change Request"""

    project_id: ProjectId = Field(description="Proyek yang menerima permintaan perubahan")
    added_tasks: list[NewTask] = Field(
        default_factory=list, max_length=MAX_ITEMS, description="Task baru beserta estimasinya"
    )
    removed_task_ids: list[TaskId] = _ids("Task proyek ini yang dikeluarkan dari scope")
    affected_period: DateRange = Field(description="Periode sprint yang terdampak")
    target_date: IsoDate = Field(description="Target selesai permintaan perubahan")


class MemberAbsenceParams(Params):
    """Member Absence"""

    resource_id: ResourceId = Field(description="Anggota yang tidak hadir")
    absence_type: LeaveType = Field(description="Jenis ketidakhadiran")
    absence_period: DateRange = Field(description="Periode ketidakhadiran")
    affected_task_ids: list[TaskId] = _ids(
        "Task anggota ini yang terdampak; kosong berarti semua task aktifnya"
    )


class NewMemberParams(Params):
    """New Member"""

    role_id: RoleId = Field(description="Peran anggota baru")
    seniority: Seniority
    join_date: IsoDate = Field(description="Tanggal bergabung")
    onboarding_weeks: float = Field(ge=1, le=2, description="Lama onboarding, 1 sampai 2 minggu")
    initial_capacity: float = Field(
        gt=0, le=1, description="Kapasitas selama onboarding, pecahan kapasitas penuh (0.5 = 50%)"
    )
    mentor_resource_id: ResourceId | None = Field(
        default=None, description="Anggota yang membimbing selama onboarding; kosong berarti tanpa"
    )
    mentor_share: float | None = Field(
        default=None,
        gt=0,
        le=MAX_MENTOR_SHARE,
        description="Bagian kapasitas harian mentor untuk membimbing (0.2 = 20%); kosong berarti "
        f"{DEFAULT_MENTOR_SHARE:g}",
    )


class MemberRotationParams(Params):
    """Member Rotation or Withdrawal"""

    resource_id: ResourceId = Field(description="Anggota yang dipindahkan atau keluar")
    origin_project_id: ProjectId = Field(description="Proyek asal")
    destination_project_id: ProjectId | None = Field(
        default=None, description="Proyek tujuan; kosong berarti anggota keluar dari tim"
    )
    transfer_date: IsoDate = Field(description="Tanggal pindah atau keluar")
    existing_assignment_ids: list[AssignmentId] = _ids(
        "Penugasan di proyek asal yang dilepas; kosong berarti semua penugasan aktifnya"
    )


class TaskOverrunParams(Params):
    """Task Overrun"""

    task_id: TaskId = Field(description="Task yang melebihi estimasi")
    original_days: Days = Field(description="Estimasi awal, sama dengan estimasi di baseline")
    revised_days: Days = Field(description="Estimasi baru, lebih dari estimasi awal")
    remaining_days: Days = Field(description="Sisa pekerjaan dalam hari kerja")


class TaskBlockerParams(Params):
    """Task Blocker"""

    task_id: TaskId = Field(description="Task yang terblokir")
    blocking_task_id: TaskId | None = Field(
        default=None,
        description="Dependency penghambat (task yang ditunggu); kosong bila penghambatnya "
        "di luar baseline",
    )
    blocking_days: Days = Field(description="Lama blokir dalam hari kerja")
    independent_task_ids: list[TaskId] = _ids("Task yang tetap bisa dikerjakan selama blokir")


class PriorityChangeParams(Params):
    """Priority Change"""

    project_id: ProjectId = Field(description="Proyek yang prioritasnya diubah")
    new_priority_rank: int = Field(ge=1, description="Prioritas baru, 1 = tertinggi")
    affected_task_ids: list[TaskId] = _ids(
        "Task proyek ini yang terdampak; kosong berarti semua task aktifnya"
    )
    estimated_days: Days | None = Field(
        default=None,
        description="Estimasi hari kerja task terdampak; kosong berarti estimasi baseline",
    )
    target_date: IsoDate = Field(description="Target baru proyek")


class CriticalBugParams(Params):
    """Critical Bug"""

    severity: Severity
    fixing_days: Days = Field(description="Lama perbaikan dalam hari kerja")
    resource_id: ResourceId = Field(description="Anggota yang ditugasi memperbaiki")
    affected_task_ids: list[TaskId] = _ids("Task yang terdampak bug")


# --- indeks baseline ---------------------------------------------------------------------------


@dataclass
class BaselineIndex:
    """Isi baseline snapshot yang sudah diindeks untuk pemeriksaan parameter."""

    projects: dict[str, ProjectRow]
    resources: dict[str, ResourceRow]
    roles: dict[str, RoleRow]
    tasks: dict[str, TaskRow]
    assignments: dict[str, AssignmentRow]
    assignments_by_resource: dict[str, list[AssignmentRow]]
    availability_by_resource: dict[str, list[AvailabilityRow]]
    # task -> task yang ditunggunya, dan task -> task yang menunggunya.
    depends_on: dict[str, set[str]]
    dependents: dict[str, set[str]]
    # Rentang tanggal yang masuk akal untuk skenario, None bila baseline tanpa proyek.
    horizon: tuple[date, date] | None

    @classmethod
    def build(cls, baseline: BaselineSnapshotData) -> "BaselineIndex":
        assignments_by_resource: dict[str, list[AssignmentRow]] = defaultdict(list)
        for assignment in baseline.assignments:
            assignments_by_resource[assignment.resource_id].append(assignment)
        availability_by_resource: dict[str, list[AvailabilityRow]] = defaultdict(list)
        for row in baseline.availability:
            availability_by_resource[row.resource_id].append(row)
        depends_on: dict[str, set[str]] = defaultdict(set)
        dependents: dict[str, set[str]] = defaultdict(set)
        for dependency in baseline.task_dependencies:
            depends_on[dependency.task_id].add(dependency.depends_on_task_id)
            dependents[dependency.depends_on_task_id].add(dependency.task_id)

        horizon = None
        if baseline.projects:
            ends = [p.target_launch_date for p in baseline.projects]
            ends += [p.actual_launch_date for p in baseline.projects if p.actual_launch_date]
            ends += [t.planned_finish for t in baseline.tasks]
            horizon = (
                min(p.start_date for p in baseline.projects),
                max(ends) + timedelta(days=HORIZON_SLACK_DAYS),
            )
        return cls(
            projects={p.project_id: p for p in baseline.projects},
            resources={r.resource_id: r for r in baseline.resources},
            roles={r.role_id: r for r in baseline.roles},
            tasks={t.task_id: t for t in baseline.tasks},
            assignments={a.assignment_id: a for a in baseline.assignments},
            assignments_by_resource=dict(assignments_by_resource),
            availability_by_resource=dict(availability_by_resource),
            depends_on=dict(depends_on),
            dependents=dict(dependents),
            horizon=horizon,
        )

    def downstream(self, task_id: str) -> set[str]:
        """Semua task yang bergantung, langsung atau tidak langsung, pada `task_id`."""
        found: set[str] = set()
        queue = deque([task_id])
        while queue:
            for dependent in self.dependents.get(queue.popleft(), ()):
                if dependent not in found:
                    found.add(dependent)
                    queue.append(dependent)
        found.discard(task_id)
        return found


# --- kesalahan parameter -----------------------------------------------------------------------


@dataclass(frozen=True)
class ParameterError:
    """Satu kesalahan; `loc` relatif terhadap objek `parameters`."""

    loc: Loc
    message: str
    type: str

    def as_detail(self) -> dict[str, Any]:
        # Bentuknya sama dengan error validasi FastAPI (dan `FieldError` modul Data), sehingga
        # formulir cukup punya satu cara menandai field yang salah.
        return {"loc": ["body", "parameters", *self.loc], "msg": self.message, "type": self.type}


@dataclass
class _Checker:
    index: BaselineIndex
    errors: list[ParameterError] = field(default_factory=list)

    def add(self, loc: Loc, message: str, kind: str) -> None:
        self.errors.append(ParameterError(loc, message, kind))

    def project(self, loc: Loc, project_id: str) -> ProjectRow | None:
        project = self.index.projects.get(project_id)
        if project is None:
            self.add(loc, f"Proyek {project_id} tidak ada di baseline", "project_not_found")
        return project

    def resource(self, loc: Loc, resource_id: str) -> ResourceRow | None:
        resource = self.index.resources.get(resource_id)
        if resource is None:
            self.add(loc, f"Anggota {resource_id} tidak ada di baseline", "resource_not_found")
        return resource

    def role(self, loc: Loc, role_id: str) -> RoleRow | None:
        role = self.index.roles.get(role_id)
        if role is None:
            self.add(loc, f"Peran {role_id} tidak ada di baseline", "role_not_found")
        return role

    def task(self, loc: Loc, task_id: str, *, open_only: bool) -> TaskRow | None:
        task = self.index.tasks.get(task_id)
        if task is None:
            self.add(loc, f"Task {task_id} tidak ada di baseline", "task_not_found")
            return None
        if open_only and task.status in CLOSED_TASK_STATUSES:
            self.add(loc, f"Task {task_id} sudah berstatus {task.status.value}", "task_closed")
            return None
        return task

    def tasks(
        self, name: str, task_ids: list[str], *, open_only: bool
    ) -> list[tuple[int, TaskRow]]:
        """Periksa daftar task; kembalikan yang lolos beserta posisinya di daftar."""
        found: list[tuple[int, TaskRow]] = []
        seen: set[str] = set()
        for position, task_id in enumerate(task_ids):
            loc = (name, position)
            if task_id in seen:
                self.add(loc, f"Task {task_id} disebut lebih dari sekali", "duplicate")
                continue
            seen.add(task_id)
            task = self.task(loc, task_id, open_only=open_only)
            if task is not None:
                found.append((position, task))
        return found

    def same_project(
        self, name: str, tasks: list[tuple[int, TaskRow]], project: ProjectRow | None
    ) -> None:
        if project is None:
            return
        for position, task in tasks:
            if task.project_id != project.project_id:
                self.add(
                    (name, position),
                    f"Task {task.task_id} milik proyek {task.project_id}, "
                    f"bukan {project.project_id}",
                    "task_other_project",
                )

    def period(self, name: str, period: DateRange) -> bool:
        """Periode berurutan dan tidak terlalu panjang; True bila keduanya terpenuhi."""
        if period.end < period.start:
            self.add(
                (name, "end"),
                f"Tidak boleh sebelum tanggal mulai ({period.start.isoformat()})",
                "end_before_start",
            )
            return False
        if (period.end - period.start).days + 1 > MAX_PERIOD_DAYS:
            self.add(
                (name, "end"), f"Periode paling panjang {MAX_PERIOD_DAYS} hari", "period_too_long"
            )
            return False
        return True

    def not_before_project(self, loc: Loc, value: date, project: ProjectRow | None) -> None:
        if project is not None and value < project.start_date:
            self.add(
                loc,
                f"Tidak boleh sebelum proyek {project.project_id} dimulai "
                f"({project.start_date.isoformat()})",
                "before_project_start",
            )


# --- aturan per situasi ------------------------------------------------------------------------


def check_change_request(p: ChangeRequestParams, c: _Checker) -> None:
    project = c.project(("project_id",), p.project_id)
    if not p.added_tasks and not p.removed_task_ids:
        c.add(
            ("added_tasks",),
            "Isi minimal satu task yang ditambah atau dikurangi",
            "no_task_change",
        )
    for position, task in enumerate(p.added_tasks):
        c.role(("added_tasks", position, "required_role_id"), task.required_role_id)
    removed = c.tasks("removed_task_ids", p.removed_task_ids, open_only=True)
    c.same_project("removed_task_ids", removed, project)
    if c.period("affected_period", p.affected_period):
        c.not_before_project(("affected_period", "start"), p.affected_period.start, project)
        if p.target_date < p.affected_period.start:
            c.add(
                ("target_date",),
                "Tidak boleh sebelum awal periode terdampak "
                f"({p.affected_period.start.isoformat()})",
                "target_before_period",
            )


def check_member_absence(p: MemberAbsenceParams, c: _Checker) -> None:
    resource = c.resource(("resource_id",), p.resource_id)
    period_ok = c.period("absence_period", p.absence_period)
    if resource is not None and period_ok:
        # Sama dengan ex_availability_no_overlap J&C: ketidakhadiran satu anggota tidak boleh
        # bertumpuk, kecuali rotation_in (kedatangan, bukan ketidakhadiran).
        for row in c.index.availability_by_resource.get(p.resource_id, []):
            if row.absence_type == AbsenceType.ROTATION_IN:
                continue
            if row.date_start <= p.absence_period.end and p.absence_period.start <= row.date_end:
                c.add(
                    ("absence_period",),
                    f"Bertumpuk dengan {row.absence_type.value} {row.availability_id} "
                    f"({row.date_start.isoformat()} s.d. {row.date_end.isoformat()}) di baseline",
                    "absence_overlap",
                )
                break
    tasks = c.tasks("affected_task_ids", p.affected_task_ids, open_only=True)
    if resource is not None:
        assigned = {a.task_id for a in c.index.assignments_by_resource.get(p.resource_id, [])}
        for position, task in tasks:
            if task.task_id not in assigned:
                c.add(
                    ("affected_task_ids", position),
                    f"Task {task.task_id} tidak ditugaskan ke {p.resource_id} di baseline",
                    "task_not_assigned",
                )


def check_new_member(p: NewMemberParams, c: _Checker) -> None:
    c.role(("role_id",), p.role_id)
    if p.mentor_resource_id is not None:
        mentor = c.resource(("mentor_resource_id",), p.mentor_resource_id)
        if mentor is not None and mentor.join_date > p.join_date:
            c.add(
                ("mentor_resource_id",),
                f"{mentor.resource_id} baru bergabung {mentor.join_date.isoformat()}, "
                "setelah anggota baru",
                "mentor_not_joined",
            )
    elif p.mentor_share is not None:
        c.add(("mentor_resource_id",), "Pilih mentor bila bagian waktu mentor diisi", "missing")


def check_member_rotation(p: MemberRotationParams, c: _Checker) -> None:
    resource = c.resource(("resource_id",), p.resource_id)
    origin = c.project(("origin_project_id",), p.origin_project_id)
    if p.destination_project_id is not None:
        if p.destination_project_id == p.origin_project_id:
            c.add(
                ("destination_project_id",),
                "Harus berbeda dari proyek asal",
                "same_project",
            )
        else:
            destination = c.project(("destination_project_id",), p.destination_project_id)
            if destination is not None and destination.status == ProjectStatus.RELEASED:
                c.add(
                    ("destination_project_id",),
                    f"Proyek {destination.project_id} sudah rilis",
                    "project_released",
                )
    c.not_before_project(("transfer_date",), p.transfer_date, origin)

    if resource is not None and origin is not None:
        in_origin = [
            a
            for a in c.index.assignments_by_resource.get(p.resource_id, [])
            if _task_project(c.index, a.task_id) == origin.project_id
        ]
        if not in_origin:
            c.add(
                ("origin_project_id",),
                f"{p.resource_id} tidak punya penugasan di proyek {origin.project_id}",
                "no_assignment_in_origin",
            )

    seen: set[str] = set()
    for position, assignment_id in enumerate(p.existing_assignment_ids):
        loc = ("existing_assignment_ids", position)
        if assignment_id in seen:
            c.add(loc, f"Penugasan {assignment_id} disebut lebih dari sekali", "duplicate")
            continue
        seen.add(assignment_id)
        assignment = c.index.assignments.get(assignment_id)
        if assignment is None:
            c.add(loc, f"Penugasan {assignment_id} tidak ada di baseline", "assignment_not_found")
        elif assignment.resource_id != p.resource_id:
            c.add(
                loc,
                f"Penugasan {assignment_id} milik {assignment.resource_id}, bukan {p.resource_id}",
                "assignment_other_member",
            )
        elif origin is not None and _task_project(c.index, assignment.task_id) != origin.project_id:
            c.add(
                loc,
                f"Penugasan {assignment_id} bukan di proyek asal {origin.project_id}",
                "assignment_other_project",
            )
        elif assignment.released_date is not None and assignment.released_date < p.transfer_date:
            c.add(
                loc,
                f"Penugasan {assignment_id} sudah berakhir pada "
                f"{assignment.released_date.isoformat()}, sebelum tanggal pindah",
                "assignment_released",
            )


def check_task_overrun(p: TaskOverrunParams, c: _Checker) -> None:
    task = c.task(("task_id",), p.task_id, open_only=True)
    if task is not None and not math.isclose(p.original_days, task.estimated_days):
        c.add(
            ("original_days",),
            f"Harus sama dengan estimasi baseline task {task.task_id} "
            f"({task.estimated_days:g} hari)",
            "original_mismatch",
        )
    if p.revised_days <= p.original_days:
        c.add(
            ("revised_days",),
            f"Harus lebih dari estimasi awal ({p.original_days:g} hari)",
            "not_an_overrun",
        )
    if p.remaining_days > p.revised_days:
        c.add(
            ("remaining_days",),
            f"Tidak boleh lebih dari estimasi baru ({p.revised_days:g} hari)",
            "remaining_exceeds_revised",
        )


def check_task_blocker(p: TaskBlockerParams, c: _Checker) -> None:
    task = c.task(("task_id",), p.task_id, open_only=True)
    blocker = p.blocking_task_id
    if blocker is not None:
        if blocker == p.task_id:
            c.add(("blocking_task_id",), "Task tidak dapat memblokir dirinya sendiri", "self_block")
        else:
            found = c.task(("blocking_task_id",), blocker, open_only=False)
            if found and task and blocker not in c.index.depends_on.get(p.task_id, set()):
                c.add(
                    ("blocking_task_id",),
                    f"Task {blocker} bukan dependency task {p.task_id} di baseline",
                    "not_a_dependency",
                )

    downstream = c.index.downstream(p.task_id) if task is not None else set()
    independent = c.tasks("independent_task_ids", p.independent_task_ids, open_only=True)
    for position, other in independent:
        if other.task_id == p.task_id:
            c.add(
                ("independent_task_ids", position),
                "Task yang terblokir tidak dapat sekaligus menjadi task independen",
                "blocked_task_listed",
            )
        elif other.task_id in downstream:
            c.add(
                ("independent_task_ids", position),
                f"Task {other.task_id} bergantung pada {p.task_id}, jadi ikut terblokir",
                "depends_on_blocked",
            )


def check_priority_change(p: PriorityChangeParams, c: _Checker) -> None:
    project = c.project(("project_id",), p.project_id)
    if project is not None:
        if project.status == ProjectStatus.RELEASED:
            c.add(("project_id",), f"Proyek {project.project_id} sudah rilis", "project_released")
        else:
            is_active = project.status in ACTIVE_PROJECT_STATUSES
            limit = max_priority_rank(project, c.index)
            if p.new_priority_rank > limit:
                c.add(
                    ("new_priority_rank",),
                    f"Paling besar {limit} (jumlah proyek aktif)",
                    "rank_out_of_range",
                )
            elif is_active and p.new_priority_rank == project.priority_rank:
                c.add(
                    ("new_priority_rank",),
                    f"Sama dengan prioritas sekarang ({project.priority_rank})",
                    "rank_unchanged",
                )
        c.not_before_project(("target_date",), p.target_date, project)
    tasks = c.tasks("affected_task_ids", p.affected_task_ids, open_only=True)
    c.same_project("affected_task_ids", tasks, project)


def max_priority_rank(project: ProjectRow, index: BaselineIndex) -> int:
    """Peringkat prioritas terbesar yang dapat diberikan ke `project`: jumlah proyek aktif.
    Proyek on_hold yang diberi prioritas ikut menjadi aktif, jadi batasnya bertambah satu."""
    active = sum(1 for x in index.projects.values() if x.status in ACTIVE_PROJECT_STATUSES)
    return active if project.status in ACTIVE_PROJECT_STATUSES else active + 1


def check_critical_bug(p: CriticalBugParams, c: _Checker) -> None:
    c.resource(("resource_id",), p.resource_id)
    # Bug boleh ditemukan pada task yang sudah selesai, jadi status task tidak dibatasi.
    c.tasks("affected_task_ids", p.affected_task_ids, open_only=False)


def _task_project(index: BaselineIndex, task_id: str) -> str | None:
    task = index.tasks.get(task_id)
    return task.project_id if task else None


# --- registri situasi --------------------------------------------------------------------------


@dataclass(frozen=True)
class SituationSpec:
    situation: Situation
    label: str
    description: str
    model: type[Params]
    check: Callable[[Any, _Checker], None]


SITUATIONS: dict[Situation, SituationSpec] = {
    spec.situation: spec
    for spec in (
        SituationSpec(
            Situation.CHANGE_REQUEST,
            "Mid-sprint Change Request",
            "Permintaan perubahan di tengah sprint: task ditambah atau dikeluarkan dari scope.",
            ChangeRequestParams,
            check_change_request,
        ),
        SituationSpec(
            Situation.MEMBER_ABSENCE,
            "Member Absence",
            "Anggota tidak hadir (cuti terencana, mendadak, atau melahirkan) selama suatu periode.",
            MemberAbsenceParams,
            check_member_absence,
        ),
        SituationSpec(
            Situation.NEW_MEMBER,
            "New Member",
            "Anggota baru bergabung dengan masa onboarding dan kapasitas awal yang lebih rendah.",
            NewMemberParams,
            check_new_member,
        ),
        SituationSpec(
            Situation.MEMBER_ROTATION,
            "Member Rotation or Withdrawal",
            "Anggota dipindahkan ke proyek lain atau keluar dari tim.",
            MemberRotationParams,
            check_member_rotation,
        ),
        SituationSpec(
            Situation.TASK_OVERRUN,
            "Task Overrun",
            "Task membutuhkan waktu lebih lama dari estimasinya.",
            TaskOverrunParams,
            check_task_overrun,
        ),
        SituationSpec(
            Situation.TASK_BLOCKER,
            "Task Blocker",
            "Task terhambat dependency selama beberapa hari.",
            TaskBlockerParams,
            check_task_blocker,
        ),
        SituationSpec(
            Situation.PRIORITY_CHANGE,
            "Priority Change",
            "Prioritas proyek diubah sehingga urutan pengerjaan task bergeser.",
            PriorityChangeParams,
            check_priority_change,
        ),
        SituationSpec(
            Situation.CRITICAL_BUG,
            "Critical Bug",
            "Bug harus segera diperbaiki oleh anggota tertentu.",
            CriticalBugParams,
            check_critical_bug,
        ),
    )
}


# --- validasi ----------------------------------------------------------------------------------


@dataclass(frozen=True)
class ValidationOutcome:
    params: Params | None
    errors: list[ParameterError]

    @property
    def ok(self) -> bool:
        return not self.errors


def validate_parameters(
    situation: Situation | str, raw: Mapping[str, Any], index: BaselineIndex
) -> ValidationOutcome:
    """Validasi penuh parameter satu situasi terhadap baseline snapshot.

    Kesalahan bentuk dilaporkan lebih dulu; aturan isi baru diperiksa bila bentuknya benar,
    karena aturan itu butuh nilai yang sudah bertipe.
    """
    spec = SITUATIONS[Situation(situation)]
    try:
        params = spec.model.model_validate(raw)
    except ValidationError as exc:
        return ValidationOutcome(None, [_from_pydantic(error) for error in exc.errors()])

    checker = _Checker(index)
    spec.check(params, checker)
    _check_horizon(params, checker)
    return ValidationOutcome(params, checker.errors)


def _check_horizon(params: Params, c: _Checker) -> None:
    """Setiap tanggal di dalam rentang baseline; field yang sudah salah tidak dilaporkan lagi."""
    if c.index.horizon is None:
        return
    first, last = c.index.horizon
    reported = {error.loc for error in c.errors}
    for loc, value in _dates(params, ()):
        if not first <= value <= last and loc not in reported:
            c.add(
                loc,
                f"Di luar rentang baseline ({first.isoformat()} s.d. {last.isoformat()})",
                "date_out_of_range",
            )


def _dates(value: Any, loc: Loc) -> Iterator[tuple[Loc, date]]:
    if isinstance(value, date):
        yield loc, value
    elif isinstance(value, BaseModel):
        for name in type(value).model_fields:
            yield from _dates(getattr(value, name), (*loc, name))
    elif isinstance(value, list):
        for position, item in enumerate(value):
            yield from _dates(item, (*loc, position))


def _from_pydantic(error: Mapping[str, Any]) -> ParameterError:
    return ParameterError(tuple(error["loc"]), _message(error), error["type"])


def _message(error: Mapping[str, Any]) -> str:
    """Pesan error Pydantic dalam bahasa pengguna aplikasi."""
    kind = error.get("type", "")
    ctx = error.get("ctx") or {}
    if kind == "missing" or (error.get("input") is None and kind != "extra_forbidden"):
        return "Wajib diisi"
    if kind == "value_error":
        return str(ctx.get("error", error.get("msg", "Nilai tidak sah")))
    if kind == "extra_forbidden":
        return "Field ini tidak dikenal untuk situasi ini"
    if kind in ("enum", "literal_error"):
        return f"Nilai tidak sah, gunakan salah satu dari {ctx.get('expected')}"
    if kind == "string_pattern_mismatch":
        example = ID_EXAMPLES.get(str(ctx.get("pattern")))
        return f"Format tidak sah, contoh {example}" if example else "Format tidak sah"
    if kind == "string_too_short":
        return "Wajib diisi"
    if kind == "string_too_long":
        return f"Paling banyak {ctx.get('max_length')} karakter"
    if kind == "too_long":
        return f"Paling banyak {ctx.get('max_length')} item"
    if kind.startswith("date"):
        return "Tanggal harus berformat YYYY-MM-DD"
    if kind == "list_type":
        return "Harus berupa daftar"
    if kind in ("model_type", "dict_type", "model_attributes_type"):
        return "Harus berupa objek"
    if kind.startswith("string"):
        return "Harus berupa teks"
    if kind.startswith("int"):
        return "Harus bilangan bulat"
    if kind.startswith("float") or kind == "finite_number":
        return "Harus angka, misalnya 1.5"
    if kind == "greater_than":
        return f"Harus lebih dari {_number(ctx.get('gt'))}"
    if kind == "greater_than_equal":
        return f"Paling kecil {_number(ctx.get('ge'))}"
    if kind == "less_than_equal":
        return f"Paling besar {_number(ctx.get('le'))}"
    return str(error.get("msg", "Nilai tidak sah"))


def _number(value: Any) -> str:
    return f"{value:g}" if isinstance(value, float) else str(value)
