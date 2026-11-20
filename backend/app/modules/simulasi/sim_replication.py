"""Replikasi Monte Carlo atas ketidakpastian estimasi effort (SCRUM-27).

Satu hasil deterministik (estimasi = `estimated_days`) terlihat lebih pasti dari kenyataannya
(§I.4.1, Law 2015). Mode replikasi menjalankan baseline dan skenario `runs` kali (bawaan 500).
Pada setiap replikasi, effort setiap task diundi dari distribusi segitiga antara `min_days` dan
`max_days` level kompleksitasnya (D-04) dengan puncak pada estimasi task itu (dijepit ke dalam
rentang). Task dengan estimasi dari parameter skenario (`effort_fixed`, misalnya Task Overrun)
tidak diundi.

Baseline dan skenario dalam satu replikasi memakai undian yang sama (common random numbers),
sehingga selisih keduanya hanya berasal dari skenario. Setiap replikasi memakai generator
`random.Random` dengan benih turunan dari `seed`, jadi hasilnya dapat diulang persis.

Keluaran: median, P10-P90, dan min-max untuk setiap KPI (baseline, skenario, dan selisihnya),
tanggal mulai dan selesai setiap task, proyeksi selesai dan keterlambatan setiap proyek, serta
beban mingguan setiap anggota.
"""

import math
import random
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.modules.simulasi.sim_calendar import CapacityCalendar
from app.modules.simulasi.sim_kpi import (
    KPI_DEFINITIONS,
    KpiStatus,
    KpiUnit,
    calculate_kpis,
    compare_values,
    member_loads,
    project_outcomes,
    status_of,
    weekly_loads,
)
from app.modules.simulasi.sim_scheduler import TaskState, simulate
from app.modules.simulasi.sim_world import SimWorld

DEFAULT_RUNS = 500
MIN_RUNS = 10
MAX_RUNS = 2000
DEFAULT_SEED = 42
# Effort hasil undian paling kecil, supaya level dengan min_days 0 tidak menghasilkan task kosong.
MIN_EFFORT = 0.1
# Benih replikasi ke-i = seed x SEED_STRIDE + i.
SEED_STRIDE = 1_000_003


@dataclass(frozen=True)
class Range:
    """Median dan rentang nilai di antara replikasi; `missing` = replikasi tanpa nilai."""

    median: date | float | None
    p10: date | float | None
    p90: date | float | None
    min: date | float | None
    max: date | float | None
    runs: int
    missing: int = 0


@dataclass
class KpiStats:
    baseline: Range
    scenario: Range
    delta: Range
    status: KpiStatus


@dataclass
class TaskStats:
    baseline_start: Range
    baseline_finish: Range
    scenario_start: Range
    scenario_finish: Range
    baseline_unscheduled_runs: int
    scenario_unscheduled_runs: int


@dataclass
class ProjectStats:
    baseline_completion: Range
    scenario_completion: Range
    baseline_delay: Range
    scenario_delay: Range


@dataclass
class WeekStats:
    baseline: Range
    scenario: Range
    baseline_overloaded_runs: int
    scenario_overloaded_runs: int


@dataclass
class ReplicationSummary:
    runs: int
    seed: int
    kpis: dict[str, KpiStats] = field(default_factory=dict)
    tasks: dict[str, TaskStats] = field(default_factory=dict)
    projects: dict[str, ProjectStats] = field(default_factory=dict)
    members: dict[str, dict[date, WeekStats]] = field(default_factory=dict)


