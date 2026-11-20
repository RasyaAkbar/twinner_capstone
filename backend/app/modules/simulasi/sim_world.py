"""Dunia simulasi what-if: langkah 1 §IV.2.3 (SCRUM-24 s.d. SCRUM-27).

`build_world` menyalin baseline snapshot ke struktur yang dapat diubah aturan skenario
(`sim_rules`, SCRUM-24), lalu dibaca kalender kapasitas (`sim_calendar`, SCRUM-25) dan penjadwal
(`sim_scheduler`, SCRUM-26). Struktur ini selalu dibuat baru dari `BaselineSnapshotData`, dan
`sim_rules.apply_scenario` bekerja pada salinan dalam (deep copy), jadi baseline tidak pernah
berubah.

Asumsi yang berlaku untuk baseline maupun skenario (lihat docs/SIMULATION_ENGINE.md):

- Simulasi mulai pada `start_date`: bawaannya hari kerja pertama setelah tanggal realisasi
  terakhir di snapshot (actual_start/actual_finish, D-08, D-10).
- Pemilik task adalah penugasan aktif terakhir (D-07). Task tanpa penugasan aktif masuk "pool"
  dan diambil anggota mana pun yang memegang perannya.
- Task belum mulai tidak dikerjakan sebelum `planned_start`-nya (rencana sprint dihormati).
- Task `in_progress` melanjutkan sisa pekerjaan: estimasi dikurangi hari kerja sejak
  `actual_start`, paling sedikit `MIN_REMAINING_DAYS`. Task `in_qa` tinggal `MIN_REMAINING_DAYS`.
- Task `blocked` di baseline dianggap terbuka lagi mulai `start_date`.
- Proyek `on_hold` dan task `on_hold` tidak dijadwalkan sampai diaktifkan Priority Change.
- Anggota yang dirotasi keluar (D-09 rotation_out) melepas task terbukanya ke pool selama
  rotasi; ketidakhadiran lain (cuti) membuat task-nya menunggu.
- Item D-08 memakai sebagian kapasitas harian anggota menurut severity (`ITEM_SHARE`); hotfix
  mengambil seluruh hari.
"""

import math
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.modules.data.enums import ACTIVE_PROJECT_STATUSES, AbsenceType, TaskStatus
from app.modules.data.workdays import count_working_days, is_working_day, next_working_day
from app.modules.simulasi.snapshot_schemas import BaselineSnapshotData

ONE_DAY = timedelta(days=1)
# Nilai bawaan Data Dictionary bila peran atau level kompleksitas tidak ada di snapshot.
DEFAULT_CAPACITY_REFERENCE = 3.0
DEFAULT_LOAD_WEIGHT = 1.0
# Sisa pekerjaan paling kecil untuk task yang sudah berjalan (dan task in_qa).
MIN_REMAINING_DAYS = 0.5

# Bagian kapasitas harian anggota yang dipakai satu item beban non-proyek (D-08) atau perbaikan
# bug (Critical Bug), menurut severity. Hotfix production selalu mengambil seluruh hari
# (preemption, D-08 is_hotfix). Angka ini asumsi tim untuk ditinjau bersama PuTI (SCRUM-31).
ITEM_SHARE = {"critical": 1.0, "high": 0.5, "medium": 0.25, "low": 0.25}
DEFAULT_ITEM_SHARE = 0.25
HOTFIX_SHARE = 1.0
# Item D-08 yang belum selesai pada start_date dianggap selesai dalam sekian hari kerja.
OPEN_ITEM_DAYS = 2

CLOSED_STATUSES = frozenset({TaskStatus.DONE.value, TaskStatus.DROPPED.value})
ACTIVE_STATUSES = frozenset(status.value for status in ACTIVE_PROJECT_STATUSES)


def item_share(severity: str | None, is_hotfix: bool = False) -> float:
    if is_hotfix:
        return HOTFIX_SHARE
    return ITEM_SHARE.get(severity or "", DEFAULT_ITEM_SHARE)


@dataclass
class SimRole:
    role_id: str
    name: str
    capacity_reference: float


@dataclass
class Complexity:
    complexity_id: str
    min_days: float
    max_days: float
    load_weight: float


@dataclass
class SimProject:
    project_id: str
    name: str
    status: str
    priority_rank: int
    start_date: date
    target_launch_date: date

    @property
    def active(self) -> bool:
        return self.status in ACTIVE_STATUSES


@dataclass(frozen=True)
class Release:
    """Mulai `on`, task dilepas dari `resource_id` (bila masih miliknya) dan masuk pool."""

    on: date
    resource_id: str


