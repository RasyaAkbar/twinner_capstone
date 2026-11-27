"""Menyimulasikan setiap kandidat sweep DSS dan membandingkan KPI-nya (SCRUM-65).

`run_sweep` menerima snapshot skenario, kandidat dari `build_candidates`, dan pengaturan
simulasi tersimpan (tanggal mulai, jumlah replikasi, dan benih):

1. Baseline disiapkan sekali (`sim_engine.prepare_baseline`): dunia, kalender kapasitas, dan
   jadwalnya dipakai ulang untuk setiap kandidat. KPI baseline dihitung sekali per hari terakhir
   pembanding.
2. Pilihan pengguna disimulasikan lebih dulu. Bila gagal, sweep berhenti dengan `SweepError`,
   karena kandidat lain dibandingkan dengannya.
3. Setiap kandidat layak disimulasikan deterministik dengan parameter dan responsnya, lalu
   KPI-nya dibandingkan dengan pembanding modul Evaluasi (`kpi_items`, selisih dan status
   better/same/worse):
   - `vs_baseline`: terhadap baseline sampai hari kerja terakhir keduanya, sama dengan
     `run_engine`. Untuk pilihan pengguna hasilnya sama dengan simulasi tersimpannya.
   - `vs_current`: terhadap pilihan pengguna, kedua sisi sampai hari kerja terakhir dari
     baseline, pilihan pengguna, dan kandidat, supaya keduanya diukur pada jendela yang sama.
4. Kandidat yang gagal (misalnya respons ditolak mesin) ditandai `failed` beserta pesannya dan
   sweep berlanjut. Kandidat yang dikecualikan tidak disimulasikan (`excluded`).
5. `progress(selesai, total)` dipanggil setelah setiap kandidat layak, untuk job latar belakang.

Replikasi Monte Carlo mahal, jadi tidak dijalankan untuk semua kandidat. Setelah kandidat
dinilai (SCRUM-66), `replicate` menjalankannya untuk kandidat teratas (`TOP_REPLICATED`) dengan
`runs` dan `seed` simulasi tersimpan, sehingga rentangnya sebanding dengan pilihan pengguna,
lalu `vs_baseline` kandidat itu memakai median dan rentangnya. Perbandingan dengan pilihan
pengguna tetap deterministik. Simulasi tersimpan tanpa replikasi berarti tidak ada replikasi.

Modul ini tidak menyentuh basis data.
"""

import logging
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from app.modules.dss.sweep_candidates import CandidateSet, SweepCandidate
from app.modules.evaluasi.evaluation import kpi_items
from app.modules.evaluasi.kpi_definitions import EvaluationConfig
from app.modules.evaluasi.schemas import KpiEvaluationItem
from app.modules.simulasi.scenario_params import SITUATIONS
from app.modules.simulasi.sim_engine import (
    PreparedBaseline,
    ScenarioRun,
    SideRun,
    comparison_end,
    prepare_baseline,
    replicate_scenario,
    scenario_side,
    simulate_scenario,
)
from app.modules.simulasi.sim_replication import DEFAULT_SEED, ReplicationSummary
from app.modules.simulasi.sim_rules import Targets
from app.modules.simulasi.snapshot_schemas import BaselineSnapshotData

logger = logging.getLogger(__name__)

# Kandidat teratas setelah penilaian yang mendapat replikasi Monte Carlo.
TOP_REPLICATED = 3

Progress = Callable[[int, int], None]


class CandidateStatus(StrEnum):
    DONE = "done"
    FAILED = "failed"
    EXCLUDED = "excluded"


class SweepError(Exception):
    """Pilihan pengguna tidak dapat disimulasikan, jadi kandidat lain tidak punya pembanding."""


@dataclass
class CandidateRun:
    candidate: SweepCandidate
    status: CandidateStatus
    # Pesan kegagalan, atau alasan dikecualikan.
    error: str | None = None
    # Sisi skenario kandidat sampai `until`, hari kerja terakhir pembanding baseline.
    side: SideRun | None = None
    until: date | None = None
    vs_baseline: list[KpiEvaluationItem] = field(default_factory=list)
    vs_current: list[KpiEvaluationItem] = field(default_factory=list)
    targets: Targets | None = None
    notes: list[str] = field(default_factory=list)
    replication: ReplicationSummary | None = None
    duration_ms: int = 0
    # Jadwal kandidat, dipakai ulang oleh `replicate`.
    run: ScenarioRun | None = None


@dataclass
class SweepRun:
    candidates: CandidateSet
    baseline: PreparedBaseline
    # Satu hasil per kandidat, urutannya sama dengan `candidates.candidates`.
    results: list[CandidateRun]
    runs: int | None
    seed: int
    duration_ms: int

    @property
    def start_date(self) -> date:
        return self.baseline.start_date

    @property
    def current(self) -> CandidateRun:
        return self.results[0]

    @property
    def done(self) -> list[CandidateRun]:
        return [r for r in self.results if r.status == CandidateStatus.DONE]


