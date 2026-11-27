"""Menyusun hasil mesin simulasi menjadi isi layar Simulation result (UC-03, UC-04).

Tanpa basis data: masukannya `EngineOutput`, keluarannya `SimulationResult` yang juga disimpan
apa adanya (JSONB) untuk "run and save".

- Task "affected": disentuh langsung parameter skenario, atau status, mulai, maupun selesainya
  berbeda dari baseline. Pergantian pengerjanya saja dilaporkan terpisah (`owner_changed`),
  karena task di pool diambil anggota yang kapasitasnya paling longgar hari itu dan dapat
  berpindah walaupun jadwalnya tetap.
- Proyek "affected": disentuh langsung, punya task affected, atau proyeksi selesai, target,
  prioritas, maupun statusnya berubah.
- Anggota "affected": disentuh langsung, atau beban maupun task hariannya berubah.

KPI dihitung dan dibandingkan dengan rumus modul Evaluasi (FR-04.1 s.d. FR-04.10, `kpi_items`):
Schedule Variance proyek, Resource Load Index mingguan, Overload Flag, dan SOI anggota di layar
ini memakai nilai yang sama dengan KPI itu.
"""

from datetime import date, timedelta
from typing import Any

from app.modules.evaluasi.evaluation import kpi_items
from app.modules.evaluasi.kpi_definitions import KpiStatus
from app.modules.evaluasi.schemas import StatusCount
from app.modules.simulasi.scenario_schemas import ScenarioSummary
from app.modules.simulasi.sim_calendar import EPS
from app.modules.simulasi.sim_engine import EngineOutput, SideRun
from app.modules.simulasi.sim_kpi import (
    CAPACITY_LIMIT,
    ProjectOutcome,
    signed_working_days,
    weekly_loads,
)
from app.modules.simulasi.sim_scheduler import MemberDay, TaskOutcome
from app.modules.simulasi.sim_stats import Range as RangeStats
from app.modules.simulasi.simulation_schemas import (
    MemberDayLoad,
    MemberPeriod,
    MemberResult,
    PeriodReplication,
    ProjectReplication,
    ProjectResult,
    ProjectSide,
    Range,
    ReplicationInfo,
    ResultSummary,
    RunMode,
    SimulationResult,
    SimulationWarning,
    TaskReplication,
    TaskResult,
    TaskSchedule,
)
from app.modules.simulasi.snapshot_schemas import DataSource

WEEK = timedelta(days=6)


def build_result(
    out: EngineOutput,
    *,
    scenario: ScenarioSummary,
    parameters: dict[str, Any],
    mode: RunMode,
    snapshot_id: int,
    data_source: DataSource,
) -> SimulationResult:
    holidays = out.baseline.world.holidays
    tasks = _tasks(out, holidays)
    affected_tasks = {t.task_id for t in tasks if t.affected}
    projects = _projects(out, tasks, holidays)
    members = _members(out)
    kpis = kpi_items(
        out.baseline.kpis,
        out.scenario.kpis,
        out.replication.kpis if out.replication is not None else None,
        out.config,
    )
    warnings = [
        SimulationWarning(
            run=run, code=w.code, task_id=w.task_id, project_id=w.project_id, message=w.message
        )
        for run, side in (("baseline", out.baseline), ("scenario", out.scenario))
        for w in side.schedule.warnings
    ]
    return SimulationResult(
        run_id=None,
        mode=mode,
        saved=False,
        scenario=scenario,
        parameters=parameters,
        snapshot_id=snapshot_id,
        data_source=data_source,
        start_date=out.start_date,
        end_date=out.end_date,
        replication=(
            ReplicationInfo(runs=out.replication.runs, seed=out.replication.seed)
            if out.replication
            else None
        ),
        duration_ms=out.duration_ms,
        created_at=None,
        created_by=None,
        summary=ResultSummary(
            affected_projects=sum(1 for p in projects if p.affected),
            affected_tasks=len(affected_tasks),
            affected_members=sum(1 for m in members if m.affected),
            unscheduled_tasks=sum(
                1 for t in tasks if t.scenario is not None and t.scenario.state == "unscheduled"
            ),
            overloaded_members=sum(1 for m in members if m.scenario_overloaded_weeks > 0),
        ),
        kpi_config=out.config.resolved(),
        kpi_summary=StatusCount(
            better=sum(1 for k in kpis if k.status == KpiStatus.BETTER),
            same=sum(1 for k in kpis if k.status == KpiStatus.SAME),
            worse=sum(1 for k in kpis if k.status == KpiStatus.WORSE),
        ),
        kpis=kpis,
        projects=projects,
        tasks=tasks,
        members=members,
        warnings=warnings,
        notes=out.notes,
    )


