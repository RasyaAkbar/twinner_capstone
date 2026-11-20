"""Mesin simulasi what-if: baseline dan satu skenario, berdampingan (Gambar 4.1, UC-03).

1. Muat baseline snapshot sebagai dunia simulasi (`sim_world`).
2. Terapkan aturan situasi pada salinannya (`sim_rules`, SCRUM-24).
3. Hitung kalender kapasitas keduanya (`sim_calendar`, SCRUM-25).
4-5. Jadwalkan keduanya hari demi hari (`sim_scheduler`, SCRUM-26).
6. Ubah hasilnya menjadi KPI (`sim_kpi`), dan bila diminta ulangi dengan effort teracak
   (`sim_replication`, SCRUM-27).

Modul ini tidak menyentuh basis data; `simulation_service` yang memuat snapshot dan menyimpan
hasilnya.
"""

import time
from dataclasses import dataclass
from datetime import date

from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import Params
from app.modules.simulasi.sim_calendar import CapacityCalendar
from app.modules.simulasi.sim_kpi import (
    KpiValue,
    ProjectOutcome,
    calculate_kpis,
    member_loads,
    project_outcomes,
)
from app.modules.simulasi.sim_replication import (
    DEFAULT_SEED,
    ReplicationSummary,
    run_replications,
)
from app.modules.simulasi.sim_rules import Targets, apply_scenario
from app.modules.simulasi.sim_scheduler import MemberDay, ScheduleResult, simulate
from app.modules.simulasi.sim_world import SimWorld, build_world
from app.modules.simulasi.snapshot_schemas import BaselineSnapshotData


@dataclass
class SideRun:
    """Hasil deterministik satu sisi (baseline atau skenario)."""

    world: SimWorld
    calendar: CapacityCalendar
    schedule: ScheduleResult
    loads: dict[str, list[MemberDay]]
    projects: dict[str, ProjectOutcome]
    kpis: dict[str, KpiValue]


@dataclass
class EngineOutput:
    start_date: date
    # Hari kerja terakhir yang dibandingkan (yang lebih akhir dari kedua sisi).
    end_date: date
    baseline: SideRun
    scenario: SideRun
    targets: Targets
    notes: list[str]
    replication: ReplicationSummary | None
    duration_ms: int


def run_engine(
    snapshot: BaselineSnapshotData,
    situation: Situation | str,
    params: Params,
    *,
    start_date: date | None = None,
    runs: int | None = None,
    seed: int = DEFAULT_SEED,
) -> EngineOutput:
    """Simulasikan baseline dan skenario; `runs` mengaktifkan replikasi Monte Carlo."""
    began = time.perf_counter()
    baseline_world = build_world(snapshot, start_date)
    applied = apply_scenario(baseline_world, situation, params)
    scenario_world = applied.world

    baseline_calendar = CapacityCalendar(baseline_world)
    scenario_calendar = CapacityCalendar(scenario_world)
    baseline_schedule = simulate(baseline_world, baseline_calendar)
    scenario_schedule = simulate(scenario_world, scenario_calendar)
    until = max(baseline_schedule.end_date, scenario_schedule.end_date)

    sides = [
        _side(world, calendar, schedule, until)
        for world, calendar, schedule in (
            (baseline_world, baseline_calendar, baseline_schedule),
            (scenario_world, scenario_calendar, scenario_schedule),
        )
    ]
    replication = None
    if runs:
        replication = run_replications(
            baseline_world,
            scenario_world,
            runs=runs,
            seed=seed,
            baseline_calendar=baseline_calendar,
            scenario_calendar=scenario_calendar,
        )
    return EngineOutput(
        start_date=baseline_world.start_date,
        end_date=until,
        baseline=sides[0],
        scenario=sides[1],
        targets=applied.targets,
        notes=baseline_world.notes + applied.notes,
        replication=replication,
        duration_ms=round((time.perf_counter() - began) * 1000),
    )


def _side(
    world: SimWorld, calendar: CapacityCalendar, schedule: ScheduleResult, until: date
) -> SideRun:
    loads = member_loads(world, schedule, calendar, until)
    projects = project_outcomes(world, schedule)
    return SideRun(
        world=world,
        calendar=calendar,
        schedule=schedule,
        loads=loads,
        projects=projects,
        kpis=calculate_kpis(world, schedule, loads, projects),
    )
