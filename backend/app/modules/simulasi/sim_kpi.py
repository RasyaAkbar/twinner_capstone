"""Keluaran simulasi yang dipakai bersama hasil simulasi dan KPI evaluasi (UC-03, SCRUM-28).

KPI tidak didefinisikan di sini. Semua KPI (FR-04.1 s.d. FR-04.10) didefinisikan dan dihitung
oleh modul Evaluasi (`evaluasi.kpi_definitions`, `evaluasi.kpi_calculator`); modul ini hanya
menyediakan bahan yang dipakai kalkulator itu dan layar hasil simulasi:

- `member_loads`: beban harian setiap anggota pada rentang yang sama untuk kedua sisi.
- `project_outcomes`: proyeksi selesai setiap proyek aktif (dasar Schedule Variance).
- `weekly_loads`: utilisasi kapasitas harian per minggu. Ini bukan Resource Load Index; beban
  harian di atas 100% kapasitas (`CAPACITY_LIMIT`) berarti kapasitas terlampaui, sedangkan
  Overload Flag (FR-04.4) memakai RLI mingguan terhadap threshold-nya.
"""

from dataclasses import dataclass
from datetime import date, timedelta

from app.modules.data.workdays import count_working_days
from app.modules.simulasi.sim_calendar import EPS, CapacityCalendar
from app.modules.simulasi.sim_scheduler import (
    MemberDay,
    ScheduleResult,
    TaskState,
    daily_load,
)
from app.modules.simulasi.sim_world import SimWorld

ONE_DAY = timedelta(days=1)
# Beban harian di atas 100% kapasitas berarti kapasitas harian terlampaui.
CAPACITY_LIMIT = 1.0


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
    """Utilisasi kapasitas harian satu anggota dalam satu minggu (Senin sampai Minggu)."""

    start: date
    # Rata-rata beban harian pada hari kerja anggota itu; None bila tidak bekerja seminggu penuh.
    average: float | None
    peak: float | None
    over_capacity_days: int
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
            over_capacity_days=sum(1 for v in present if v > CAPACITY_LIMIT + EPS),
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
