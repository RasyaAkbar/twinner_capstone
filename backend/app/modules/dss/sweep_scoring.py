"""Penilaian dan peringkat kandidat sweep DSS pada empat kriteria keputusan (SCRUM-66).

Setiap kandidat yang berhasil disimulasikan (`SweepRun.done`, termasuk pilihan pengguna)
dinilai pada empat kriteria §IV.2.4. Setiap kriteria bernilai 0..1, lebih besar lebih baik:

1. `role_match`: peran utama anggota sama dengan peran yang dibutuhkan = 1, peran sekunder
   (cross-role capable) = 0.5, lainnya 0. Peran yang dibutuhkan: peran task yang dialihkan,
   peran anggota baru (mentor), atau peran task terdampak bug (perbaikan). Untuk mentor nilainya
   dikali faktor senioritas (`SENIORITY_FACTOR`).
2. `available_capacity`: rata-rata bagian hari kerja anggota yang masih kosong, max(0, 1 - beban
   harian), selama periode dibutuhkannya: periode onboarding (mentor), periode perbaikan
   (bug), atau sejak pengalihan sampai task yang dialihkan selesai.
3. `current_work`: dampak pada pekerjaan anggota itu sendiri: perubahan jumlah minggu
   berbeban berlebih, perubahan RLI mingguan tertingginya, dan keterlambatan task miliknya
   (hari kerja). Ketiganya dinormalisasi min-max di antara kandidat (dampak terkecil = 1) lalu
   dirata-rata.
4. `kpi_effect`: perbaikan setiap KPI terhadap pilihan pengguna (`vs_current`), memakai arah
   dan toleransi KPI itu (perubahan di dalam toleransi = 0), dinormalisasi min-max per KPI di
   antara kandidat, lalu dirata-rata atas KPI yang nilainya berbeda antar kandidat. Nilai
   kosong mengikuti statusnya: better = 1, worse = 0.

Keadaan acuan kriteria 2 dan 3 adalah keadaan tanpa keterlibatan anggota itu: skenario pengguna
untuk lever pengalihan, dan baseline untuk lever parameter (mentor, perbaikan bug). Dengan
begitu anggota pilihan pengguna dinilai dengan cara yang sama dengan anggota lain.

Kandidat tanpa anggota (tanpa mentor, tanpa pengalihan, Priority Change) hanya dinilai pada
kriteria 4; bobot kriteria lain tidak berlaku. Total = rata-rata berbobot kriteria yang berlaku
(`ScoringWeights`, bawaan sama rata 0.25 sampai disepakati tim). Bila total sama, pilihan
pengguna didahulukan, sehingga DSS hanya mengusulkan perubahan yang benar-benar lebih baik.

- `no_action_needed`: skenario pengguna tidak memperburuk Schedule Variance, peluang tepat
  waktu, Overload Flag, SOI, maupun Cascade Delay Impact dibandingkan baseline. Kandidat tetap
  dinilai dan ditampilkan.
- `no_suitable_candidate`: tidak ada kandidat yang totalnya lebih besar dari pilihan pengguna.

Modul ini tidak menyentuh basis data.
"""

import bisect
import math
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from app.modules.dss.sweep_levers import LeverKind
from app.modules.dss.sweep_runner import TOP_REPLICATED, CandidateRun, SweepRun
from app.modules.evaluasi.kpi_comparator import KpiOutcome, gain_of
from app.modules.evaluasi.kpi_definitions import (
    CASCADE_DELAY_IMPACT,
    KPI_DEFINITIONS,
    ON_TIME_PROBABILITY,
    OVERLOAD_FLAGS,
    SCHEDULE_VARIANCE,
    SUSTAINED_OVERLOAD,
    KpiStatus,
)
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.sim_calendar import EPS
from app.modules.simulasi.sim_engine import SideRun, scenario_side
from app.modules.simulasi.sim_kpi import signed_working_days
from app.modules.simulasi.sim_scheduler import TaskState
from app.modules.simulasi.sim_world import SimResource, onboarding_working_days

# Faktor senioritas mentor pada kecocokan peran (D-02 seniority).
SENIORITY_FACTOR = {"junior": 0.5, "mid": 0.75, "senior": 1.0}
# KPI yang menandai beban berlebih, keterlambatan, atau target rilis terlewat (UC-07).
PROBLEM_KPIS = (
    SCHEDULE_VARIANCE,
    ON_TIME_PROBABILITY,
    OVERLOAD_FLAGS,
    SUSTAINED_OVERLOAD,
    CASCADE_DELAY_IMPACT,
)
WORK_COMPONENTS = ("overloaded_weeks", "peak_rli", "own_delay_days")


