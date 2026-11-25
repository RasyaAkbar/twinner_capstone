"""KPI evaluasi di setiap replikasi Monte Carlo: median dan rentangnya.

Undian effort sama persis dengan `sim_replication.run_replications` modul Simulasi: replikasi
ke-i memakai `random.Random(seed x SEED_STRIDE + i)` dan `sample_efforts` dunia baseline, dan
baseline serta skenario memakai undian yang sama (common random numbers). Jadi run tersimpan
dengan `replications` dan `seed` tertentu menghasilkan replikasi yang sama dengan yang dilihat
pengguna di hasil simulasi.

Setiap replikasi menghitung semua KPI kedua sisi dengan `kpi_calculator.calculate`. Keluarannya
median, P10-P90, dan min-max baseline, skenario, dan selisihnya per KPI. On-Time Launch
Probability adalah proporsi replikasi yang semua proyeknya tepat waktu, jadi tidak punya rentang.
"""

import random
from collections import defaultdict
from dataclasses import dataclass, field

from app.modules.evaluasi.kpi_calculator import Reference, SimulationOutput, calculate, item_weeks
from app.modules.evaluasi.kpi_definitions import KPI_KEYS, ON_TIME_PROBABILITY, EvaluationConfig
from app.modules.simulasi.sim_calendar import CapacityCalendar
from app.modules.simulasi.sim_kpi import member_loads
from app.modules.simulasi.sim_replication import SEED_STRIDE, Range, sample_efforts, summarize
from app.modules.simulasi.sim_scheduler import simulate
from app.modules.simulasi.sim_world import SimWorld

SIDES = ("baseline", "scenario")


@dataclass
class ReplicatedKpis:
    runs: int
    seed: int
    # Rentang nilai per sisi ("baseline", "scenario", "delta") per KPI; None untuk KPI tanpa
    # rentang (On-Time Launch Probability).
    ranges: dict[str, dict[str, Range | None]] = field(default_factory=dict)
    # Proporsi replikasi tepat waktu per sisi, keseluruhan dan per proyek.
    on_time: dict[str, float] = field(default_factory=dict)
    project_on_time: dict[str, dict[str, float]] = field(default_factory=dict)


def replicate(
    worlds: dict[str, tuple[SimWorld, CapacityCalendar]],
    reference: Reference,
    config: EvaluationConfig,
    runs: int,
    seed: int,
) -> ReplicatedKpis:
    """`worlds` berisi dunia dan kalender kapasitas sisi "baseline" dan "scenario"."""
    items = {side: item_weeks(*worlds[side]) for side in SIDES}
    values: dict[str, dict[str, list[float | None]]] = {
        key: {"baseline": [], "scenario": [], "delta": []} for key in KPI_KEYS
    }
    on_time = {side: 0 for side in SIDES}
    project_on_time: dict[str, dict[str, int]] = {side: defaultdict(int) for side in SIDES}
    projects: dict[str, set[str]] = {side: set() for side in SIDES}

    baseline_world = worlds["baseline"][0]
    for i in range(runs):
        efforts = sample_efforts(baseline_world, random.Random(seed * SEED_STRIDE + i))
        schedules = {side: simulate(worlds[side][0], worlds[side][1], efforts) for side in SIDES}
        until = max(schedule.end_date for schedule in schedules.values())
        kpis = {}
        for side in SIDES:
            world, calendar = worlds[side]
            loads = member_loads(world, schedules[side], calendar, until)
            output = SimulationOutput(world, schedules[side], loads, calendar, until)
            kpis[side] = calculate(output, reference, config, items[side])
            on_time[side] += kpis[side].on_time
            for project_id, sv in kpis[side].project_sv.items():
                projects[side].add(project_id)
                project_on_time[side][project_id] += sv is not None and sv <= 0
        for key in KPI_KEYS:
            b = kpis["baseline"].values[key]
            s = kpis["scenario"].values[key]
            values[key]["baseline"].append(b)
            values[key]["scenario"].append(s)
            values[key]["delta"].append(
                round(s - b, 6) if b is not None and s is not None else None
            )

    result = ReplicatedKpis(runs=runs, seed=seed)
    for key in KPI_KEYS:
        if key == ON_TIME_PROBABILITY:
            result.ranges[key] = {"baseline": None, "scenario": None, "delta": None}
            continue
        result.ranges[key] = {part: summarize(values[key][part]) for part in values[key]}
    for side in SIDES:
        result.on_time[side] = round(on_time[side] / runs, 6)
        result.project_on_time[side] = {
            project_id: round(project_on_time[side][project_id] / runs, 6)
            for project_id in sorted(projects[side])
        }
    return result
