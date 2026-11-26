"""KPI Comparator (SCRUM-32): selisih terhadap baseline, status, dan skenario terbaik.

Status satu KPI (`compare`):

- `delta` = skenario - baseline.
- Arah `lower`: `better` bila delta < -toleransi, `worse` bila delta > toleransi.
- Arah `higher`: kebalikannya.
- Arah `target`: dibandingkan jaraknya ke target, |baseline - target| - |skenario - target|;
  `better` bila jarak skenario lebih dekat lebih dari toleransi, `worse` bila lebih jauh.
- Selain itu `same`. Nilai kosong mengikuti `KpiDefinition.missing`.

Skenario terbaik di antara skenario terpilih (`rank`), dengan bobot KPI (bawaan 1):

1. Skor status = jumlah bobot x (+1 better, 0 same, -1 worse). Status sudah memakai arah dan
   toleransi setiap KPI, jadi skor ini terbaca langsung: "berapa KPI membaik dikurangi
   memburuk".
2. Bila skor status sama: skor besaran = jumlah bobot x perbaikan ternormalisasi min-max di
   antara skenario terpilih (0 = perbaikan terkecil, 1 = terbesar; semua sama = 0).
3. Bila masih sama: run_id terkecil (skenario yang lebih dulu disimulasikan).
"""

from collections.abc import Sequence
from dataclasses import dataclass, replace

from app.modules.evaluasi.kpi_definitions import (
    KPI_BY_KEY,
    Direction,
    KpiDefinition,
    KpiStatus,
    Missing,
)
from app.modules.simulasi.sim_calendar import EPS

STATUS_SCORE = {KpiStatus.BETTER: 1.0, KpiStatus.SAME: 0.0, KpiStatus.WORSE: -1.0}


def compare(
    definition: KpiDefinition,
    baseline: float | None,
    scenario: float | None,
    tolerance: float | None = None,
    delta: float | None = None,
) -> tuple[float | None, KpiStatus]:
    """Selisih skenario - baseline dan statusnya.

    `delta` boleh diisi (median selisih per replikasi) untuk menggantikan skenario - baseline
    pada arah `lower`/`higher`, sama seperti status replikasi modul Simulasi.
    """
    tolerance = definition.tolerance if tolerance is None else tolerance
    if baseline is None or scenario is None:
        if baseline is None and scenario is None:
            return None, KpiStatus.SAME
        if definition.missing == Missing.NOT_APPLICABLE:
            return None, KpiStatus.SAME
        return None, KpiStatus.WORSE if scenario is None else KpiStatus.BETTER
    if delta is None:
        delta = round(scenario - baseline, 6)
    return delta, status_of(improvement(definition, baseline, scenario, delta), tolerance)


def improvement(
    definition: KpiDefinition, baseline: float, scenario: float, delta: float | None = None
) -> float:
    """Besar perbaikan skenario terhadap baseline; positif berarti membaik."""
    delta = scenario - baseline if delta is None else delta
    if definition.direction == Direction.LOWER:
        return -delta
    if definition.direction == Direction.HIGHER:
        return delta
    target = definition.target or 0.0
    return abs(baseline - target) - abs(scenario - target)


def status_of(gain: float, tolerance: float) -> KpiStatus:
    if gain > tolerance + EPS:
        return KpiStatus.BETTER
    if gain < -tolerance - EPS:
        return KpiStatus.WORSE
    return KpiStatus.SAME


# --- skenario terbaik ----------------------------------------------------------------------------


@dataclass(frozen=True)
class KpiOutcome:
    """Hasil perbandingan satu KPI satu skenario (nilai utama: deterministik atau median)."""

    baseline: float | None
    scenario: float | None
    delta: float | None
    status: KpiStatus


@dataclass(frozen=True)
class Candidate:
    run_id: int
    kpis: dict[str, KpiOutcome]


@dataclass(frozen=True)
class Ranked:
    run_id: int
    rank: int
    status_score: float
    magnitude_score: float
    better: int
    same: int
    worse: int

    @property
    def improves_on_baseline(self) -> bool:
        return self.status_score > EPS


def gain_of(key: str, outcome: KpiOutcome) -> float | None:
    if outcome.baseline is None or outcome.scenario is None:
        return None
    definition = KPI_BY_KEY[key]
    delta = outcome.delta if definition.direction != Direction.TARGET else None
    return improvement(definition, outcome.baseline, outcome.scenario, delta)


def rank(candidates: Sequence[Candidate], weights: dict[str, float] | None = None) -> list[Ranked]:
    """Urutkan skenario dari yang terbaik; KPI berbobot 0 tidak ikut dinilai."""
    weights = weights or {}
    keys = sorted({key for c in candidates for key in c.kpis})
    weight = {key: weights.get(key, 1.0) for key in keys}

    normalized: dict[tuple[int, str], float] = {}
    for key in keys:
        gains: dict[int, float] = {}
        for c in candidates:
            gain = gain_of(key, c.kpis[key]) if key in c.kpis else None
            if gain is not None:
                gains[c.run_id] = gain
        if not gains:
            continue
        low, high = min(gains.values()), max(gains.values())
        for run_id, g in gains.items():
            normalized[(run_id, key)] = (g - low) / (high - low) if high - low > EPS else 0.0

    scored: list[tuple[tuple, Ranked]] = []
    for c in candidates:
        counts = {status: 0 for status in KpiStatus}
        status_score = magnitude = 0.0
        for key, outcome in c.kpis.items():
            if weight[key] <= 0:
                continue
            counts[outcome.status] += 1
            status_score += weight[key] * STATUS_SCORE[outcome.status]
            magnitude += weight[key] * normalized.get((c.run_id, key), 0.0)
        status_score, magnitude = round(status_score, 6), round(magnitude, 6)
        ranked = Ranked(
            run_id=c.run_id,
            rank=0,
            status_score=status_score,
            magnitude_score=magnitude,
            better=counts[KpiStatus.BETTER],
            same=counts[KpiStatus.SAME],
            worse=counts[KpiStatus.WORSE],
        )
        scored.append(((-status_score, -magnitude, c.run_id), ranked))
    scored.sort(key=lambda pair: pair[0])
    return [replace(r, rank=position) for position, (_, r) in enumerate(scored, start=1)]
