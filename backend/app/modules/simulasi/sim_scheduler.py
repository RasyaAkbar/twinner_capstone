"""Penjadwal task hari demi hari: langkah 4-5 §IV.2.3 dan Gambar 4.1 (SCRUM-26).

Setiap hari kerja mulai `start_date`, penjadwal:

1. Melepas task yang dijadwalkan dilepas hari itu (rotasi, keluar tim, penugasan berakhir)
   ke pool. Bila tidak ada lagi anggota yang memegang perannya, task tidak terjadwal. Lalu
   memindahkan task yang dialihkan respons DSS hari itu (`Transfer`) ke pemilik barunya: task
   yang sudah berjalan dilanjutkan pemilik baru dengan sisa effort-nya.
2. Menandai task yang dependency-nya terpenuhi. Task bergantung pada pendahulunya dengan
   `propagation_factor` p (D-06): task boleh mulai setelah pendahulunya menyelesaikan paling
   sedikit p bagian effort-nya. Dependency hard (p = 1) berarti pendahulu sudah selesai pada
   hari kerja sebelumnya. Dengan begitu keterlambatan pendahulu karena effort-nya bertambah
   diteruskan sebesar p, sedangkan pendahulu yang mulai terlambat menunda task sepenuhnya.
3. Menahan task terblokir (Task Blocker) selama sisa hari blokirnya, dihitung sejak task itu
   siap dikerjakan. Pemiliknya beralih ke task lain.
4. Untuk setiap anggota yang bekerja hari itu (urut resource_id): melanjutkan task yang sedang
   dipegangnya, lalu memulai task miliknya yang siap menurut prioritas (prioritas proyek,
   prioritas task, planned_start, task_id) selama bobotnya (`load_weight`) masih muat di
   kapasitas bebas. Task pertama yang tidak muat menghentikan antrean anggota itu.
5. Membagikan task pool yang siap kepada anggota yang memegang perannya dan kapasitas bebasnya
   paling besar (peran utama didahulukan). Task tetap milik anggota itu sampai selesai.
6. Anggota "penolong" (anggota baru, anggota yang dirotasi ke proyek tujuan) mengambil task
   belum mulai milik anggota lain yang tidak sempat memulainya hari itu.
7. Mengurangi sisa effort setiap task yang dikerjakan sebesar
   min(1, kapasitas bebas / total bobot task yang dikerjakan) hari: penuh bila semua task
   muat di kapasitas bebas, sebanding bila tidak (misalnya saat hotfix, tiket, onboarding).
   Lalu dicatat beban harian anggota (Resource Load Index):
   bobot task yang dikerjakan / kapasitas + bagian non-proyek + bagian mentoring.

Task yang tidak dapat dikerjakan siapa pun (tidak ada anggota dengan perannya, menunggu task
on hold atau tidak terjadwal, atau belum selesai sampai batas horizon) ditandai `unscheduled`
beserta peringatannya, dan simulasi berjalan terus (UC-03 alur alternatif).

Hasilnya (`ScheduleResult`) menjadi masukan KPI Calculator (`sim_kpi`).
"""

import bisect
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from app.modules.data.enums import TaskStatus
from app.modules.simulasi.sim_calendar import EPS, CapacityCalendar, DayCapacity
from app.modules.simulasi.sim_world import SimTask, SimWorld


class TaskState(StrEnum):
    DONE = "done"  # sudah selesai sebelum start_date (D-05 status done)
    SCHEDULED = "scheduled"  # selesai di dalam simulasi
    UNSCHEDULED = "unscheduled"  # tidak dapat dijadwalkan; lihat `reason`
    ON_HOLD = "on_hold"  # proyek atau task on hold, tidak dijadwalkan
    DROPPED = "dropped"  # dibatalkan di baseline atau dikeluarkan Change Request


class WarningCode(StrEnum):
    NO_MEMBER_WITH_ROLE = "no_member_with_role"
    WAITS_ON_HOLD = "waits_on_hold_task"
    WAITS_UNSCHEDULED = "waits_unscheduled_task"
    HORIZON = "not_finished_in_horizon"


