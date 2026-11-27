"""KPI Calculator (SCRUM-32): KPI FR-04.1 s.d. FR-04.10 dari keluaran satu simulasi.

Masukannya keluaran mesin simulasi modul Simulasi untuk satu sisi (baseline atau skenario):

- `ScheduleResult.tasks`: status, tanggal mulai dan selesai, serta pemilik setiap task.
- beban harian setiap anggota (`MemberDay` dari `sim_kpi.member_loads`): hari kerja anggota
  dan task yang dikerjakannya setiap hari.
- `SimWorld` dan `CapacityCalendar`: target proyek, rencana task, dependency, item D-08.

Modul ini tidak menyentuh basis data dan tidak menjalankan simulasi. Setiap rumus adalah fungsi
kecil yang dapat dites dengan hitungan manual; `calculate` merangkainya.

Penerjemahan rumus ke data simulasi (asumsi untuk ditinjau bersama PuTI):

- SV memakai proyeksi selesai proyek dari `sim_kpi.project_outcomes`. Proyek aktif yang punya
  task tidak terjadwal tidak dapat diluncurkan, jadi SV-nya kosong.
- RLI(r,w) dihitung per minggu Senin-Minggu, hanya untuk minggu yang anggota itu punya hari
  kerja. "Task proyek aktif" = task berbeda yang dikerjakan anggota pada minggu itu. Item D-08
  `operational` dihitung sebagai item operasional; tiket, hotfix, dan perbaikan bug skenario
  (Critical Bug) dihitung sebagai tiket terbuka, pada minggu item itu memakai kapasitas
  anggota.
- Keterlambatan task (CDI) = hari kerja dari planned_finish ke selesai simulasi, paling sedikit
  0. Task hilir hanya task yang masih dijadwalkan (scheduled/unscheduled); task yang dicapai
  lewat beberapa jalur memakai faktor jalur terbesar.
- Durasi aktual (EAR) = hari kerja dari mulai sampai selesai (inklusif) task yang selesai di
  simulasi; estimasinya `estimated_days` baseline, sehingga overrun skenario terlihat sebagai
  EAR yang lebih besar.
- Hari tunggu (FR-04.10) = hari kerja pemilik task yang task-nya sudah waktunya dimulai tetapi
  masih menunggu dependency, atau sedang tertahan blocker (Task Blocker).
"""

import heapq
import statistics
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.modules.data.enums import TaskStatus
from app.modules.data.workdays import count_working_days, next_working_day
from app.modules.evaluasi.kpi_definitions import (
    BLOCKED_RATIO,
    CASCADE_DELAY_IMPACT,
    ESTIMATE_ACCURACY,
    ON_TIME_PROBABILITY,
    OVERLOAD_FLAGS,
    REALLOCATED_RATIO,
    RESOURCE_LOAD_INDEX,
    REWORK_RATE,
    SCHEDULE_VARIANCE,
    SUSTAINED_OVERLOAD,
    WORKLOAD_BALANCE_INDEX,
    EvaluationConfig,
)
from app.modules.simulasi.sim_calendar import EPS, CapacityCalendar
from app.modules.simulasi.sim_kpi import project_outcomes, signed_working_days, week_start
from app.modules.simulasi.sim_scheduler import MemberDay, ScheduleResult, TaskState
from app.modules.simulasi.sim_world import NonProjectItem, SimWorld
from app.modules.simulasi.snapshot_schemas import BaselineSnapshotData

ONE_DAY = timedelta(days=1)
ONE_WEEK = timedelta(days=7)
# Label item D-08 dibentuk sim_world sebagai "<Jenis> <item_id>".
OPERATIONAL_PREFIX = "Operational"
OPEN_STATES = (TaskState.SCHEDULED, TaskState.UNSCHEDULED)
QA_STATUSES = frozenset({TaskStatus.IN_QA.value, TaskStatus.DONE.value})
NO_COMPLEXITY = "-"


@dataclass
class SimulationOutput:
    """Keluaran simulasi satu sisi sampai `until` (hari terakhir yang dibandingkan)."""

    world: SimWorld
    schedule: ScheduleResult
    loads: dict[str, list[MemberDay]]
    calendar: CapacityCalendar
    until: date


@dataclass(frozen=True)
class Reference:
    """Data acuan yang sama untuk baseline dan skenario."""

    # estimated_days baseline per task (estimasi awal sebelum skenario mengubahnya).
    estimates: dict[str, float] = field(default_factory=dict)
    # D-05 qa_reject_count per task.
    qa_rejects: dict[str, int] = field(default_factory=dict)