def run_sweep(
    snapshot: BaselineSnapshotData,
    candidates: CandidateSet,
    *,
    start_date: date | None = None,
    runs: int | None = None,
    seed: int = DEFAULT_SEED,
    config: EvaluationConfig | None = None,
    progress: Progress | None = None,
) -> SweepRun:
    """Simulasikan setiap kandidat layak secara deterministik; lihat docstring modul."""
    began = time.perf_counter()
    base = prepare_baseline(snapshot, start_date=start_date, config=config)
    total = len(candidates.eligible)

    started = time.perf_counter()
    try:
        current_run = _simulate(base, candidates.current)
        reference = _evaluate(base, candidates.current, current_run)
    except Exception as exc:
        raise SweepError(f"Skenario pengguna tidak dapat disimulasikan: {exc}") from exc
    current = _CurrentSides(base, current_run, reference)
    reference.vs_current = kpi_items(reference.side.kpis, reference.side.kpis, None, base.config)
    reference.duration_ms = _since(started)
    results = [reference]
    completed = 1
    if progress is not None:
        progress(completed, total)

    for candidate in candidates.candidates[1:]:
        if not candidate.eligible:
            results.append(
                CandidateRun(candidate, CandidateStatus.EXCLUDED, error=candidate.excluded_reason)
            )
            continue
        started = time.perf_counter()
        try:
            result = _evaluate(base, candidate, _simulate(base, candidate))
            current.compare(result)
        except Exception as exc:
            logger.warning("Kandidat sweep %s gagal", candidate.label, exc_info=True)
            result = CandidateRun(candidate, CandidateStatus.FAILED, error=str(exc) or repr(exc))
        result.duration_ms = _since(started)
        results.append(result)
        completed += 1
        if progress is not None:
            progress(completed, total)

    return SweepRun(
        candidates=candidates,
        baseline=base,
        results=results,
        runs=runs,
        seed=seed,
        duration_ms=_since(began),
    )


def replicate(sweep: SweepRun, results: Iterable[CandidateRun]) -> None:
    """Replikasi Monte Carlo untuk `results` (kandidat teratas setelah penilaian)."""
    if not sweep.runs:
        return
    base = sweep.baseline
    for result in results:
        if result.status != CandidateStatus.DONE or result.replication is not None:
            continue
        assert result.run is not None and result.side is not None and result.until is not None
        result.replication = replicate_scenario(base, result.run, sweep.runs, sweep.seed)
        result.vs_baseline = kpi_items(
            base.side(result.until).kpis, result.side.kpis, result.replication.kpis, base.config
        )


def _simulate(base: PreparedBaseline, candidate: SweepCandidate) -> ScenarioRun:
    params = SITUATIONS[candidate.situation].model.model_validate(candidate.parameters)
    return simulate_scenario(base, candidate.situation, params, candidate.actions)


def _evaluate(base: PreparedBaseline, candidate: SweepCandidate, run: ScenarioRun) -> CandidateRun:
    """KPI kandidat dibandingkan dengan baseline, seperti `run_engine`."""
    until = comparison_end(base, run)
    side = scenario_side(base, run, until)
    return CandidateRun(
        candidate,
        CandidateStatus.DONE,
        side=side,
        until=until,
        vs_baseline=kpi_items(base.side(until).kpis, side.kpis, None, base.config),
        targets=run.targets,
        notes=run.notes,
        run=run,
    )


class _CurrentSides:
    """KPI pilihan pengguna per hari terakhir pembanding, untuk `vs_current`."""

    def __init__(self, base: PreparedBaseline, run: ScenarioRun, reference: CandidateRun) -> None:
        assert reference.until is not None and reference.side is not None
        self.base = base
        self.run = run
        self.sides: dict[date, SideRun] = {reference.until: reference.side}

    def side(self, until: date) -> SideRun:
        if until not in self.sides:
            self.sides[until] = scenario_side(self.base, self.run, until)
        return self.sides[until]

    def compare(self, result: CandidateRun) -> None:
        """Bandingkan kandidat dengan pilihan pengguna pada jendela yang sama: sampai hari
        kerja terakhir dari baseline, pilihan pengguna, dan kandidat."""
        assert result.run is not None and result.side is not None and result.until is not None
        shared = comparison_end(self.base, result.run, self.run)
        mine = (
            result.side if shared == result.until else scenario_side(self.base, result.run, shared)
        )
        result.vs_current = kpi_items(self.side(shared).kpis, mine.kpis, None, self.base.config)


def _since(began: float) -> int:
    return round((time.perf_counter() - began) * 1000)