@dataclass
class SimTask:
    task_id: str
    project_id: str
    name: str
    required_role_id: str
    complexity_id: str | None
    priority_rank: int
    # Effort penuh dalam hari kerja satu anggota (D-05 estimated_days).
    estimated_days: float
    status: str
    planned_start: date
    planned_finish: date
    actual_start: date | None = None
    actual_finish: date | None = None
    owner: str | None = None
    # Paling awal boleh dimulai; None berarti max(planned_start, mulai proyek).
    earliest_start: date | None = None
    # Sisa pekerjaan yang ditetapkan skenario (Task Overrun); None berarti dihitung penjadwal.
    remaining_days: float | None = None
    # Estimasi dari parameter skenario: tidak diundi ulang oleh replikasi Monte Carlo.
    effort_fixed: bool = False
    # Hari kerja task tertahan setelah siap dikerjakan (Task Blocker).
    block_days: int = 0
    releases: list[Release] = field(default_factory=list)
    # Ditambahkan atau dikeluarkan dari scope oleh Change Request.
    added: bool = False
    removed: bool = False

    @property
    def closed(self) -> bool:
        return self.status in CLOSED_STATUSES

    @property
    def started(self) -> bool:
        return self.actual_start is not None


@dataclass(frozen=True)
class SimDependency:
    task_id: str
    depends_on_task_id: str
    dependency_type: str
    propagation_factor: float


@dataclass(frozen=True)
class Ramp:
    """Onboarding: kapasitas naik linier dari `initial` ke 1 selama `working_days` hari kerja."""

    start: date
    working_days: int
    initial: float

    def factor(self, day_number: int) -> float:
        """Kapasitas pada hari kerja ke-`day_number` sejak `start` (mulai dari 0)."""
        if day_number >= self.working_days:
            return 1.0
        return self.initial + (1.0 - self.initial) * day_number / self.working_days


@dataclass(frozen=True)
class Help:
    """Anggota boleh mengambil task belum mulai milik anggota lain yang perannya sama.

    Dipakai anggota baru (New Member) dan anggota yang dirotasi ke proyek tujuan.
    `project_id` None berarti task proyek mana pun.
    """

    start: date
    project_id: str | None


@dataclass
class SimResource:
    resource_id: str
    name: str
    primary_role_id: str
    secondary_role_ids: tuple[str, ...]
    capacity_reference: float
    join_date: date
    # Tidak lagi di tim mulai tanggal ini (Member Withdrawal).
    leave_date: date | None = None
    ramp: Ramp | None = None
    helps: Help | None = None
    # Proyek yang tidak lagi dikerjakan anggota ini, mulai tanggal tertentu (rotasi).
    excluded_projects: dict[str, date] = field(default_factory=dict)
    added: bool = False

    def can_do(self, role_id: str) -> bool:
        return role_id == self.primary_role_id or role_id in self.secondary_role_ids

    def works_on(self, project_id: str, day: date) -> bool:
        excluded_from = self.excluded_projects.get(project_id)
        return excluded_from is None or day < excluded_from


@dataclass(frozen=True)
class Absence:
    resource_id: str
    start: date
    end: date
    absence_type: str
    # D-09 is_known_at_planning: cuti terencana atau mendadak.
    planned: bool


@dataclass(frozen=True)
class NonProjectItem:
    """Beban non-proyek: item D-08 (rentang tanggal) atau perbaikan bug skenario (hari kerja).

    Item dengan `end` memakai bagian `share` pada setiap hari kerja `start..end`. Item dengan
    `working_days` memakai `share` pada hari kerja anggota (yang hadir) mulai `start` sampai
    `working_days` habis; hari terakhir yang pecahan hanya memakai sebagian.
    """

    resource_id: str
    start: date
    share: float
    label: str
    end: date | None = None
    working_days: float | None = None
    source: str = "baseline"


@dataclass(frozen=True)
class Mentoring:
    """Selama onboarding `mentee_id`, mentor memakai `share` kapasitas hariannya."""

    mentor_id: str
    mentee_id: str
    share: float


