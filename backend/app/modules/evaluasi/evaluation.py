"""Evaluasi KPI satu simulasi: baseline dan skenario, selisih, dan status (SCRUM-32).

Tanpa basis data. `evaluate` menjalankan ulang mesin simulasi modul Simulasi (`run_engine`)
dengan masukan yang sama dengan simulasi tersimpan (snapshot, situasi, parameter, tanggal
mulai, jumlah replikasi, dan benih) dan konfigurasi KPI yang diminta. Mesin itu deterministik
untuk benih yang sama dan menghitung KPI-nya dengan `kpi_calculator` (juga di setiap replikasi,
lewat `kpi_replication`), jadi KPI evaluasi dengan konfigurasi bawaan sama dengan KPI di hasil
simulasi. KPI dibandingkan dengan `kpi_comparator` (`kpi_items`, dipakai juga oleh hasil
simulasi). Tanpa replikasi nilai utama adalah hasil deterministik; dengan replikasi nilai utama
adalah median.
"""

import time
from dataclasses import dataclass
from datetime import date

from app.modules.evaluasi.kpi_calculator import SideKpis
from app.modules.evaluasi.kpi_comparator import compare
from app.modules.evaluasi.kpi_definitions import (
    KPI_DEFINITIONS,
    ON_TIME_PROBABILITY,
    EvaluationConfig,
    KpiDefinition,
    KpiStatus,
)
from app.modules.evaluasi.kpi_replication import ReplicatedKpis
from app.modules.evaluasi.schemas import (
    EarGroup,
    KpiDetails,
    KpiEvaluation,
    KpiEvaluationItem,
    KpiPoint,
    KpiRange,
    KpiReplicationStats,
    MemberKpi,
    MemberWeekKpi,
    ProjectKpi,
    ReplicationInfo,
    StatusCount,
    TaskCascade,
    TimeUse,
    WeekBalance,
    WeekCounts,
)
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import Params
from app.modules.simulasi.sim_calendar import EPS
from app.modules.simulasi.sim_engine import run_engine
from app.modules.simulasi.sim_replication import DEFAULT_SEED
from app.modules.simulasi.sim_stats import Range as RangeStats
from app.modules.simulasi.sim_world import SimWorld
from app.modules.simulasi.snapshot_schemas import BaselineSnapshotData


@dataclass
class EvaluationOutput:
    start_date: date
    end_date: date
    baseline_world: SimWorld
    scenario_world: SimWorld
    baseline: SideKpis
    scenario: SideKpis
    replication: ReplicatedKpis | None
    duration_ms: int


def evaluate(
    snapshot: BaselineSnapshotData,
    situation: Situation | str,
    params: Params,
    config: EvaluationConfig,
    *,
    start_date: date | None = None,
    runs: int | None = None,
    seed: int = DEFAULT_SEED,
) -> EvaluationOutput:
    began = time.perf_counter()
    out = run_engine(
        snapshot, situation, params, start_date=start_date, runs=runs, seed=seed, config=config
    )
    return EvaluationOutput(
        start_date=out.start_date,
        end_date=out.end_date,
        baseline_world=out.baseline.world,
        scenario_world=out.scenario.world,
        baseline=out.baseline.kpis,
        scenario=out.scenario.kpis,
        replication=out.replication.kpis if out.replication is not None else None,
        duration_ms=round((time.perf_counter() - began) * 1000),
    )


# --- hasil -------------------------------------------------------------------------------------


def build_evaluation(
    out: EvaluationOutput,
    config: EvaluationConfig,
    *,
    situation: str,
    snapshot_id: int,
    run_id: int | None = None,
    scenario_id: int | None = None,
) -> KpiEvaluation:
    config = config.resolved()
    kpis = kpi_items(out.baseline, out.scenario, out.replication, config)
    return KpiEvaluation(
        evaluation_id=None,
        run_id=run_id,
        scenario_id=scenario_id,
        situation=situation,
        snapshot_id=snapshot_id,
        start_date=out.start_date,
        end_date=out.end_date,
        replication=(
            ReplicationInfo(runs=out.replication.runs, seed=out.replication.seed)
            if out.replication
            else None
        ),
        config=config,
        summary=StatusCount(
            better=sum(1 for k in kpis if k.status == KpiStatus.BETTER),
            same=sum(1 for k in kpis if k.status == KpiStatus.SAME),
            worse=sum(1 for k in kpis if k.status == KpiStatus.WORSE),
        ),
        kpis=kpis,
        details=details(out, config),
        duration_ms=out.duration_ms,
        created_at=None,
        created_by=None,
    )