def reference_of(snapshot: BaselineSnapshotData, baseline_world: SimWorld) -> Reference:
    return Reference(
        estimates={t.task_id: t.estimated_days for t in baseline_world.tasks.values()},
        qa_rejects={t.task_id: t.qa_reject_count for t in snapshot.tasks},
    )


@dataclass(frozen=True)
class WeekLoad:
    tasks: int
    operational: int
    tickets: int
    rli: float


@dataclass
class SideKpis:
    """Nilai KPI satu sisi beserta rinciannya."""

    values: dict[str, float | None]
    on_time: bool
    project_sv: dict[str, int | None]
    member_weeks: dict[str, dict[date, WeekLoad]]
    soi: dict[str, float]
    wbi_weeks: dict[date, float]
    task_delay: dict[str, float]
    cdi: dict[str, float]
    ear_by_complexity: dict[str, float]
    ear_by_role: dict[str, float]
    working_days: int
    blocked_days: int
    reallocated_days: int


# --- FR-04.1, FR-04.2 ----------------------------------------------------------------------------


def schedule_variances(world: SimWorld, schedule: ScheduleResult) -> dict[str, int | None]:
    """SV setiap proyek aktif yang punya task; kosong bila proyek tidak dapat diluncurkan."""
    variances: dict[str, int | None] = {}
    for project_id, outcome in sorted(project_outcomes(world, schedule).items()):
        if outcome.task_count == 0:
            continue
        if outcome.unscheduled or outcome.completion is None:
            variances[project_id] = None
        else:
            variances[project_id] = signed_working_days(
                outcome.target, outcome.completion, world.holidays
            )
    return variances


def portfolio_sv(variances: dict[str, int | None]) -> float | None:
    """SV proyek paling terlambat; kosong bila tidak ada proyek atau ada yang tidak dapat
    diluncurkan."""
    if not variances or any(v is None for v in variances.values()):
        return None
    return float(max(variances.values()))


def launched_on_time(variances: dict[str, int | None]) -> bool:
    return all(v is not None and v <= 0 for v in variances.values())


# --- FR-04.3 s.d. FR-04.6 ------------------------------------------------------------------------


def rli(
    tasks: int, operational: int, tickets: int, capacity: float, config: EvaluationConfig
) -> float:
    """RLI(r,w) = (wp x task + wo x operasional + wt x tiket) / kapasitas referensi."""
    return (config.wp * tasks + config.wo * operational + config.wt * tickets) / capacity


def is_operational(item: NonProjectItem) -> bool:
    return item.source == "baseline" and item.label.startswith(OPERATIONAL_PREFIX)


def item_days(item: NonProjectItem, calendar: CapacityCalendar) -> list[date]:
    """Hari kerja item memakai kapasitas anggota (sama dengan `CapacityCalendar._spread`)."""
    row = calendar.members.get(item.resource_id)
    if row is None:
        return []
    first = calendar.first_index(max(item.start, calendar.start))
    if item.end is not None:
        last = calendar.first_index(item.end + ONE_DAY)
        return [calendar.days[i] for i in range(first, last) if row[i].availability > EPS]
    days: list[date] = []
    remaining = item.working_days or 0.0
    i = first
    while remaining > EPS and i < len(calendar.days):
        if row[i].availability > EPS:
            days.append(calendar.days[i])
            remaining -= min(1.0, remaining)
        i += 1
    return days


@dataclass(frozen=True)
class ItemWeek:
    """Item aktif pada minggu tertentu: hari pertamanya di minggu itu."""

    item: int
    operational: bool
    first_day: date


def item_weeks(
    world: SimWorld, calendar: CapacityCalendar
) -> dict[str, dict[date, list[ItemWeek]]]:
    """Item D-08 setiap anggota per minggu. Tidak bergantung pada effort task, jadi dihitung
    sekali per sisi dan dipakai ulang oleh semua replikasi."""
    weeks: dict[str, dict[date, list[ItemWeek]]] = defaultdict(lambda: defaultdict(list))
    for index, item in enumerate(world.items):
        seen: set[date] = set()
        for day in item_days(item, calendar):
            week = week_start(day)
            if week in seen:
                continue
            seen.add(week)
            weeks[item.resource_id][week].append(ItemWeek(index, is_operational(item), day))
    return weeks


