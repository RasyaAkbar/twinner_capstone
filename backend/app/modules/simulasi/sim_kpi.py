"""KPI hasil simulasi dan perbandingannya dengan baseline (UC-03, SCRUM-28).

Daftar KPI di sini mengikuti kandidat SCRUM-31 (§I.4.2) dan masih sementara sampai disepakati
dengan PuTI. KPI Calculator and Comparator lengkap (SCRUM-32) dibangun di atas modul ini.

Semua KPI bernilai "lebih kecil lebih baik": tanggal selesai lebih awal, keterlambatan lebih
sedikit, beban lebih ringan, dan task bermasalah lebih sedikit. Status KPI skenario terhadap
baseline: `better` bila selisihnya negatif, `worse` bila positif, `same` bila nol.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum

from app.modules.data.workdays import count_working_days, next_working_day
from app.modules.simulasi.sim_calendar import EPS, CapacityCalendar
from app.modules.simulasi.sim_scheduler import (
    MemberDay,
    ScheduleResult,
    TaskState,
    daily_load,
)
from app.modules.simulasi.sim_world import SimWorld

ONE_DAY = timedelta(days=1)
# Beban harian di atas 100% kapasitas dianggap berlebih (ambang menunggu SCRUM-31).
OVERLOAD_THRESHOLD = 1.0


class KpiUnit(StrEnum):
    DATE = "date"
    WORKING_DAYS = "working_days"
    COUNT = "count"
    RATIO = "ratio"


class KpiStatus(StrEnum):
    BETTER = "better"
    SAME = "same"
    WORSE = "worse"


@dataclass(frozen=True)
class KpiDefinition:
    key: str
    label: str
    unit: KpiUnit
    definition: str
    formula: str
    better: str = "lower"


KPI_DEFINITIONS: tuple[KpiDefinition, ...] = (
    KpiDefinition(
        "completion_date",
        "Proyeksi tanggal selesai",
        KpiUnit.DATE,
        "Tanggal task terakhir proyek aktif selesai menurut simulasi.",
        "max(finish) semua task proyek aktif; selisih dihitung dalam hari kerja",
    ),
    KpiDefinition(
        "total_delay_days",
        "Total keterlambatan",
        KpiUnit.WORKING_DAYS,
        "Jumlah hari kerja keterlambatan setiap proyek aktif terhadap target rilisnya.",
        "sum(hari kerja dari target_launch_date sampai proyeksi selesai, 0 bila tepat waktu)",
    ),
    KpiDefinition(
        "projects_missing_target",
        "Proyek melewati target rilis",
        KpiUnit.COUNT,
        "Proyek aktif yang proyeksi selesainya melewati target rilis atau punya task yang "
        "tidak terjadwal, sehingga target peluncurannya tidak lagi terpenuhi.",
        "count(proyek dengan selesai > target_launch_date atau task unscheduled > 0)",
    ),
    KpiDefinition(
        "dependency_delayed_tasks",
        "Task tertunda karena dependency",
        KpiUnit.COUNT,
        "Task yang dependency-nya baru terpenuhi setelah tanggal mulai rencananya, atau tidak "
        "pernah terpenuhi.",
        "count(task dengan dependency_ready > hari kerja pertama planned_start)",
    ),
    KpiDefinition(
        "peak_load",
        "Beban harian tertinggi",
        KpiUnit.RATIO,
        "Resource Load Index harian tertinggi di antara semua anggota (1.0 = 100% kapasitas).",
        "max(bobot task dikerjakan / (capacity_reference x availability) + bagian non-proyek "
        "+ bagian mentoring)",
    ),
    KpiDefinition(
        "overloaded_member_days",
        "Hari anggota kelebihan beban",
        KpiUnit.COUNT,
        "Jumlah pasangan anggota-hari kerja dengan beban di atas 100% kapasitas.",
        f"count(beban harian > {OVERLOAD_THRESHOLD:g})",
    ),
    KpiDefinition(
        "unscheduled_tasks",
        "Task tidak terjadwal",
        KpiUnit.COUNT,
        "Task yang tidak dapat dikerjakan karena tidak ada anggota dengan perannya yang punya "
        "kapasitas, atau menunggu task lain yang tidak terjadwal.",
        "count(task berstatus unscheduled)",
    ),
    KpiDefinition(
        "dropped_tasks",
        "Task dikeluarkan dari scope",
        KpiUnit.COUNT,
        "Task yang dikeluarkan dari scope oleh skenario.",
        "count(task yang dikeluarkan Change Request)",
    ),
)
KPI_BY_KEY = {definition.key: definition for definition in KPI_DEFINITIONS}

KpiValue = date | float | None


@dataclass
class ProjectOutcome:
    project_id: str
    priority_rank: int
    target: date
    completion: date | None
    delay_days: int | None
    on_target: bool
    task_count: int
    unscheduled: int


def signed_working_days(earlier: date, later: date, holidays: frozenset[date]) -> int:
    """Selisih hari kerja `later - earlier`; negatif bila `later` lebih awal."""
    if later > earlier:
        return count_working_days(earlier + ONE_DAY, later, holidays)
    if later < earlier:
        return -count_working_days(later + ONE_DAY, earlier, holidays)
    return 0


def delay_after(target: date, finish: date, holidays: frozenset[date]) -> int:
    """Hari kerja keterlambatan setelah `target`; 0 bila tepat waktu."""
    return count_working_days(target + ONE_DAY, finish, holidays) if finish > target else 0


def member_loads(
    world: SimWorld, result: ScheduleResult, calendar: CapacityCalendar, until: date
) -> dict[str, list[MemberDay]]:
    """Beban harian setiap anggota dari `start_date` sampai `until`.

    Hari setelah simulasi selesai hanya berisi beban non-proyek dan mentoring, supaya baseline
    dan skenario yang selesai pada hari berbeda tetap dibandingkan pada rentang yang sama.
    """
    loads: dict[str, list[MemberDay]] = {}
    for resource_id in sorted(world.resources):
        days = [d for d in result.members.get(resource_id, []) if d.day <= until]
        after = days[-1].day + ONE_DAY if days else world.start_date
        for i in range(calendar.first_index(after), len(calendar.days)):
            day = calendar.days[i]
            if day > until:
                break
            capacity = calendar.at(resource_id, i)
            days.append(
                MemberDay(
                    day=day,
                    availability=capacity.availability,
                    capacity=capacity.capacity,
                    project=0.0,
                    non_project=capacity.non_project,
                    mentoring=capacity.mentoring,
                    load=daily_load(capacity, 0.0),
                    absence=capacity.absence,
                    tasks=(),
                )
            )
        loads[resource_id] = days
    return loads


@dataclass
class WeekLoad:
    """Beban satu anggota dalam satu minggu (Senin sampai Minggu)."""

    start: date
    # Rata-rata beban harian pada hari kerja anggota itu; None bila tidak bekerja seminggu penuh.
    average: float | None
    peak: float | None
    overloaded_days: int
    working_days: int


def week_start(day: date) -> date:
    return day - timedelta(days=day.weekday())


def weekly_loads(days: list[MemberDay]) -> dict[date, WeekLoad]:
    """Kelompokkan beban harian per minggu, berurutan."""
    grouped: dict[date, list[float | None]] = {}
    for member_day in days:
        grouped.setdefault(week_start(member_day.day), []).append(member_day.load)
    weeks: dict[date, WeekLoad] = {}
    for start, values in grouped.items():
        present = [v for v in values if v is not None]
        weeks[start] = WeekLoad(
            start=start,
            average=round(sum(present) / len(present), 4) if present else None,
            peak=round(max(present), 4) if present else None,
            overloaded_days=sum(1 for v in present if v > OVERLOAD_THRESHOLD + EPS),
            working_days=len(present),
        )
    return weeks


def project_outcomes(world: SimWorld, result: ScheduleResult) -> dict[str, ProjectOutcome]:
    """Proyeksi setiap proyek aktif: tanggal selesai, keterlambatan, dan task tidak terjadwal."""
    by_project: dict[str, list] = {}
    for task in world.tasks.values():
        outcome = result.tasks.get(task.task_id)
        if outcome is None or outcome.state == TaskState.DROPPED:
            continue
        by_project.setdefault(task.project_id, []).append(outcome)
    projects: dict[str, ProjectOutcome] = {}
    for project in world.projects.values():
        if not project.active:
            continue
        outcomes = by_project.get(project.project_id, [])
        finishes = [
            o.finish
            for o in outcomes
            if o.state in (TaskState.DONE, TaskState.SCHEDULED) and o.finish is not None
        ]
        unscheduled = sum(1 for o in outcomes if o.state == TaskState.UNSCHEDULED)
        completion = max(finishes) if finishes else None
        delay = (
            delay_after(project.target_launch_date, completion, world.holidays)
            if completion is not None
            else None
        )
        projects[project.project_id] = ProjectOutcome(
            project_id=project.project_id,
            priority_rank=project.priority_rank,
            target=project.target_launch_date,
            completion=completion,
            delay_days=delay,
            on_target=unscheduled == 0 and (delay or 0) == 0,
            task_count=len(outcomes),
            unscheduled=unscheduled,
        )
    return projects


def dependency_delayed(world: SimWorld, result: ScheduleResult) -> list[str]:
    """Task yang dependency-nya terpenuhi setelah hari kerja pertama rencana mulainya."""
    with_preds = {d.task_id for d in world.dependencies}
    delayed: list[str] = []
    for task_id in sorted(with_preds):
        task = world.tasks.get(task_id)
        outcome = result.tasks.get(task_id)
        if task is None or outcome is None or task.started or not world.schedulable(task):
            continue
        reference = next_working_day(max(world.earliest(task), world.start_date), world.holidays)
        if outcome.dependency_ready is None:
            if outcome.state == TaskState.UNSCHEDULED:
                delayed.append(task_id)
        elif outcome.dependency_ready > reference:
            delayed.append(task_id)
    return delayed


def calculate_kpis(
    world: SimWorld,
    result: ScheduleResult,
    loads: dict[str, list[MemberDay]],
    projects: dict[str, ProjectOutcome] | None = None,
) -> dict[str, KpiValue]:
    """Nilai setiap KPI untuk satu hasil simulasi."""
    projects = projects if projects is not None else project_outcomes(world, result)
    completions = [p.completion for p in projects.values() if p.completion is not None]
    daily = [d.load for days in loads.values() for d in days if d.load is not None]
    return {
        "completion_date": max(completions) if completions else None,
        "total_delay_days": float(sum(p.delay_days or 0 for p in projects.values())),
        "projects_missing_target": float(sum(1 for p in projects.values() if not p.on_target)),
        "dependency_delayed_tasks": float(len(dependency_delayed(world, result))),
        "peak_load": round(max(daily), 4) if daily else 0.0,
        "overloaded_member_days": float(
            sum(1 for load in daily if load > OVERLOAD_THRESHOLD + EPS)
        ),
        "unscheduled_tasks": float(
            sum(1 for o in result.tasks.values() if o.state == TaskState.UNSCHEDULED)
        ),
        "dropped_tasks": float(sum(1 for t in world.tasks.values() if t.removed)),
    }


def compare_values(
    definition: KpiDefinition,
    baseline: KpiValue,
    scenario: KpiValue,
    holidays: frozenset[date],
) -> tuple[float | None, KpiStatus]:
    """Selisih skenario - baseline dan statusnya. Selisih tanggal dalam hari kerja.

    Tanggal kosong berarti tidak ada proyeksi selesai; skenario tanpa proyeksi sementara
    baseline punya dianggap lebih buruk.
    """
    if definition.unit == KpiUnit.DATE:
        if baseline is None or scenario is None:
            if baseline is None and scenario is None:
                return None, KpiStatus.SAME
            return None, KpiStatus.WORSE if scenario is None else KpiStatus.BETTER
        delta = float(signed_working_days(baseline, scenario, holidays))
    else:
        delta = round(float(scenario or 0.0) - float(baseline or 0.0), 6)
    return delta, status_of(delta)


def status_of(delta: float) -> KpiStatus:
    if delta < -EPS:
        return KpiStatus.BETTER
    if delta > EPS:
        return KpiStatus.WORSE
    return KpiStatus.SAME
