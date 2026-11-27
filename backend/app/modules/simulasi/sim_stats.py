"""Statistik ringkas nilai di antara replikasi Monte Carlo: median, P10-P90, dan min-max.

Dipisah dari `sim_replication` supaya modul Evaluasi (`kpi_replication`) dapat memakainya tanpa
mengimpor mesin replikasi.
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date


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


def percentile(sorted_values: Sequence[float], q: float) -> float:
    """Persentil dengan interpolasi linier (sama dengan metode bawaan numpy)."""
    if not sorted_values:
        raise ValueError("Tidak ada nilai")
    position = (len(sorted_values) - 1) * q
    low = math.floor(position)
    high = math.ceil(position)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


# Presisi nilai KPI evaluasi; median dan persentil dibulatkan sama supaya min <= median <= max.
DECIMALS = 6


def summarize(values: Sequence[float | None]) -> Range:
    present = sorted(v for v in values if v is not None)
    missing = len(values) - len(present)
    if not present:
        return Range(None, None, None, None, None, 0, missing)
    return Range(
        median=round(percentile(present, 0.5), DECIMALS),
        p10=round(percentile(present, 0.1), DECIMALS),
        p90=round(percentile(present, 0.9), DECIMALS),
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