@dataclass
class SimWorld:
    snapshot_id: int
    start_date: date
    holidays: frozenset[date]
    roles: dict[str, SimRole]
    complexity: dict[str, Complexity]
    projects: dict[str, SimProject]
    tasks: dict[str, SimTask]
    dependencies: list[SimDependency]
    resources: dict[str, SimResource]
    absences: list[Absence]
    items: list[NonProjectItem]
    mentoring: list[Mentoring] = field(default_factory=list)
    # assignment_id D-07 -> task_id, untuk parameter rotasi yang menunjuk penugasan.
    assignment_tasks: dict[str, str] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def load_weight(self, task: SimTask) -> float:
        level = self.complexity.get(task.complexity_id or "")
        return level.load_weight if level is not None else DEFAULT_LOAD_WEIGHT

    def capacity_reference(self, role_id: str) -> float:
        role = self.roles.get(role_id)
        return role.capacity_reference if role is not None else DEFAULT_CAPACITY_REFERENCE

    def is_working_day(self, day: date) -> bool:
        return is_working_day(day, self.holidays)

    def schedulable(self, task: SimTask) -> bool:
        """Task terbuka di proyek aktif yang tidak sedang on hold."""
        project = self.projects.get(task.project_id)
        return (
            not task.closed
            and task.status != TaskStatus.ON_HOLD
            and project is not None
            and project.active
        )

    def initial_remaining(self, task: SimTask, effort: float) -> float:
        """Sisa pekerjaan pada `start_date` untuk effort penuh `effort`."""
        if task.remaining_days is not None:
            return task.remaining_days
        if task.status == TaskStatus.IN_QA:
            return min(MIN_REMAINING_DAYS, effort)
        if task.actual_start is not None and task.actual_start < self.start_date:
            worked = count_working_days(
                task.actual_start, self.start_date - ONE_DAY, self.holidays
            )
            return max(effort - worked, MIN_REMAINING_DAYS)
        return effort

    def earliest(self, task: SimTask) -> date:
        """Hari pertama task belum mulai boleh dikerjakan."""
        if task.earliest_start is not None:
            return task.earliest_start
        project = self.projects[task.project_id]
        return max(task.planned_start, project.start_date)


def default_start_date(baseline: BaselineSnapshotData) -> date:
    """Hari kerja pertama setelah tanggal realisasi terakhir yang tercatat di snapshot.

    Tanpa realisasi sama sekali: hari kerja pertama sejak proyek paling awal dimulai.
    """
    holidays = {h.holiday_date for h in baseline.holidays}
    recorded: list[date] = []
    for task in baseline.tasks:
        recorded += [d for d in (task.actual_start, task.actual_finish) if d is not None]
    for item in baseline.workload_items:
        recorded += [
            d for d in (item.received_date, item.started_date, item.resolved_date) if d is not None
        ]
    recorded += [event.event_date for event in baseline.change_events]
    if recorded:
        return next_working_day(max(recorded) + ONE_DAY, holidays)
    if baseline.projects:
        return next_working_day(min(p.start_date for p in baseline.projects), holidays)
    return next_working_day(date.today(), holidays)


def build_world(baseline: BaselineSnapshotData, start_date: date | None = None) -> SimWorld:
    """Langkah 1: baseline snapshot sebagai dunia simulasi yang mulai pada `start_date`."""
    start = start_date or default_start_date(baseline)
    holidays = frozenset(h.holiday_date for h in baseline.holidays)
    roles = {
        r.role_id: SimRole(r.role_id, r.role_name, r.capacity_reference) for r in baseline.roles
    }
    complexity = {
        c.complexity_id: Complexity(
            c.complexity_id,
            min(c.min_days, c.max_days),
            max(c.min_days, c.max_days),
            c.load_weight,
        )
        for c in baseline.complexity_levels
    }
    projects = {
        p.project_id: SimProject(
            p.project_id,
            p.project_name,
            p.status.value,
            p.priority_rank,
            p.start_date,
            p.target_launch_date,
        )
        for p in baseline.projects
    }
    resources = {
        r.resource_id: SimResource(
            resource_id=r.resource_id,
            name=r.resource_name,
            primary_role_id=r.primary_role_id,
            secondary_role_ids=tuple(
                role for role in (r.secondary_role_ids or "").split(";") if role
            ),
            capacity_reference=roles[r.primary_role_id].capacity_reference
            if r.primary_role_id in roles
            else DEFAULT_CAPACITY_REFERENCE,
            join_date=r.join_date,
        )
        for r in baseline.resources
    }
    tasks = {
        t.task_id: SimTask(
            task_id=t.task_id,
            project_id=t.project_id,
            name=t.task_name,
            required_role_id=t.required_role_id,
            complexity_id=t.complexity_id,
            priority_rank=t.priority_rank,
            estimated_days=t.estimated_days,
            status=t.status.value,
            planned_start=t.planned_start,
            planned_finish=t.planned_finish,
            actual_start=t.actual_start,
            actual_finish=t.actual_finish,
        )
        for t in baseline.tasks
    }
    _assign_owners(tasks, resources, baseline, start)

    absences = [
        Absence(
            a.resource_id,
            a.date_start,
            a.date_end,
            a.absence_type.value,
            a.is_known_at_planning,
        )
        for a in baseline.availability
        # rotation_in adalah kedatangan, bukan ketidakhadiran.
        if a.absence_type != AbsenceType.ROTATION_IN and a.date_end >= start
    ]
    items = _workload_items(baseline, start, holidays)

    world = SimWorld(
        snapshot_id=baseline.snapshot_id,
        start_date=start,
        holidays=holidays,
        roles=roles,
        complexity=complexity,
        projects=projects,
        tasks=tasks,
        dependencies=[
            SimDependency(
                d.task_id, d.depends_on_task_id, d.dependency_type.value, d.propagation_factor
            )
            for d in baseline.task_dependencies
        ],
        resources=resources,
        absences=absences,
        items=items,
        assignment_tasks={a.assignment_id: a.task_id for a in baseline.assignments},
    )
    _release_rotated_out(world)
    blocked = sorted(
        t.task_id for t in tasks.values() if t.status == TaskStatus.BLOCKED and world.schedulable(t)
    )
    if blocked:
        world.notes.append(
            f"{len(blocked)} task berstatus blocked di baseline ({', '.join(blocked)}) dianggap "
            f"dapat dilanjutkan mulai {start.isoformat()}."
        )
    return world


