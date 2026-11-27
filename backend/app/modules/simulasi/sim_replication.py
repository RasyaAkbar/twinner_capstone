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

KPI setiap replikasi dihitung dengan KPI Calculator modul Evaluasi (`kpi_replication`), sama
dengan KPI hasil deterministik. Keluaran: median, P10-P90, dan min-max untuk setiap KPI
(baseline, skenario, dan selisihnya), tanggal mulai dan selesai setiap task, proyeksi selesai
dan Schedule Variance setiap proyek, serta Resource Load Index mingguan setiap anggota.
"""

import random
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date

from app.modules.evaluasi.kpi_calculator import Reference, SimulationOutput
from app.modules.evaluasi.kpi_definitions import EvaluationConfig
from app.modules.evaluasi.kpi_replication import KpiCollector, ReplicatedKpis
from app.modules.simulasi.sim_calendar import EPS, CapacityCalendar
from app.modules.simulasi.sim_kpi import member_loads, project_outcomes
from app.modules.simulasi.sim_scheduler import TaskState, simulate
from app.modules.simulasi.sim_stats import (  # noqa: F401 (diekspor ulang)
    Range,
    percentile,
    summarize,
    summarize_dates,
)
from app.modules.simulasi.sim_world import SimWorld

DEFAULT_RUNS = 500
MIN_RUNS = 10
MAX_RUNS = 2000
DEFAULT_SEED = 42
# Effort hasil undian paling kecil, supaya level dengan min_days 0 tidak menghasilkan task kosong.
MIN_EFFORT = 0.1
# Benih replikasi ke-i = seed x SEED_STRIDE + i.
SEED_STRIDE = 1_000_003


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
    baseline_sv: Range
    scenario_sv: Range
    # Proporsi replikasi proyek ini diluncurkan tepat waktu (SV <= 0, FR-04.2).
    baseline_on_time_probability: float | None
    scenario_on_time_probability: float | None


@dataclass
class WeekStats:
    """Resource Load Index satu anggota pada satu minggu di antara replikasi."""

    baseline: Range
    scenario: Range
    # Replikasi dengan RLI minggu itu di atas threshold Overload Flag (FR-04.4).
    baseline_overloaded_runs: int
    scenario_overloaded_runs: int


@dataclass
class ReplicationSummary:
    runs: int
    seed: int
    kpis: ReplicatedKpis
    tasks: dict[str, TaskStats] = field(default_factory=dict)
    projects: dict[str, ProjectStats] = field(default_factory=dict)
    members: dict[str, dict[date, WeekStats]] = field(default_factory=dict)


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
    *,
    reference: Reference | None = None,
    config: EvaluationConfig | None = None,
) -> ReplicationSummary:
    """`reference` kosong berarti estimasi baseline dunia `baseline` tanpa data QA reject."""
    config = config or EvaluationConfig()
    reference = reference or Reference(
        estimates={t.task_id: t.estimated_days for t in baseline.tasks.values()}
    )
    calendars = {
        "baseline": baseline_calendar or CapacityCalendar(baseline),
        "scenario": scenario_calendar or CapacityCalendar(scenario),
    }
    worlds = {"baseline": baseline, "scenario": scenario}
    collector = KpiCollector(
        {side: (worlds[side], calendars[side]) for side in worlds}, reference, config
    )
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
        schedules = {side: simulate(worlds[side], calendars[side], efforts) for side in worlds}
        until = max(schedule.end_date for schedule in schedules.values())
        outputs = {
            side: SimulationOutput(
                worlds[side],
                schedules[side],
                member_loads(worlds[side], schedules[side], calendars[side], until),
                calendars[side],
                until,
            )
            for side in worlds
        }
        kpis = collector.add(outputs)
        for side, output in outputs.items():
            result = output.schedule
            for task_id in task_ids:
                outcome = result.tasks.get(task_id)
                if outcome is None:
                    continue
                if outcome.state == TaskState.UNSCHEDULED:
                    unscheduled[task_id][side] += 1
                task_values[task_id][f"{side}_start"].append(outcome.start)
                task_values[task_id][f"{side}_finish"].append(outcome.finish)
            projects = project_outcomes(output.world, result)
            sv = kpis[side].project_sv
            for project_id in project_ids:
                project = projects.get(project_id)
                project_values[project_id][f"{side}_completion"].append(
                    project.completion if project else None
                )
                value = sv.get(project_id)
                project_values[project_id][f"{side}_sv"].append(
                    float(value) if value is not None else None
                )
            for resource_id, weeks in kpis[side].member_weeks.items():
                for week, load in weeks.items():
                    week_values[resource_id][week][side].append(load.rli)
                    if load.rli > config.overload_threshold + EPS:
                        week_overloaded[resource_id][week][side] += 1

    replicated = collector.result(seed)
    summary = ReplicationSummary(runs=runs, seed=seed, kpis=replicated)
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
            baseline_sv=summarize(values["baseline_sv"]),
            scenario_sv=summarize(values["scenario_sv"]),
            baseline_on_time_probability=replicated.project_on_time["baseline"].get(project_id),
            scenario_on_time_probability=replicated.project_on_time["scenario"].get(project_id),
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