# --- task --------------------------------------------------------------------------------------


def _tasks(out: EngineOutput, holidays: frozenset[date]) -> list[TaskResult]:
    base, scen = out.baseline, out.scenario
    projects = {
        pid
        for side in (base, scen)
        for pid, project in side.world.projects.items()
        if project.active
    }
    task_ids = [
        task_id
        for task_id in set(base.world.tasks) | set(scen.world.tasks)
        if (scen.world.tasks.get(task_id) or base.world.tasks[task_id]).project_id in projects
    ]
    results: list[TaskResult] = []
    for task_id in task_ids:
        task = scen.world.tasks.get(task_id) or base.world.tasks[task_id]
        b = base.schedule.tasks.get(task_id)
        s = scen.schedule.tasks.get(task_id)
        targeted = task_id in out.targets.tasks
        changed = _schedule_key(b) != _schedule_key(s)
        owner_changed = b is not None and s is not None and b.owner != s.owner
        replication = None
        if out.replication is not None and task_id in out.replication.tasks:
            stats = out.replication.tasks[task_id]
            replication = TaskReplication(
                baseline_start=_range(stats.baseline_start),
                baseline_finish=_range(stats.baseline_finish),
                scenario_start=_range(stats.scenario_start),
                scenario_finish=_range(stats.scenario_finish),
                baseline_unscheduled_runs=stats.baseline_unscheduled_runs,
                scenario_unscheduled_runs=stats.scenario_unscheduled_runs,
            )
        results.append(
            TaskResult(
                task_id=task_id,
                project_id=task.project_id,
                task_name=task.name,
                required_role_id=task.required_role_id,
                complexity_id=task.complexity_id,
                added=task.added,
                targeted=targeted,
                affected=targeted or changed,
                owner_changed=owner_changed,
                baseline=_schedule(b),
                scenario=_schedule(s),
                start_shift=_shift(b.start if b else None, s.start if s else None, holidays),
                finish_shift=_shift(b.finish if b else None, s.finish if s else None, holidays),
                replication=replication,
            )
        )
    far = date.max

    def order(result: TaskResult) -> tuple:
        side = result.scenario or result.baseline
        return (result.project_id, (side.start if side else None) or far, result.task_id)

    return sorted(results, key=order)


def _schedule_key(outcome: TaskOutcome | None) -> tuple | None:
    if outcome is None:
        return None
    return (outcome.state, outcome.start, outcome.finish)


def _schedule(outcome: TaskOutcome | None) -> TaskSchedule | None:
    if outcome is None:
        return None
    return TaskSchedule(
        state=outcome.state,
        start=outcome.start,
        finish=outcome.finish,
        owner=outcome.owner,
        effort=round(outcome.effort, 4) if outcome.effort is not None else None,
        dependency_ready=outcome.dependency_ready,
        reason=outcome.reason,
    )


def _shift(before: date | None, after: date | None, holidays: frozenset[date]) -> int | None:
    if before is None or after is None:
        return None
    return signed_working_days(before, after, holidays)


# --- proyek ------------------------------------------------------------------------------------