def _assign_owners(
    tasks: dict[str, SimTask],
    resources: dict[str, SimResource],
    baseline: BaselineSnapshotData,
    start: date,
) -> None:
    """Pemilik = penugasan aktif terakhir; penugasan yang berakhir nanti menjadi `Release`."""
    latest: dict[str, tuple] = {}
    for a in baseline.assignments:
        if a.released_date is not None and a.released_date < start:
            continue
        if a.resource_id not in resources:
            continue
        key = (a.assigned_date, a.is_takeover, a.assignment_id)
        if a.task_id not in latest or key > latest[a.task_id][0]:
            latest[a.task_id] = (key, a)
    for task_id, (_, assignment) in latest.items():
        task = tasks.get(task_id)
        if task is None:
            continue
        task.owner = assignment.resource_id
        if assignment.released_date is not None:
            released_from = assignment.released_date + ONE_DAY
            task.releases.append(Release(released_from, assignment.resource_id))


def _release_rotated_out(world: SimWorld) -> None:
    """Anggota yang dirotasi keluar (D-09 rotation_out) bekerja di unit lain, bukan cuti.

    Task terbukanya yang jatuh di dalam masa rotasi (sudah berjalan, atau rencana mulainya
    tidak setelah rotasi berakhir) dilepas ke pool sejak rotasi dimulai, sama seperti aturan
    Member Rotation. Ketidakhadiran lain (cuti) tidak melepas task; task menunggu anggotanya.
    """
    released: list[str] = []
    for absence in world.absences:
        if absence.absence_type != AbsenceType.ROTATION_OUT:
            continue
        on = max(absence.start, world.start_date)
        for task in world.tasks.values():
            if task.owner != absence.resource_id or not world.schedulable(task):
                continue
            if not task.started and world.earliest(task) > absence.end:
                continue
            task.releases.append(Release(on, absence.resource_id))
            released.append(task.task_id)
    if released:
        world.notes.append(
            f"{len(released)} task milik anggota yang dirotasi keluar (D-09 rotation_out) "
            f"dilepas ke pool dan dapat diambil anggota lain dengan peran sama: "
            f"{', '.join(sorted(released))}."
        )


def _workload_items(
    baseline: BaselineSnapshotData, start: date, holidays: frozenset[date]
) -> list[NonProjectItem]:
    items: list[NonProjectItem] = []
    for item in baseline.workload_items:
        begin = item.started_date or item.received_date
        end = item.resolved_date
        if end is None:
            # Belum selesai: dianggap selesai OPEN_ITEM_DAYS hari kerja setelah dilanjutkan.
            day = next_working_day(max(begin, start), holidays)
            for _ in range(OPEN_ITEM_DAYS - 1):
                day = next_working_day(day + ONE_DAY, holidays)
            end = day
        if end < start:
            continue
        kind = "Hotfix" if item.is_hotfix else item.item_type.value.capitalize()
        items.append(
            NonProjectItem(
                resource_id=item.resource_id,
                start=begin,
                end=end,
                share=item_share(item.severity.value if item.severity else None, item.is_hotfix),
                label=f"{kind} {item.item_id}",
            )
        )
    return items


def onboarding_working_days(weeks: float) -> int:
    """Lama onboarding dalam hari kerja (5 hari kerja per minggu)."""
    return max(1, math.ceil(weeks * 5 - 1e-9))


def next_id(existing: set[str] | dict[str, object], prefix: str) -> str:
    number = 1
    while f"{prefix}-{number:03d}" in existing:
        number += 1
    return f"{prefix}-{number:03d}"