class Criterion(StrEnum):
    ROLE_MATCH = "role_match"
    AVAILABLE_CAPACITY = "available_capacity"
    CURRENT_WORK = "current_work"
    KPI_EFFECT = "kpi_effect"


@dataclass(frozen=True)
class ScoringWeights:
    """Bobot kriteria; hanya perbandingannya yang berarti (dinormalisasi atas kriteria yang
    berlaku untuk setiap kandidat)."""

    role_match: float = 0.25
    available_capacity: float = 0.25
    current_work: float = 0.25
    kpi_effect: float = 0.25

    def __post_init__(self) -> None:
        values = [self.of(criterion) for criterion in Criterion]
        if any(not math.isfinite(v) or v < 0 for v in values):
            raise ValueError("Bobot kriteria harus angka yang tidak negatif")
        if sum(values) <= 0:
            raise ValueError("Paling sedikit satu bobot kriteria harus lebih dari 0")

    def of(self, criterion: Criterion) -> float:
        return getattr(self, criterion.value)

    def as_json(self) -> dict[str, float]:
        return {criterion.value: self.of(criterion) for criterion in Criterion}


DEFAULT_WEIGHTS = ScoringWeights()


@dataclass(frozen=True)
class CriterionValue:
    """Nilai 0..1 satu kriteria (None = tidak berlaku) beserta fakta mentahnya untuk alasan."""

    score: float | None
    facts: dict[str, Any] = field(default_factory=dict)


NOT_APPLICABLE = CriterionValue(None)


@dataclass
class ScoredCandidate:
    result: CandidateRun
    criteria: dict[Criterion, CriterionValue]
    score: float
    rank: int = 0

    @property
    def is_current(self) -> bool:
        return self.result.candidate.is_current


@dataclass
class Ranking:
    # Kandidat yang berhasil disimulasikan, terbaik lebih dulu.
    candidates: list[ScoredCandidate]
    weights: ScoringWeights
    no_action_needed: bool
    no_suitable_candidate: bool

    @property
    def current(self) -> ScoredCandidate:
        return next(c for c in self.candidates if c.is_current)

    def top(self, n: int = TOP_REPLICATED) -> list[CandidateRun]:
        """`n` kandidat teratas selain pilihan pengguna, misalnya untuk replikasi."""
        return [c.result for c in self.candidates if not c.is_current][:n]


def rank_sweep(sweep: SweepRun, weights: ScoringWeights = DEFAULT_WEIGHTS) -> Ranking:
    """Nilai dan urutkan kandidat sweep; lihat docstring modul."""
    context = _Context(sweep)
    done = sweep.done
    criteria: list[dict[Criterion, CriterionValue]] = []
    work: dict[int, dict[str, float]] = {}
    for i, result in enumerate(done):
        values = {
            Criterion.ROLE_MATCH: NOT_APPLICABLE,
            Criterion.AVAILABLE_CAPACITY: NOT_APPLICABLE,
            Criterion.CURRENT_WORK: NOT_APPLICABLE,
        }
        member = context.member(result)
        if member is not None:
            reference = context.reference(result)
            values[Criterion.ROLE_MATCH] = _role_match(context, result, member)
            values[Criterion.AVAILABLE_CAPACITY] = _available_capacity(
                context, result, member.resource_id, reference
            )
            work[i] = _work_effect(context, result, member.resource_id, reference)
        criteria.append(values)
    for i, value in _current_work_scores(work).items():
        criteria[i][Criterion.CURRENT_WORK] = value
    for i, value in enumerate(kpi_effect_scores([r.vs_current for r in done])):
        criteria[i][Criterion.KPI_EFFECT] = value

    scored = [ScoredCandidate(r, c, _total(c, weights)) for r, c in zip(done, criteria)]
    order = sorted(
        range(len(scored)),
        key=lambda i: (
            -scored[i].score,
            -(scored[i].criteria[Criterion.KPI_EFFECT].score or 0.0),
            not scored[i].is_current,
            i,
        ),
    )
    ranked = [scored[i] for i in order]
    for position, candidate in enumerate(ranked, start=1):
        candidate.rank = position
    current = next(c for c in ranked if c.is_current)
    return Ranking(
        candidates=ranked,
        weights=weights,
        no_action_needed=_no_action_needed(sweep.current),
        no_suitable_candidate=not any(
            c.score > current.score + EPS for c in ranked if not c.is_current
        ),
    )