def kpi_items(
    baseline: SideKpis,
    scenario: SideKpis,
    replication: ReplicatedKpis | None,
    config: EvaluationConfig,
) -> list[KpiEvaluationItem]:
    """Semua KPI (urutan `KPI_DEFINITIONS`) beserta selisih dan statusnya."""
    return [
        kpi_item(definition, baseline, scenario, replication, config)
        for definition in KPI_DEFINITIONS
    ]


def kpi_item(
    definition: KpiDefinition,
    baseline: SideKpis,
    scenario: SideKpis,
    replication: ReplicatedKpis | None,
    config: EvaluationConfig,
) -> KpiEvaluationItem:
    key = definition.key
    tolerance = config.tolerance(key)
    b = baseline.values[key]
    s = scenario.values[key]
    delta, status = compare(definition, b, s, tolerance)
    deterministic = KpiPoint(baseline=b, scenario=s, delta=delta, status=status)

    basis = "deterministic"
    primary = deterministic
    stats = None
    rep = replication
    if rep is not None:
        basis = "median"
        if key == ON_TIME_PROBABILITY:
            # Proporsi replikasi, bukan median; tidak punya rentang.
            mb, ms, md = rep.on_time["baseline"], rep.on_time["scenario"], None
        else:
            ranges = rep.ranges[key]
            mb, ms, md = (ranges[part].median for part in ("baseline", "scenario", "delta"))
        median_delta, median_status = compare(definition, mb, ms, tolerance, md)
        primary = KpiPoint(baseline=mb, scenario=ms, delta=median_delta, status=median_status)
        stats = KpiReplicationStats(
            runs=rep.runs,
            baseline=_range(rep.ranges[key]["baseline"]),
            scenario=_range(rep.ranges[key]["scenario"]),
            delta=_range(rep.ranges[key]["delta"]),
        )
    return KpiEvaluationItem(
        key=key,
        fr=definition.fr,
        label=definition.label,
        unit=definition.unit,
        direction=definition.direction,
        target=definition.target,
        tolerance=tolerance,
        definition=definition.definition,
        formula=definition.formula,
        basis=basis,
        baseline=primary.baseline,
        scenario=primary.scenario,
        delta=primary.delta,
        status=primary.status,
        deterministic=deterministic,
        replication=stats,
    )


def _range(stats: RangeStats | None) -> KpiRange | None:
    if stats is None:
        return None
    return KpiRange(
        median=stats.median,
        p10=stats.p10,
        p90=stats.p90,
        min=stats.min,
        max=stats.max,
        runs=stats.runs,
        missing=stats.missing,
    )


def details(out: EvaluationOutput, config: EvaluationConfig) -> KpiDetails:
    base, scen = out.baseline, out.scenario
    return KpiDetails(
        projects=_projects(out),
        members=_members(out, config),
        balance=[
            WeekBalance(
                week_start=week,
                baseline_wbi=_round(base.wbi_weeks.get(week)),
                scenario_wbi=_round(scen.wbi_weeks.get(week)),
            )
            for week in sorted(base.wbi_weeks.keys() | scen.wbi_weeks.keys())
        ],
        cascade=_cascade(out),
        ear_by_complexity=_groups(base.ear_by_complexity, scen.ear_by_complexity),
        ear_by_role=_groups(base.ear_by_role, scen.ear_by_role),
        baseline_time=_time(base),
        scenario_time=_time(scen),
    )