def _projects(
    out: EngineOutput, tasks: list[TaskResult], holidays: frozenset[date]
) -> list[ProjectResult]:
    base, scen = out.baseline, out.scenario
    by_project: dict[str, list[TaskResult]] = {}
    for task in tasks:
        by_project.setdefault(task.project_id, []).append(task)
    results: list[ProjectResult] = []
    for project_id in sorted(by_project.keys() | out.targets.projects):
        project = scen.world.projects.get(project_id) or base.world.projects.get(project_id)
        if project is None:
            continue
        b = _project_side(base, project_id)
        s = _project_side(scen, project_id)
        if b is None or s is None:
            continue
        project_tasks = by_project.get(project_id, [])
        affected_tasks = sum(1 for t in project_tasks if t.affected)
        targeted = project_id in out.targets.projects
        replication = None
        if out.replication is not None and project_id in out.replication.projects:
            stats = out.replication.projects[project_id]
            replication = ProjectReplication(
                baseline_completion=_range(stats.baseline_completion),
                scenario_completion=_range(stats.scenario_completion),
                baseline_sv=_range(stats.baseline_sv),
                scenario_sv=_range(stats.scenario_sv),
                baseline_on_time_probability=stats.baseline_on_time_probability,
                scenario_on_time_probability=stats.scenario_on_time_probability,
            )
        results.append(
            ProjectResult(
                project_id=project_id,
                project_name=project.name,
                targeted=targeted,
                affected=targeted or affected_tasks > 0 or b != s,
                baseline=b,
                scenario=s,
                completion_shift=_shift(b.completion, s.completion, holidays),
                task_count=len(project_tasks),
                affected_task_count=affected_tasks,
                replication=replication,
            )
        )
    return sorted(results, key=lambda p: (p.scenario.priority_rank, p.project_id))


def _project_side(side: SideRun, project_id: str) -> ProjectSide | None:
    project = side.world.projects.get(project_id)
    if project is None:
        return None
    outcome: ProjectOutcome | None = side.projects.get(project_id)
    if outcome is None:
        return ProjectSide(
            active=project.active,
            priority_rank=project.priority_rank,
            target_launch_date=project.target_launch_date,
            completion=None,
            schedule_variance=None,
            delay_days=None,
            on_target=None,
            unscheduled_tasks=0,
        )
    return ProjectSide(
        active=True,
        priority_rank=outcome.priority_rank,
        target_launch_date=outcome.target,
        completion=outcome.completion,
        schedule_variance=side.kpis.project_sv.get(project_id),
        delay_days=outcome.delay_days,
        on_target=outcome.on_target,
        unscheduled_tasks=outcome.unscheduled,
    )


# --- anggota -----------------------------------------------------------------------------------