def kpi_effect_scores(comparisons: Sequence[Sequence[Any]]) -> list[CriterionValue]:
    """Kriteria 4 untuk setiap daftar perbandingan KPI terhadap pilihan pengguna.

    Setiap butir perbandingan punya `key`, `baseline` (nilai pilihan pengguna), `scenario`
    (nilai kandidat), `delta`, dan `status`, seperti `KpiEvaluationItem`.
    """
    items = [{item.key: item for item in comparison} for comparison in comparisons]
    parts: list[list[float]] = [[] for _ in items]
    for definition in KPI_DEFINITIONS:
        gains: list[float | None] = []
        statuses: list[KpiStatus] = []
        for by_key in items:
            item = by_key.get(definition.key)
            status = item.status if item is not None else KpiStatus.SAME
            gain = 0.0
            if item is not None and status != KpiStatus.SAME:
                outcome = KpiOutcome(item.baseline, item.scenario, item.delta, status)
                gain = gain_of(definition.key, outcome)
            gains.append(gain)
            statuses.append(status)
        finite = [g for g in gains if g is not None]
        low, high = (min(finite), max(finite)) if finite else (0.0, 0.0)
        spread = high - low > EPS
        if not spread and all(g is not None for g in gains):
            continue  # Semua kandidat sama pada KPI ini.
        for i, (gain, status) in enumerate(zip(gains, statuses)):
            if gain is None:
                parts[i].append(1.0 if status == KpiStatus.BETTER else 0.0)
            else:
                parts[i].append((gain - low) / (high - low) if spread else 1.0)

    values: list[CriterionValue] = []
    for by_key, part in zip(items, parts):
        counts = {status: 0 for status in KpiStatus}
        changes: dict[str, float | None] = {}
        for key, item in by_key.items():
            counts[item.status] += 1
            if item.status != KpiStatus.SAME:
                changes[key] = item.delta
        facts = {
            "better": counts[KpiStatus.BETTER],
            "same": counts[KpiStatus.SAME],
            "worse": counts[KpiStatus.WORSE],
            "changes": changes,
        }
        score = sum(part) / len(part) if part else 1.0
        values.append(CriterionValue(round(score, 6), facts))
    return values


# --- kriteria per anggota ----------------------------------------------------------------------


class _Context:
    def __init__(self, sweep: SweepRun) -> None:
        self.sweep = sweep
        self.base = sweep.baseline
        self.kind = sweep.candidates.lever.kind
        self.situation = sweep.candidates.lever.situation
        assert sweep.current.side is not None
        # Dunia skenario pengguna: berisi anggota dan task skenario (misalnya task CR).
        self.world = sweep.current.side.world
        self.days = self.base.calendar.days
        self.holidays = self.base.world.holidays
        self._current: dict[date, SideRun] = {}

    def member(self, result: CandidateRun) -> SimResource | None:
        """Anggota yang menjadi nilai lever kandidat, bila ada."""
        value = result.candidate.value
        return self.world.resources.get(value) if isinstance(value, str) else None

    def reference(self, result: CandidateRun) -> SideRun:
        """Keadaan tanpa keterlibatan anggota kandidat, sampai hari terakhir kandidat."""
        assert result.until is not None
        if self.kind == LeverKind.PARAMETER:
            return self.base.side(result.until)
        current = self.sweep.current
        assert current.side is not None and current.run is not None
        if result.until == current.until:
            return current.side
        if result.until not in self._current:
            self._current[result.until] = scenario_side(self.base, current.run, result.until)
        return self._current[result.until]

    def required_roles(self, result: CandidateRun) -> set[str]:
        candidate = result.candidate
        tasks = self.world.tasks
        if candidate.actions:
            return {
                tasks[task_id].required_role_id
                for action in candidate.actions
                for task_id in action.task_ids
                if task_id in tasks
            }
        if self.situation == Situation.NEW_MEMBER:
            return {candidate.parameters["role_id"]}
        if self.situation == Situation.CRITICAL_BUG:
            return {
                tasks[task_id].required_role_id
                for task_id in candidate.parameters.get("affected_task_ids", [])
                if task_id in tasks
            }
        return set()

    def period(self, result: CandidateRun) -> list[date]:
        """Hari kerja anggota kandidat dibutuhkan."""
        candidate = result.candidate
        params = candidate.parameters
        if self.situation == Situation.NEW_MEMBER:
            first = bisect.bisect_left(self.days, date.fromisoformat(params["join_date"]))
            return self.days[first : first + onboarding_working_days(params["onboarding_weeks"])]
        if self.situation == Situation.CRITICAL_BUG:
            return self.days[: math.ceil(params["fixing_days"] - 1e-9)]
        if not candidate.actions:
            return []
        assert result.side is not None and result.until is not None
        start = min(action.from_date for action in candidate.actions)
        schedule = result.side.schedule.tasks
        finishes = [
            schedule[task_id].finish if task_id in schedule else None
            for action in candidate.actions
            for task_id in action.task_ids
        ]
        end = result.until if None in finishes else max(f for f in finishes if f is not None)
        return [day for day in self.days if start <= day <= end]