def _projects(out: EvaluationOutput) -> list[ProjectKpi]:
    base, scen = out.baseline, out.scenario
    rep = out.replication
    rows: list[ProjectKpi] = []
    for project_id in sorted(base.project_sv.keys() | scen.project_sv.keys()):
        project = out.scenario_world.projects.get(project_id) or out.baseline_world.projects.get(
            project_id
        )
        b, s = base.project_sv.get(project_id), scen.project_sv.get(project_id)
        if rep is not None:
            pb = rep.project_on_time["baseline"].get(project_id)
            ps = rep.project_on_time["scenario"].get(project_id)
        else:
            pb = _on_time(b) if project_id in base.project_sv else None
            ps = _on_time(s) if project_id in scen.project_sv else None
        rows.append(
            ProjectKpi(
                project_id=project_id,
                project_name=project.name if project else project_id,
                target_launch_date=project.target_launch_date if project else None,
                baseline_sv=b,
                scenario_sv=s,
                sv_delta=s - b if b is not None and s is not None else None,
                baseline_on_time_probability=pb,
                scenario_on_time_probability=ps,
            )
        )
    return rows


def _on_time(sv: int | None) -> float:
    return 1.0 if sv is not None and sv <= 0 else 0.0


def _members(out: EvaluationOutput, config: EvaluationConfig) -> list[MemberKpi]:
    base, scen = out.baseline, out.scenario
    threshold = config.overload_threshold
    rows: list[MemberKpi] = []
    for resource_id in sorted(base.member_weeks.keys() | scen.member_weeks.keys()):
        resource = out.scenario_world.resources.get(
            resource_id
        ) or out.baseline_world.resources.get(resource_id)
        bw = base.member_weeks.get(resource_id, {})
        sw = scen.member_weeks.get(resource_id, {})

        def counts(load) -> WeekCounts | None:
            if load is None:
                return None
            return WeekCounts(
                tasks=load.tasks,
                operational=load.operational,
                tickets=load.tickets,
                rli=load.rli,
                overloaded=load.rli > threshold + EPS,
            )

        weeks = [
            MemberWeekKpi(
                week_start=week, baseline=counts(bw.get(week)), scenario=counts(sw.get(week))
            )
            for week in sorted(bw.keys() | sw.keys())
        ]
        rows.append(
            MemberKpi(
                resource_id=resource_id,
                resource_name=resource.name,
                primary_role_id=resource.primary_role_id,
                baseline_peak_rli=max((w.rli for w in bw.values()), default=None),
                scenario_peak_rli=max((w.rli for w in sw.values()), default=None),
                baseline_overloaded_weeks=sum(1 for w in bw.values() if w.rli > threshold + EPS),
                scenario_overloaded_weeks=sum(1 for w in sw.values() if w.rli > threshold + EPS),
                baseline_soi=base.soi.get(resource_id, 0.0),
                scenario_soi=scen.soi.get(resource_id, 0.0),
                weeks=weeks,
            )
        )
    return rows


def _cascade(out: EvaluationOutput) -> list[TaskCascade]:
    base, scen = out.baseline, out.scenario
    rows: list[TaskCascade] = []
    for task_id in base.cdi.keys() | scen.cdi.keys():
        task = out.scenario_world.tasks.get(task_id) or out.baseline_world.tasks[task_id]
        rows.append(
            TaskCascade(
                task_id=task_id,
                project_id=task.project_id,
                baseline_delay=base.task_delay.get(task_id),
                scenario_delay=scen.task_delay.get(task_id),
                baseline_cdi=round(base.cdi.get(task_id, 0.0), 6),
                scenario_cdi=round(scen.cdi.get(task_id, 0.0), 6),
            )
        )
    rows = [r for r in rows if r.baseline_cdi > EPS or r.scenario_cdi > EPS]
    return sorted(rows, key=lambda r: (-max(r.baseline_cdi, r.scenario_cdi), r.task_id))


def _groups(baseline: dict[str, float], scenario: dict[str, float]) -> list[EarGroup]:
    return [
        EarGroup(group=group, baseline=baseline.get(group), scenario=scenario.get(group))
        for group in sorted(baseline.keys() | scenario.keys())
    ]


def _time(side: SideKpis) -> TimeUse:
    return TimeUse(
        working_days=side.working_days,
        blocked_days=side.blocked_days,
        reallocated_days=side.reallocated_days,
    )


def _round(value: float | None) -> float | None:
    return round(value, 6) if value is not None else None