def _members(out: EngineOutput) -> list[MemberResult]:
    base, scen = out.baseline, out.scenario
    threshold = out.config.overload_threshold

    def overloaded(rli: float | None) -> bool:
        return rli is not None and rli > threshold + EPS

    results: list[MemberResult] = []
    for resource_id in sorted(set(base.world.resources) | set(scen.world.resources)):
        resource = scen.world.resources.get(resource_id) or base.world.resources[resource_id]
        b_days = {d.day: d for d in base.loads.get(resource_id, [])}
        s_days = {d.day: d for d in scen.loads.get(resource_id, [])}
        days = [
            _day_load(day, b_days.get(day), s_days.get(day))
            for day in sorted(b_days.keys() | s_days.keys())
        ]
        # Utilisasi kapasitas harian per minggu, dan RLI mingguan KPI evaluasi (FR-04.3).
        b_weeks = weekly_loads(list(b_days.values()))
        s_weeks = weekly_loads(list(s_days.values()))
        b_rli = {w: load.rli for w, load in base.kpis.member_weeks.get(resource_id, {}).items()}
        s_rli = {w: load.rli for w, load in scen.kpis.member_weeks.get(resource_id, {}).items()}
        replication_weeks = (
            out.replication.members.get(resource_id, {}) if out.replication is not None else {}
        )
        periods = []
        for start in sorted(b_weeks.keys() | s_weeks.keys() | b_rli.keys() | s_rli.keys()):
            bw, sw = b_weeks.get(start), s_weeks.get(start)
            stats = replication_weeks.get(start)
            periods.append(
                MemberPeriod(
                    period_start=start,
                    period_end=start + WEEK,
                    baseline_rli=b_rli.get(start),
                    scenario_rli=s_rli.get(start),
                    baseline_overloaded=overloaded(b_rli.get(start)),
                    overloaded=overloaded(s_rli.get(start)),
                    baseline_load=bw.average if bw else None,
                    scenario_load=sw.average if sw else None,
                    baseline_peak=bw.peak if bw else None,
                    scenario_peak=sw.peak if sw else None,
                    baseline_over_capacity_days=bw.over_capacity_days if bw else 0,
                    scenario_over_capacity_days=sw.over_capacity_days if sw else 0,
                    replication=PeriodReplication(
                        baseline=_range(stats.baseline),
                        scenario=_range(stats.scenario),
                        baseline_overloaded_runs=stats.baseline_overloaded_runs,
                        scenario_overloaded_runs=stats.scenario_overloaded_runs,
                    )
                    if stats is not None
                    else None,
                )
            )
        changed = any(
            _differs(b_days.get(day), s_days.get(day)) for day in b_days.keys() | s_days.keys()
        )
        b_loads = [d.load for d in b_days.values() if d.load is not None]
        s_loads = [d.load for d in s_days.values() if d.load is not None]
        results.append(
            MemberResult(
                resource_id=resource_id,
                resource_name=resource.name,
                primary_role_id=resource.primary_role_id,
                added=resource.added,
                targeted=resource_id in out.targets.resources,
                affected=resource_id in out.targets.resources or changed,
                baseline_peak_rli=max(b_rli.values(), default=None),
                scenario_peak_rli=max(s_rli.values(), default=None),
                baseline_overloaded_weeks=sum(1 for v in b_rli.values() if overloaded(v)),
                scenario_overloaded_weeks=sum(1 for v in s_rli.values() if overloaded(v)),
                baseline_soi=base.kpis.soi.get(resource_id, 0.0),
                scenario_soi=scen.kpis.soi.get(resource_id, 0.0),
                baseline_peak_load=_round(max(b_loads)) if b_loads else None,
                scenario_peak_load=_round(max(s_loads)) if s_loads else None,
                baseline_over_capacity_days=_over_capacity(b_loads),
                scenario_over_capacity_days=_over_capacity(s_loads),
                periods=periods,
                days=days,
            )
        )
    return results


def _day_load(day: date, b: MemberDay | None, s: MemberDay | None) -> MemberDayLoad:
    return MemberDayLoad(
        day=day,
        baseline_load=_round(b.load) if b else None,
        scenario_load=_round(s.load) if s else None,
        scenario_capacity=_round(s.capacity) if s else 0.0,
        scenario_absence=s.absence if s else None,
        scenario_tasks=list(s.tasks) if s else [],
        baseline_over_capacity=bool(b and b.overloaded),
        over_capacity=bool(s and s.overloaded),
    )


def _differs(b: MemberDay | None, s: MemberDay | None) -> bool:
    if b is None or s is None:
        return (b is None) != (s is None)
    if (b.load is None) != (s.load is None):
        return True
    if b.load is not None and abs(b.load - s.load) > 1e-6:
        return True
    return b.tasks != s.tasks


def _over_capacity(loads: list[float]) -> int:
    return sum(1 for load in loads if load > CAPACITY_LIMIT + EPS)


def _round(value: float | None) -> float | None:
    return round(value, 4) if value is not None else None


def _range(stats: RangeStats) -> Range:
    return Range(
        median=stats.median,
        p10=stats.p10,
        p90=stats.p90,
        min=stats.min,
        max=stats.max,
        runs=stats.runs,
        missing=stats.missing,
    )