def weekly_rli(
    output: SimulationOutput,
    config: EvaluationConfig,
    items: dict[str, dict[date, list[ItemWeek]]] | None = None,
) -> dict[str, dict[date, WeekLoad]]:
    """RLI setiap anggota pada setiap minggu yang ia punya hari kerja."""
    items = items if items is not None else item_weeks(output.world, output.calendar)
    result: dict[str, dict[date, WeekLoad]] = {}
    for resource_id, days in sorted(output.loads.items()):
        resource = output.world.resources[resource_id]
        capacity = config.capacity_reference or resource.capacity_reference
        tasks_by_week: dict[date, set[str]] = {}
        for member_day in days:
            if member_day.load is None or member_day.day > output.until:
                continue
            tasks_by_week.setdefault(week_start(member_day.day), set()).update(member_day.tasks)
        weeks: dict[date, WeekLoad] = {}
        for week, tasks in sorted(tasks_by_week.items()):
            active = [
                entry
                for entry in items.get(resource_id, {}).get(week, [])
                if entry.first_day <= output.until
            ]
            operational = sum(1 for entry in active if entry.operational)
            tickets = len(active) - operational
            weeks[week] = WeekLoad(
                tasks=len(tasks),
                operational=operational,
                tickets=tickets,
                rli=round(rli(len(tasks), operational, tickets, capacity, config), 6),
            )
        result[resource_id] = weeks
    return result


def overload_flags(rli_by_member: dict[str, dict[date, float]], threshold: float) -> int:
    return sum(
        1 for weeks in rli_by_member.values() for value in weeks.values() if value > threshold + EPS
    )


def workload_balance(
    rli_by_member: dict[str, dict[date, float]],
) -> tuple[float | None, dict[date, float]]:
    """WBI(w) = 1 - CV RLI anggota yang bekerja pada minggu w; WBI = rata-rata WBI(w).

    CV memakai simpangan baku populasi. Minggu yang RLI semua anggotanya 0 dianggap merata (1).
    """
    by_week: dict[date, list[float]] = defaultdict(list)
    for weeks in rli_by_member.values():
        for week, value in weeks.items():
            by_week[week].append(value)
    per_week: dict[date, float] = {}
    for week, values in sorted(by_week.items()):
        mean = sum(values) / len(values)
        per_week[week] = 1.0 if mean <= EPS else 1.0 - statistics.pstdev(values) / mean
    if not per_week:
        return None, per_week
    return sum(per_week.values()) / len(per_week), per_week


def sustained_overload(weeks: dict[date, float], threshold: float, min_weeks: int) -> float:
    """SOI(r): untuk setiap periode minggu berturut-turut dengan RLI > threshold yang panjangnya
    paling sedikit `min_weeks`, panjang periode x rata-rata ekses; dijumlah semua periode.

    Minggu tanpa hari kerja (anggota absen seminggu penuh) memutus periode.
    """
    total = 0.0
    excess: list[float] = []
    previous: date | None = None

    def close() -> float:
        if len(excess) < min_weeks:
            return 0.0
        return len(excess) * (sum(excess) / len(excess))

    for week in sorted(weeks):
        value = weeks[week]
        consecutive = previous is not None and week - previous == ONE_WEEK
        if value > threshold + EPS:
            if excess and not consecutive:
                total += close()
                excess = []
            excess.append(value - threshold)
        else:
            total += close()
            excess = []
        previous = week
    return total + close()


# --- FR-04.7 -------------------------------------------------------------------------------------


def cascade_delay(
    delays: dict[str, float], successors: dict[str, list[tuple[str, float]]]
) -> dict[str, float]:
    """CDI(t) = keterlambatan(t) x jumlah faktor jalur terbesar t -> u untuk setiap task hilir u.

    Faktor jalur = hasil kali faktor propagasi dependency di sepanjang jalur. Karena faktor
    paling besar 1, jalur terbesar dicari seperti jalur terpendek (Dijkstra).
    """
    impact: dict[str, float] = {}
    for source, delay in sorted(delays.items()):
        if delay <= EPS:
            continue
        best: dict[str, float] = {}
        heap: list[tuple[float, str]] = [(-1.0, source)]
        visited: set[str] = set()
        while heap:
            negative, node = heapq.heappop(heap)
            if node in visited:
                continue
            visited.add(node)
            for successor, factor in successors.get(node, []):
                if successor == source:
                    continue
                reach = -negative * factor
                if reach > best.get(successor, 0.0) + EPS:
                    best[successor] = reach
                    heapq.heappush(heap, (-reach, successor))
        impact[source] = delay * sum(best.values())
    return impact