@dataclass(slots=True)
class TaskOutcome:
    task_id: str
    state: TaskState
    start: date | None = None
    finish: date | None = None
    owner: str | None = None
    effort: float | None = None
    # Hari pertama semua dependency task terpenuhi (task yang belum mulai pada start_date).
    dependency_ready: date | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class MemberDay:
    day: date
    availability: float
    capacity: float
    # Total bobot task proyek yang dikerjakan hari itu (satuan beban D-04).
    project: float
    non_project: float
    mentoring: float
    load: float | None
    absence: str | None
    tasks: tuple[str, ...]

    @property
    def overloaded(self) -> bool:
        return self.load is not None and self.load > 1.0 + EPS


@dataclass(frozen=True, slots=True)
class ScheduleWarning:
    code: WarningCode
    message: str
    task_id: str | None = None
    project_id: str | None = None


@dataclass
class ScheduleResult:
    start_date: date
    # Hari kerja terakhir yang disimulasikan.
    end_date: date
    tasks: dict[str, TaskOutcome]
    members: dict[str, list[MemberDay]]
    warnings: list[ScheduleWarning]


def daily_load(capacity: DayCapacity, project_weight: float) -> float | None:
    """Resource Load Index satu hari; None bila anggota tidak bekerja hari itu."""
    if not capacity.working:
        return None
    return project_weight / capacity.capacity + capacity.non_project + capacity.mentoring


def priority_key(world: SimWorld, task: SimTask) -> tuple:
    return (
        world.projects[task.project_id].priority_rank,
        task.priority_rank,
        task.planned_start,
        task.task_id,
    )


def simulate(
    world: SimWorld,
    calendar: CapacityCalendar | None = None,
    efforts: dict[str, float] | None = None,
    *,
    record_members: bool = True,
) -> ScheduleResult:
    """Jadwalkan dunia `world`. `efforts` mengganti effort task yang tidak `effort_fixed`
    (replikasi Monte Carlo); tanpa itu dipakai `estimated_days`."""
    return _Run(world, calendar or CapacityCalendar(world), efforts or {}, record_members).run()