# --- statistik ---------------------------------------------------------------------------------


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Persentil dengan interpolasi linier (sama dengan metode bawaan numpy)."""
    if not sorted_values:
        raise ValueError("Tidak ada nilai")
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def summarize(values: Sequence[float | None]) -> Range:
    present = sorted(v for v in values if v is not None)
    missing = len(values) - len(present)
    if not present:
        return Range(None, None, None, None, None, 0, missing)
    return Range(
        median=round(percentile(present, 0.5), 4),
        p10=round(percentile(present, 0.1), 4),
        p90=round(percentile(present, 0.9), 4),
        min=present[0],
        max=present[-1],
        runs=len(present),
        missing=missing,
    )


def summarize_dates(values: Sequence[date | None]) -> Range:
    """Seperti `summarize`, dihitung pada nomor hari lalu dibulatkan ke tanggal terdekat."""
    ordinals = summarize([v.toordinal() if v is not None else None for v in values])
    if ordinals.runs == 0:
        return ordinals

    def to_date(value: float | None) -> date | None:
        return date.fromordinal(math.floor(value + 0.5)) if value is not None else None

    return Range(
        median=to_date(ordinals.median),
        p10=to_date(ordinals.p10),
        p90=to_date(ordinals.p90),
        min=to_date(ordinals.min),
        max=to_date(ordinals.max),
        runs=ordinals.runs,
        missing=ordinals.missing,
    )


# --- undian effort -----------------------------------------------------------------------------


def sample_efforts(world: SimWorld, rng: random.Random) -> dict[str, float]:
    """Effort setiap task terbuka, diundi dalam urutan task_id supaya dapat diulang."""
    efforts: dict[str, float] = {}
    for task_id in sorted(world.tasks):
        task = world.tasks[task_id]
        if task.closed or task.effort_fixed:
            continue
        level = world.complexity.get(task.complexity_id or "")
        if level is None or level.max_days <= 0:
            continue
        low, high = level.min_days, level.max_days
        if high - low <= 1e-9:
            value = high
        else:
            mode = min(max(task.estimated_days, low), high)
            value = rng.triangular(low, high, mode)
        efforts[task_id] = max(value, MIN_EFFORT)
    return efforts


# --- replikasi ---------------------------------------------------------------------------------


def run_replications(
    baseline: SimWorld,
    scenario: SimWorld,
    runs: int = DEFAULT_RUNS,
    seed: int = DEFAULT_SEED,
    baseline_calendar: CapacityCalendar | None = None,
    scenario_calendar: CapacityCalendar | None = None,
) -> ReplicationSummary:
    baseline_calendar = baseline_calendar or CapacityCalendar(baseline)
    scenario_calendar = scenario_calendar or CapacityCalendar(scenario)
    kpi_values: dict[str, dict[str, list]] = {
        d.key: {"baseline": [], "scenario": [], "delta": []} for d in KPI_DEFINITIONS
    }
    task_values: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    unscheduled: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    project_values: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    week_values: dict[str, dict[date, dict[str, list]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(list))
    )
    week_overloaded: dict[str, dict[date, dict[str, int]]] = defaultdict(
        lambda: defaultdict(lambda: defaultdict(int))
    )
    task_ids = sorted(set(baseline.tasks) | set(scenario.tasks))
    project_ids = sorted(
        {p.project_id for p in baseline.projects.values() if p.active}
        | {p.project_id for p in scenario.projects.values() if p.active}
    )

    for i in range(runs):
        efforts = sample_efforts(baseline, random.Random(seed * SEED_STRIDE + i))
        results = {
            side: (world, calendar, simulate(world, calendar, efforts))
            for side, world, calendar in (
                ("baseline", baseline, baseline_calendar),
                ("scenario", scenario, scenario_calendar),
            )
        }
        until = max(r[2].end_date for r in results.values())
        kpis: dict[str, dict] = {}
        for side, (world, calendar, result) in results.items():
            loads = member_loads(world, result, calendar, until)
            projects = project_outcomes(world, result)
            kpis[side] = calculate_kpis(world, result, loads, projects)
            for task_id in task_ids:
                outcome = result.tasks.get(task_id)
                if outcome is None:
                    continue
                if outcome.state == TaskState.UNSCHEDULED:
                    unscheduled[task_id][side] += 1
                task_values[task_id][f"{side}_start"].append(outcome.start)
                task_values[task_id][f"{side}_finish"].append(outcome.finish)
            for project_id in project_ids:
                project = projects.get(project_id)
                project_values[project_id][f"{side}_completion"].append(
                    project.completion if project else None
                )
                project_values[project_id][f"{side}_delay"].append(
                    float(project.delay_days)
                    if project and project.delay_days is not None
                    else None
                )
            for resource_id, days in loads.items():
                for week, stats in weekly_loads(days).items():
                    week_values[resource_id][week][side].append(stats.average)
                    if stats.overloaded_days:
                        week_overloaded[resource_id][week][side] += 1
        for definition in KPI_DEFINITIONS:
            b, s = kpis["baseline"][definition.key], kpis["scenario"][definition.key]
            delta, _ = compare_values(definition, b, s, baseline.holidays)
            values = kpi_values[definition.key]
            values["baseline"].append(b)
            values["scenario"].append(s)
            values["delta"].append(delta)

    summary = ReplicationSummary(runs=runs, seed=seed)
    for definition in KPI_DEFINITIONS:
        values = kpi_values[definition.key]
        summarize_value = summarize_dates if definition.unit == KpiUnit.DATE else summarize
        delta = summarize(values["delta"])
        summary.kpis[definition.key] = KpiStats(
            baseline=summarize_value(values["baseline"]),
            scenario=summarize_value(values["scenario"]),
            delta=delta,
            status=status_of(delta.median) if delta.median is not None else KpiStatus.SAME,
        )
    for task_id, values in task_values.items():
        summary.tasks[task_id] = TaskStats(
            baseline_start=summarize_dates(values["baseline_start"]),
            baseline_finish=summarize_dates(values["baseline_finish"]),
            scenario_start=summarize_dates(values["scenario_start"]),
            scenario_finish=summarize_dates(values["scenario_finish"]),
            baseline_unscheduled_runs=unscheduled[task_id]["baseline"],
            scenario_unscheduled_runs=unscheduled[task_id]["scenario"],
        )
    for project_id, values in project_values.items():
        summary.projects[project_id] = ProjectStats(
            baseline_completion=summarize_dates(values["baseline_completion"]),
            scenario_completion=summarize_dates(values["scenario_completion"]),
            baseline_delay=summarize(values["baseline_delay"]),
            scenario_delay=summarize(values["scenario_delay"]),
        )
    for resource_id, weeks in week_values.items():
        summary.members[resource_id] = {
            week: WeekStats(
                baseline=summarize(values["baseline"]),
                scenario=summarize(values["scenario"]),
                baseline_overloaded_runs=week_overloaded[resource_id][week]["baseline"],
                scenario_overloaded_runs=week_overloaded[resource_id][week]["scenario"],
            )
            for week, values in sorted(weeks.items())
        }
    return summary