def task_delays(world: SimWorld, schedule: ScheduleResult) -> dict[str, float]:
    """Hari kerja task yang selesai di simulasi melewati planned_finish-nya (paling sedikit 0)."""
    delays: dict[str, float] = {}
    for task_id, outcome in schedule.tasks.items():
        task = world.tasks.get(task_id)
        if task is None or outcome.state != TaskState.SCHEDULED or outcome.finish is None:
            continue
        delays[task_id] = float(
            max(0, signed_working_days(task.planned_finish, outcome.finish, world.holidays))
        )
    return delays


def dependency_successors(
    world: SimWorld, schedule: ScheduleResult, config: EvaluationConfig
) -> dict[str, list[tuple[str, float]]]:
    """Task hilir langsung setiap task beserta faktor propagasinya."""
    successors: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for dependency in world.dependencies:
        outcome = schedule.tasks.get(dependency.task_id)
        if outcome is None or outcome.state not in OPEN_STATES:
            continue
        if dependency.dependency_type == "hard":
            factor = config.hard_factor
        else:
            factor = config.soft_factor or dependency.propagation_factor
        successors[dependency.depends_on_task_id].append((dependency.task_id, factor))
    return successors


# --- FR-04.8, FR-04.9 ----------------------------------------------------------------------------


@dataclass(frozen=True)
class Duration:
    complexity_id: str
    role_id: str
    actual: float
    estimate: float


def estimate_accuracy(
    durations: Iterable[Duration],
) -> tuple[float | None, dict[str, float], dict[str, float]]:
    """EAR keseluruhan, per level kompleksitas, dan per role: jumlah aktual / jumlah estimasi."""
    actual: dict[tuple[str, str], float] = defaultdict(float)
    estimate: dict[tuple[str, str], float] = defaultdict(float)
    for row in durations:
        for group in (("all", ""), ("complexity", row.complexity_id), ("role", row.role_id)):
            actual[group] += row.actual
            estimate[group] += row.estimate

    def ratio(group: tuple[str, str]) -> float:
        return round(actual[group] / estimate[group], 6)

    groups = [g for g in estimate if estimate[g] > EPS]
    overall = ratio(("all", "")) if ("all", "") in groups else None
    by_complexity = {g[1]: ratio(g) for g in sorted(groups) if g[0] == "complexity"}
    by_role = {g[1]: ratio(g) for g in sorted(groups) if g[0] == "role"}
    return overall, by_complexity, by_role


def task_durations(
    world: SimWorld, schedule: ScheduleResult, reference: Reference
) -> list[Duration]:
    rows: list[Duration] = []
    for task_id, outcome in sorted(schedule.tasks.items()):
        task = world.tasks.get(task_id)
        if task is None or outcome.state != TaskState.SCHEDULED:
            continue
        if outcome.start is None or outcome.finish is None:
            continue
        rows.append(
            Duration(
                complexity_id=task.complexity_id or NO_COMPLEXITY,
                role_id=task.required_role_id,
                actual=float(count_working_days(outcome.start, outcome.finish, world.holidays)),
                estimate=reference.estimates.get(task_id, task.estimated_days),
            )
        )
    return rows


def rework_rate(world: SimWorld, qa_rejects: dict[str, int]) -> float | None:
    """Task dengan qa_reject_count > 0 / task yang sudah masuk fase QA (in_qa, done, atau pernah
    dikembalikan QA). Task yang dibatalkan atau dikeluarkan dari scope tidak dihitung."""
    entered = rejected = 0
    for task in world.tasks.values():
        if task.removed or task.status == TaskStatus.DROPPED:
            continue
        rejects = qa_rejects.get(task.task_id, 0)
        if task.status in QA_STATUSES or rejects > 0:
            entered += 1
            rejected += rejects > 0
    return round(rejected / entered, 6) if entered else None


# --- FR-04.10 ------------------------------------------------------------------------------------