class _Run:
    def __init__(
        self,
        world: SimWorld,
        calendar: CapacityCalendar,
        efforts: dict[str, float],
        record_members: bool,
    ) -> None:
        self.w = world
        self.cal = calendar
        self.record = record_members
        self.outcomes: dict[str, TaskOutcome] = {}
        self.warnings: list[ScheduleWarning] = []
        self.members = sorted(world.resources)
        self.helpers = [rid for rid in self.members if world.resources[rid].helps is not None]

        schedulable: list[SimTask] = []
        for task in world.tasks.values():
            if task.status == TaskStatus.DONE:
                self.outcomes[task.task_id] = TaskOutcome(
                    task.task_id,
                    TaskState.DONE,
                    start=task.actual_start,
                    finish=task.actual_finish,
                    owner=task.owner,
                    effort=task.estimated_days,
                )
            elif task.status == TaskStatus.DROPPED:
                self.outcomes[task.task_id] = TaskOutcome(task.task_id, TaskState.DROPPED)
            elif not world.schedulable(task):
                self.outcomes[task.task_id] = TaskOutcome(
                    task.task_id, TaskState.ON_HOLD, owner=task.owner
                )
            else:
                schedulable.append(task)
        schedulable.sort(key=lambda t: priority_key(world, t))

        self.order: list[str] = [t.task_id for t in schedulable]
        self.rank = {task_id: i for i, task_id in enumerate(self.order)}
        self.weight: dict[str, float] = {}
        self.effort: dict[str, float] = {}
        self.remaining: dict[str, float] = {}
        self.earliest: dict[str, date] = {}
        self.owner: dict[str, str | None] = {}
        self.started: dict[str, date] = {}
        self.block_left: dict[str, int] = {}
        self.dep_ready: dict[str, date] = {}
        self.pending: set[str] = set()
        self.unstarted: set[str] = set()
        self.queue: dict[str, list[int]] = {rid: [] for rid in self.members}
        self.holding: dict[str, list[str]] = {rid: [] for rid in self.members}
        self.pool: list[int] = []
        self.releases: list[tuple[date, str, str]] = []
        self.transfers: list[tuple[date, str, str]] = []

        for task in schedulable:
            task_id = task.task_id
            effort = task.estimated_days
            if not task.effort_fixed:
                effort = efforts.get(task_id, effort)
            remaining = world.initial_remaining(task, effort)
            self.effort[task_id] = max(effort, remaining)
            self.remaining[task_id] = remaining
            self.weight[task_id] = world.load_weight(task)
            owner = task.owner if task.owner in world.resources else None
            self.owner[task_id] = owner
            self.pending.add(task_id)
            if task.block_days > 0:
                self.block_left[task_id] = task.block_days
            if task.started:
                self.started[task_id] = task.actual_start
                if owner is not None:
                    self.holding[owner].append(task_id)
                else:
                    bisect.insort(self.pool, self.rank[task_id])
            else:
                self.earliest[task_id] = world.earliest(task)
                self.unstarted.add(task_id)
                target = self.queue[owner] if owner is not None else self.pool
                bisect.insort(target, self.rank[task_id])
            for release in task.releases:
                self.releases.append((release.on, task_id, release.resource_id))
            for transfer in task.transfers:
                self.transfers.append((transfer.on, task_id, transfer.resource_id))
        self.releases.sort()
        self.transfers.sort()

        self.preds: dict[str, list[tuple[str, float]]] = defaultdict(list)
        self.dependents: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for dependency in world.dependencies:
            if dependency.task_id in self.pending:
                pair = (dependency.depends_on_task_id, dependency.propagation_factor)
                self.preds[dependency.task_id].append(pair)
                self.dependents[dependency.depends_on_task_id].append(
                    (dependency.task_id, dependency.propagation_factor)
                )

    # --- jalannya simulasi ---------------------------------------------------------------------

    def run(self) -> ScheduleResult:
        self._initial_checks()
        member_days: dict[str, list[MemberDay]] = {rid: [] for rid in self.members}
        last_day = self.w.start_date
        release_at = transfer_at = 0
        for i, day in enumerate(self.cal.days):
            if not self.pending:
                break
            last_day = day
            while release_at < len(self.releases) and self.releases[release_at][0] <= day:
                _, task_id, resource_id = self.releases[release_at]
                self._release(task_id, resource_id, day)
                release_at += 1
            while transfer_at < len(self.transfers) and self.transfers[transfer_at][0] <= day:
                _, task_id, resource_id = self.transfers[transfer_at]
                self._transfer(task_id, resource_id)
                transfer_at += 1

            blocked = self._prepare_day(day)
            states: dict[str, list] = {}
            for rid in self.members:
                capacity = self.cal.at(rid, i)
                if capacity.working:
                    states[rid] = self._own_work(rid, capacity, day, blocked)
            if self.pool:
                self._share_pool(states, day, blocked)
            for rid in self.helpers:
                if rid in states:
                    self._help(rid, states[rid], day, blocked)

            for rid in self.members:
                capacity = self.cal.at(rid, i)
                state = states.get(rid)
                project, worked = (state[2], state[3]) if state else (0.0, [])
                if worked and project > EPS:
                    rate = min(1.0, state[1] / project)
                    for task_id in list(worked):
                        self.remaining[task_id] -= rate
                        if self.remaining[task_id] <= EPS:
                            self._finish(task_id, day, rid)
                if self.record:
                    member_days[rid].append(
                        MemberDay(
                            day=day,
                            availability=capacity.availability,
                            capacity=capacity.capacity,
                            project=project,
                            non_project=capacity.non_project,
                            mentoring=capacity.mentoring,
                            load=daily_load(capacity, project),
                            absence=capacity.absence,
                            tasks=tuple(worked),
                        )
                    )

        for task_id in [t for t in self.order if t in self.pending]:
            if task_id in self.pending:
                self._doom(task_id, *self._leftover_reason(task_id, last_day))
        return ScheduleResult(
            start_date=self.w.start_date,
            end_date=last_day,
            tasks=self.outcomes,
            members=member_days if self.record else {},
            warnings=self.warnings,
        )

    def _initial_checks(self) -> None:
        start = self.w.start_date
        for task_id in self.order:
            if task_id not in self.pending:
                continue
            for pred, _ in self.preds.get(task_id, []):
                pred_outcome = self.outcomes.get(pred)
                if task_id in self.unstarted and pred_outcome is not None:
                    if pred_outcome.state == TaskState.ON_HOLD:
                        self._doom(
                            task_id,
                            WarningCode.WAITS_ON_HOLD,
                            f"{task_id} menunggu {pred} yang sedang on hold",
                        )
                        break
            if task_id in self.pending and self.owner[task_id] is None:
                self._check_takers(task_id, start)

    def _prepare_day(self, day: date) -> set[str]:
        """Catat dependency yang terpenuhi dan kurangi hari blokir; kembalikan task tertahan."""
        for task_id in self.unstarted:
            if task_id not in self.dep_ready and self._deps_met(task_id):
                self.dep_ready[task_id] = day
        blocked: set[str] = set()
        for task_id, left in self.block_left.items():
            if left <= 0 or task_id not in self.pending:
                continue
            if task_id in self.started or (
                task_id in self.dep_ready and day >= self.earliest[task_id]
            ):
                self.block_left[task_id] = left - 1
                blocked.add(task_id)
        return blocked

    def _own_work(self, rid: str, capacity: DayCapacity, day: date, blocked: set[str]) -> list:
        free = capacity.free
        worked = [t for t in self.holding[rid] if t not in blocked]
        project = sum(self.weight[t] for t in worked)
        queue = self.queue[rid]
        j = 0
        while j < len(queue):
            task_id = self.order[queue[j]]
            if not self._ready(task_id, day, blocked):
                j += 1
                continue
            if not self._fits(project, self.weight[task_id], free):
                break
            queue.pop(j)
            self._start(task_id, rid, day)
            worked.append(task_id)
            project += self.weight[task_id]
        return [capacity, free, project, worked]

    def _share_pool(self, states: dict[str, list], day: date, blocked: set[str]) -> None:
        j = 0
        while j < len(self.pool):
            task_id = self.order[self.pool[j]]
            task = self.w.tasks[task_id]
            ready = (
                task_id not in blocked
                if task_id in self.started
                else self._ready(task_id, day, blocked)
            )
            best: tuple | None = None
            if ready:
                weight = self.weight[task_id]
                for rid, state in states.items():
                    resource = self.w.resources[rid]
                    if not resource.can_do(task.required_role_id):
                        continue
                    if not resource.works_on(task.project_id, day):
                        continue
                    if not self._fits(state[2], weight, state[1]):
                        continue
                    key = (
                        -(state[1] - state[2]),
                        resource.primary_role_id != task.required_role_id,
                        rid,
                    )
                    if best is None or key < best:
                        best = key
            if best is None:
                j += 1
                continue
            rid = best[2]
            self.pool.pop(j)
            self.owner[task_id] = rid
            state = states[rid]
            if task_id in self.started:
                self.holding[rid].append(task_id)
            else:
                self._start(task_id, rid, day)
            state[3].append(task_id)
            state[2] += self.weight[task_id]

    def _help(self, rid: str, state: list, day: date, blocked: set[str]) -> None:
        resource = self.w.resources[rid]
        helps = resource.helps
        if helps is None or day < helps.start:
            return
        for task_id in self.order:
            if task_id not in self.unstarted:
                continue
            owner = self.owner[task_id]
            if owner is None or owner == rid:
                continue
            task = self.w.tasks[task_id]
            if helps.project_id is not None and task.project_id != helps.project_id:
                continue
            if not resource.can_do(task.required_role_id):
                continue
            if not resource.works_on(task.project_id, day):
                continue
            if not self._ready(task_id, day, blocked):
                continue
            if not self._fits(state[2], self.weight[task_id], state[1]):
                break
            self.queue[owner].remove(self.rank[task_id])
            self.owner[task_id] = rid
            self._start(task_id, rid, day)
            state[3].append(task_id)
            state[2] += self.weight[task_id]

    # --- bantuan -------------------------------------------------------------------------------

    @staticmethod
    def _fits(project: float, weight: float, free: float) -> bool:
        if free <= EPS:
            return False
        # Satu task yang bobotnya melebihi kapasitas tetap boleh dikerjakan sendirian.
        return project <= EPS or project + weight <= free + EPS

    def _ready(self, task_id: str, day: date, blocked: set[str]) -> bool:
        return (
            task_id in self.dep_ready
            and day >= self.earliest[task_id]
            and task_id not in blocked
            and self.block_left.get(task_id, 0) <= 0
        )

    def _deps_met(self, task_id: str) -> bool:
        return all(self._satisfied(pred, factor) for pred, factor in self.preds.get(task_id, []))

    def _satisfied(self, pred: str, factor: float) -> bool:
        if pred in self.effort:
            done = self.effort[pred] - max(self.remaining[pred], 0.0)
            return done >= factor * self.effort[pred] - EPS
        outcome = self.outcomes.get(pred)
        if outcome is None:
            # Pendahulu di luar snapshot: tidak menahan.
            return True
        return outcome.state in (TaskState.DONE, TaskState.DROPPED)

    def _start(self, task_id: str, rid: str, day: date) -> None:
        self.started[task_id] = day
        self.unstarted.discard(task_id)
        self.holding[rid].append(task_id)

    def _finish(self, task_id: str, day: date, rid: str) -> None:
        self.remaining[task_id] = 0.0
        self.pending.discard(task_id)
        self.holding[rid].remove(task_id)
        self.outcomes[task_id] = TaskOutcome(
            task_id,
            TaskState.SCHEDULED,
            start=self.started[task_id],
            finish=day,
            owner=rid,
            effort=self.effort[task_id],
            dependency_ready=self.dep_ready.get(task_id),
        )

    def _release(self, task_id: str, resource_id: str, day: date) -> None:
        if task_id not in self.pending or self.owner.get(task_id) != resource_id:
            return
        if task_id in self.started:
            self.holding[resource_id].remove(task_id)
        else:
            self.queue[resource_id].remove(self.rank[task_id])
        self.owner[task_id] = None
        bisect.insort(self.pool, self.rank[task_id])
        self._check_takers(task_id, day)

    def _transfer(self, task_id: str, resource_id: str) -> None:
        """Pindahkan task yang belum selesai ke `resource_id`, dari pemiliknya atau dari pool."""
        if task_id not in self.pending or resource_id not in self.queue:
            return
        owner = self.owner[task_id]
        if owner == resource_id:
            return
        rank = self.rank[task_id]
        if owner is None:
            self.pool.remove(rank)
        elif task_id in self.started:
            self.holding[owner].remove(task_id)
        else:
            self.queue[owner].remove(rank)
        self.owner[task_id] = resource_id
        if task_id in self.started:
            self.holding[resource_id].append(task_id)
        else:
            bisect.insort(self.queue[resource_id], rank)

    def _check_takers(self, task_id: str, day: date) -> None:
        """Task di pool butuh setidaknya satu anggota yang masih dapat mengambilnya."""
        task = self.w.tasks[task_id]
        for resource in self.w.resources.values():
            if not resource.can_do(task.required_role_id):
                continue
            if resource.leave_date is not None and resource.leave_date <= day:
                continue
            excluded_from = resource.excluded_projects.get(task.project_id)
            if excluded_from is not None and excluded_from <= day:
                continue
            return
        self._doom(
            task_id,
            WarningCode.NO_MEMBER_WITH_ROLE,
            f"Tidak ada anggota dengan peran {task.required_role_id} yang dapat mengerjakan "
            f"{task_id}",
        )

    def _doom(self, task_id: str, code: WarningCode, message: str) -> None:
        if task_id not in self.pending:
            return
        self.pending.discard(task_id)
        self.unstarted.discard(task_id)
        owner = self.owner.get(task_id)
        rank = self.rank[task_id]
        if owner is not None:
            if task_id in self.holding[owner]:
                self.holding[owner].remove(task_id)
            if rank in self.queue[owner]:
                self.queue[owner].remove(rank)
        if rank in self.pool:
            self.pool.remove(rank)
        task = self.w.tasks[task_id]
        self.outcomes[task_id] = TaskOutcome(
            task_id,
            TaskState.UNSCHEDULED,
            start=self.started.get(task_id),
            owner=owner,
            effort=self.effort.get(task_id),
            dependency_ready=self.dep_ready.get(task_id),
            reason=message,
        )
        self.warnings.append(ScheduleWarning(code, message, task_id, task.project_id))
        for dependent, factor in self.dependents.get(task_id, []):
            if dependent in self.unstarted and not self._satisfied(task_id, factor):
                self._doom(
                    dependent,
                    WarningCode.WAITS_UNSCHEDULED,
                    f"{dependent} menunggu {task_id} yang tidak terjadwal",
                )

    def _leftover_reason(self, task_id: str, last_day: date) -> tuple[WarningCode, str]:
        task = self.w.tasks[task_id]
        if self.owner[task_id] is None:
            return (
                WarningCode.NO_MEMBER_WITH_ROLE,
                f"Tidak ada anggota dengan peran {task.required_role_id} yang memiliki kapasitas "
                f"untuk {task_id} sampai {last_day.isoformat()}",
            )
        return (
            WarningCode.HORIZON,
            f"{task_id} belum selesai sampai batas simulasi {last_day.isoformat()}",
        )