def _role_match(context: _Context, result: CandidateRun, member: SimResource) -> CriterionValue:
    roles = context.required_roles(result)
    if not roles:
        return NOT_APPLICABLE
    score, match = max(_role_level(member, role) for role in roles)
    facts: dict[str, Any] = {"roles": sorted(roles), "match": match}
    if context.situation == Situation.NEW_MEMBER:
        facts["seniority"] = member.seniority
        score *= SENIORITY_FACTOR.get(member.seniority or "", 1.0)
    return CriterionValue(round(score, 6), facts)


def _role_level(member: SimResource, role: str) -> tuple[float, str]:
    if member.primary_role_id == role:
        return 1.0, "primary"
    if member.cross_role_capable and role in member.secondary_role_ids:
        return 0.5, "secondary"
    return 0.0, "none"


def _available_capacity(
    context: _Context, result: CandidateRun, member: str, reference: SideRun
) -> CriterionValue:
    loads = {day.day: day for day in reference.loads.get(member, [])}
    days = [loads[day] for day in context.period(result) if day in loads]
    if not days:
        return NOT_APPLICABLE
    free = [max(0.0, 1.0 - d.load) if d.load is not None else 0.0 for d in days]
    facts = {
        "available_days": round(sum(free), 2),
        "working_days": len(free),
        "start": days[0].day.isoformat(),
        "end": days[-1].day.isoformat(),
    }
    return CriterionValue(round(sum(free) / len(free), 6), facts)


def _work_effect(
    context: _Context, result: CandidateRun, member: str, reference: SideRun
) -> dict[str, float]:
    """Dampak kandidat pada pekerjaan anggotanya sendiri dibandingkan keadaan acuan."""
    assert result.side is not None
    threshold = context.base.config.overload_threshold
    before = reference.kpis.member_weeks.get(member, {})
    after = result.side.kpis.member_weeks.get(member, {})
    overloaded = sum(1 for w in after.values() if w.rli > threshold + EPS) - sum(
        1 for w in before.values() if w.rli > threshold + EPS
    )
    peak = max((w.rli for w in after.values()), default=0.0) - max(
        (w.rli for w in before.values()), default=0.0
    )
    moved = {task_id for action in result.candidate.actions for task_id in action.task_ids}
    delay = 0
    schedule = result.side.schedule
    for task_id, outcome in reference.schedule.tasks.items():
        if outcome.owner != member or outcome.state != TaskState.SCHEDULED or task_id in moved:
            continue
        mine = schedule.tasks.get(task_id)
        finish = mine.finish if mine is not None and mine.finish is not None else schedule.end_date
        delay += max(0, signed_working_days(outcome.finish, finish, context.holidays))
    return {"overloaded_weeks": overloaded, "peak_rli": round(peak, 6), "own_delay_days": delay}


def _current_work_scores(work: dict[int, dict[str, float]]) -> dict[int, CriterionValue]:
    """Kriteria 3: setiap komponen dinormalisasi min-max (dampak terkecil = 1), dirata-rata."""
    parts: dict[int, list[float]] = {i: [] for i in work}
    for component in WORK_COMPONENTS:
        values = {i: facts[component] for i, facts in work.items()}
        if not values:
            continue
        low, high = min(values.values()), max(values.values())
        for i, value in values.items():
            parts[i].append((high - value) / (high - low) if high - low > EPS else 1.0)
    return {i: CriterionValue(round(sum(parts[i]) / len(parts[i]), 6), work[i]) for i in work}


def _total(criteria: dict[Criterion, CriterionValue], weights: ScoringWeights) -> float:
    applicable = [
        (weights.of(criterion), value.score)
        for criterion, value in criteria.items()
        if value.score is not None
    ]
    total_weight = sum(weight for weight, _ in applicable)
    if total_weight <= 0:
        return 0.0
    return round(sum(weight * score for weight, score in applicable) / total_weight, 6)


def _no_action_needed(current: CandidateRun) -> bool:
    statuses = {item.key: item.status for item in current.vs_baseline}
    return all(statuses.get(key) != KpiStatus.WORSE for key in PROBLEM_KPIS)
