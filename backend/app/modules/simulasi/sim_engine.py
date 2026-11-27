"""Mesin simulasi what-if: baseline dan satu skenario, berdampingan (Gambar 4.1, UC-03).

1. Muat baseline snapshot sebagai dunia simulasi (`sim_world`).
2. Terapkan aturan situasi pada salinannya (`sim_rules`, SCRUM-24), lalu respons kandidat DSS
   bila ada (`sim_actions`, SCRUM-63).
3. Hitung kalender kapasitas keduanya (`sim_calendar`, SCRUM-25).
4-5. Jadwalkan keduanya hari demi hari (`sim_scheduler`, SCRUM-26).
6. Hitung KPI keduanya dengan KPI Calculator modul Evaluasi (`evaluasi.kpi_calculator`,
   FR-04.1 s.d. FR-04.10), dan bila diminta ulangi dengan effort teracak (`sim_replication`,
   SCRUM-27). KPI hasil simulasi dan hasil evaluasi karena itu selalu sama.

Modul ini tidak menyentuh basis data; `simulation_service` yang memuat snapshot dan menyimpan
hasilnya.
"""

import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.modules.evaluasi.kpi_calculator import (
    Reference,
    SideKpis,
    SimulationOutput,
    calculate,
    reference_of,
)
from app.modules.evaluasi.kpi_definitions import EvaluationConfig
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import Params
from app.modules.simulasi.sim_actions import ResponseAction, apply_actions
from app.modules.simulasi.sim_calendar import CapacityCalendar
from app.modules.simulasi.sim_kpi import ProjectOutcome, member_loads, project_outcomes
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
    kpis: SideKpis


@dataclass
class EngineOutput:
    start_date: date
    # Hari kerja terakhir yang dibandingkan (yang lebih akhir dari kedua sisi).
    end_date: date
    baseline: SideRun
    scenario: SideRun
    targets: Targets
    config: EvaluationConfig
    notes: list[str]
    replication: ReplicationSummary | None
    duration_ms: int


@dataclass
class PreparedBaseline:
    """Baseline yang sudah disimulasikan, dipakai ulang untuk banyak skenario (DSS, SCRUM-65).

    KPI baseline bergantung pada hari kerja terakhir yang dibandingkan (`until`), jadi sisinya
    dihitung sekali per `until` lewat `side`.
    """

    world: SimWorld
    reference: Reference
    calendar: CapacityCalendar
    schedule: ScheduleResult
    config: EvaluationConfig
    _sides: dict[date, SideRun] = field(default_factory=dict)

    @property
    def start_date(self) -> date:
        return self.world.start_date

    def side(self, until: date) -> SideRun:
        if until not in self._sides:
            self._sides[until] = _side(
                self.world, self.calendar, self.schedule, until, self.reference, self.config
            )
        return self._sides[until]


@dataclass
class ScenarioRun:
    """Skenario yang sudah dijadwalkan, sebelum KPI-nya dihitung."""

    world: SimWorld
    calendar: CapacityCalendar
    schedule: ScheduleResult
    targets: Targets
    notes: list[str]


def prepare_baseline(
    snapshot: BaselineSnapshotData,
    *,
    start_date: date | None = None,
    config: EvaluationConfig | None = None,
) -> PreparedBaseline:
    """Langkah 1, 3, dan 4-5 untuk baseline: dunia, kalender kapasitas, dan jadwalnya."""
    world = build_world(snapshot, start_date)
    calendar = CapacityCalendar(world)
    return PreparedBaseline(
        world=world,
        reference=reference_of(snapshot, world),
        calendar=calendar,
        schedule=simulate(world, calendar),
        config=config or EvaluationConfig(),
    )


def simulate_scenario(
    base: PreparedBaseline,
    situation: Situation | str,
    params: Params,
    actions: Sequence[ResponseAction] = (),
) -> ScenarioRun:
    """Langkah 2 sampai 5 untuk skenario pada salinan dunia baseline; baseline tidak berubah."""
    applied = apply_scenario(base.world, situation, params)
    apply_actions(applied, actions)
    calendar = CapacityCalendar(applied.world)
    return ScenarioRun(
        world=applied.world,
        calendar=calendar,
        schedule=simulate(applied.world, calendar),
        targets=applied.targets,
        notes=applied.notes,
    )


def comparison_end(base: PreparedBaseline, *runs: ScenarioRun) -> date:
    """Hari kerja terakhir yang dibandingkan: yang lebih akhir dari semua sisi."""
    return max([base.schedule.end_date, *(run.schedule.end_date for run in runs)])


def scenario_side(base: PreparedBaseline, run: ScenarioRun, until: date) -> SideRun:
    """Langkah 6: beban harian dan KPI skenario sampai `until`."""
    return _side(run.world, run.calendar, run.schedule, until, base.reference, base.config)


def replicate_scenario(
    base: PreparedBaseline, run: ScenarioRun, runs: int, seed: int = DEFAULT_SEED
) -> ReplicationSummary:
    return run_replications(
        base.world,
        run.world,
        runs=runs,
        seed=seed,
        baseline_calendar=base.calendar,
        scenario_calendar=run.calendar,
        reference=base.reference,
        config=base.config,
    )


def run_engine(
    snapshot: BaselineSnapshotData,
    situation: Situation | str,
    params: Params,
    *,
    start_date: date | None = None,
    runs: int | None = None,
    seed: int = DEFAULT_SEED,
    config: EvaluationConfig | None = None,
    actions: Sequence[ResponseAction] = (),
) -> EngineOutput:
    """Simulasikan baseline dan skenario; `runs` mengaktifkan replikasi Monte Carlo.

    `config` adalah parameter rumus KPI (FR-04.11); kosong berarti parameter bawaan.
    `actions` adalah respons kandidat DSS (`sim_actions`), diterapkan pada salinan skenario
    setelah aturan situasinya; `ResponseActionError` bila respons tidak dapat diterapkan.
    """
    began = time.perf_counter()
    base = prepare_baseline(snapshot, start_date=start_date, config=config)
    run = simulate_scenario(base, situation, params, actions)
    until = comparison_end(base, run)
    return EngineOutput(
        start_date=base.start_date,
        end_date=until,
        baseline=base.side(until),
        scenario=scenario_side(base, run, until),
        targets=run.targets,
        config=base.config,
        notes=base.world.notes + run.notes,
        replication=replicate_scenario(base, run, runs, seed) if runs else None,
        duration_ms=round((time.perf_counter() - began) * 1000),
    )


def _side(
    world: SimWorld,
    calendar: CapacityCalendar,
    schedule: ScheduleResult,
    until: date,
    reference: Reference,
    config: EvaluationConfig,
) -> SideRun:
    loads = member_loads(world, schedule, calendar, until)
    output = SimulationOutput(world, schedule, loads, calendar, until)
    return SideRun(
        world=world,
        calendar=calendar,
        schedule=schedule,
        loads=loads,
        projects=project_outcomes(world, schedule),
        kpis=calculate(output, reference, config),
    )