def waiting_days(output: SimulationOutput) -> dict[str, set[date]]:
    """Hari kerja kalender saat task milik setiap anggota menunggu dependency atau blocker."""
    world, schedule, calendar = output.world, output.schedule, output.calendar
    after_until = output.until + ONE_DAY
    waiting: dict[str, set[date]] = defaultdict(set)

    def working_days(start: date, end: date) -> list[date]:
        """Hari kerja kalender `start` <= hari < `end`."""
        return calendar.days[calendar.first_index(start) : calendar.first_index(end)]

    def first_working_days(start: date, count: int, end: date) -> list[date]:
        i = calendar.first_index(start)
        return [day for day in calendar.days[i : i + count] if day < end]

    for task in world.tasks.values():
        owner = task.owner
        outcome = schedule.tasks.get(task.task_id)
        if owner is None or owner not in world.resources or outcome is None:
            continue
        if outcome.state not in OPEN_STATES:
            continue
        if task.started:
            if task.block_days > 0:
                end = (outcome.finish + ONE_DAY) if outcome.finish else after_until
                waiting[owner].update(first_working_days(world.start_date, task.block_days, end))
            continue
        end = outcome.start or after_until
        due = next_working_day(max(world.earliest(task), world.start_date), world.holidays)
        ready = outcome.dependency_ready
        waiting[owner].update(working_days(due, min(ready or end, end)))
        if task.block_days > 0 and ready is not None:
            waiting[owner].update(first_working_days(max(due, ready), task.block_days, end))
    return waiting


def time_ratios(
    loads: dict[str, list[MemberDay]], waiting: dict[str, set[date]], until: date
) -> tuple[int, int, int]:
    """(hari kerja anggota, hari tunggu tanpa pekerjaan pengganti, hari tunggu yang terisi)."""
    working = blocked = reallocated = 0
    for resource_id, days in loads.items():
        waits = waiting.get(resource_id, set())
        for member_day in days:
            if member_day.load is None or member_day.day > until:
                continue
            working += 1
            if member_day.day in waits:
                if member_day.tasks:
                    reallocated += 1
                else:
                    blocked += 1
    return working, blocked, reallocated


# --- rangkaian -----------------------------------------------------------------------------------


def calculate(
    output: SimulationOutput,
    reference: Reference,
    config: EvaluationConfig,
    items: dict[str, dict[date, list[ItemWeek]]] | None = None,
) -> SideKpis:
    """Semua KPI satu sisi. `items` (dari `item_weeks`) boleh diisi untuk dipakai ulang."""
    world, schedule = output.world, output.schedule
    variances = schedule_variances(world, schedule)
    on_time = launched_on_time(variances)

    member_weeks = weekly_rli(output, config, items)
    rli_values = {
        rid: {week: load.rli for week, load in weeks.items()} for rid, weeks in member_weeks.items()
    }
    all_rli = [value for weeks in rli_values.values() for value in weeks.values()]
    wbi, wbi_weeks = workload_balance(rli_values)
    soi = {
        rid: round(sustained_overload(weeks, config.overload_threshold, config.soi_min_weeks), 6)
        for rid, weeks in rli_values.items()
    }

    delays = task_delays(world, schedule)
    cdi = cascade_delay(delays, dependency_successors(world, schedule, config))
    ear, ear_by_complexity, ear_by_role = estimate_accuracy(
        task_durations(world, schedule, reference)
    )
    working, blocked, reallocated = time_ratios(output.loads, waiting_days(output), output.until)
    waited = blocked + reallocated

    values: dict[str, float | None] = {
        SCHEDULE_VARIANCE: portfolio_sv(variances),
        ON_TIME_PROBABILITY: 1.0 if on_time else 0.0,
        RESOURCE_LOAD_INDEX: round(max(all_rli), 6) if all_rli else 0.0,
        OVERLOAD_FLAGS: float(overload_flags(rli_values, config.overload_threshold)),
        WORKLOAD_BALANCE_INDEX: round(wbi, 6) if wbi is not None else None,
        SUSTAINED_OVERLOAD: round(sum(soi.values()), 6),
        CASCADE_DELAY_IMPACT: round(sum(cdi.values()), 6),
        ESTIMATE_ACCURACY: ear,
        REWORK_RATE: rework_rate(world, reference.qa_rejects),
        BLOCKED_RATIO: round(blocked / working, 6) if working else None,
        REALLOCATED_RATIO: round(reallocated / waited, 6) if waited else None,
    }
    return SideKpis(
        values=values,
        on_time=on_time,
        project_sv=variances,
        member_weeks=member_weeks,
        soi=soi,
        wbi_weeks=wbi_weeks,
        task_delay=delays,
        cdi=cdi,
        ear_by_complexity=ear_by_complexity,
        ear_by_role=ear_by_role,
        working_days=working,
        blocked_days=blocked,
        reallocated_days=reallocated,
    )
