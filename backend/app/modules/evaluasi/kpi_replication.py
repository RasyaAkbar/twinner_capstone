"""KPI evaluasi di setiap replikasi Monte Carlo: median dan rentangnya.

Loop replikasi hanya ada satu, di `sim_replication.run_replications` modul Simulasi: replikasi
ke-i memakai `random.Random(seed x SEED_STRIDE + i)` dan `sample_efforts` dunia baseline, dan
baseline serta skenario memakai undian yang sama (common random numbers). Loop itu memanggil
`KpiCollector.add` untuk setiap replikasi, sehingga hasil simulasi dan evaluasi KPI memakai
replikasi yang sama persis.

Setiap replikasi menghitung semua KPI kedua sisi dengan `kpi_calculator.calculate`. Keluarannya
median, P10-P90, dan min-max baseline, skenario, dan selisihnya per KPI. On-Time Launch
Probability adalah proporsi replikasi yang semua proyeknya tepat waktu, jadi tidak punya rentang.
"""

from collections import defaultdict
from dataclasses import dataclass, field

from app.modules.evaluasi.kpi_calculator import (
    Reference,
    SideKpis,
    SimulationOutput,
    calculate,
    item_weeks,
)
from app.modules.evaluasi.kpi_definitions import KPI_KEYS, ON_TIME_PROBABILITY, EvaluationConfig
from app.modules.simulasi.sim_calendar import CapacityCalendar
from app.modules.simulasi.sim_stats import Range, summarize
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


class KpiCollector:
    """Mengumpulkan KPI kedua sisi dari setiap replikasi lalu meringkasnya."""

    def __init__(
        self,
        worlds: dict[str, tuple[SimWorld, CapacityCalendar]],
        reference: Reference,
        config: EvaluationConfig,
    ) -> None:
        self.reference = reference
        self.config = config
        # Item D-08 tidak bergantung pada effort task; dihitung sekali per sisi.
        self.items = {side: item_weeks(*worlds[side]) for side in SIDES}
        self.runs = 0
        self.values: dict[str, dict[str, list[float | None]]] = {
            key: {"baseline": [], "scenario": [], "delta": []} for key in KPI_KEYS
        }
        self.on_time = {side: 0 for side in SIDES}
        self.project_on_time: dict[str, dict[str, int]] = {side: defaultdict(int) for side in SIDES}
        self.projects: dict[str, set[str]] = {side: set() for side in SIDES}

    def add(self, outputs: dict[str, SimulationOutput]) -> dict[str, SideKpis]:
        """KPI satu replikasi; `outputs` berisi keluaran simulasi "baseline" dan "scenario"."""
        self.runs += 1
        kpis = {
            side: calculate(outputs[side], self.reference, self.config, self.items[side])
            for side in SIDES
        }
        for side in SIDES:
            self.on_time[side] += kpis[side].on_time
            for project_id, sv in kpis[side].project_sv.items():
                self.projects[side].add(project_id)
                self.project_on_time[side][project_id] += sv is not None and sv <= 0
        for key in KPI_KEYS:
            b = kpis["baseline"].values[key]
            s = kpis["scenario"].values[key]
            self.values[key]["baseline"].append(b)
            self.values[key]["scenario"].append(s)
            self.values[key]["delta"].append(
                round(s - b, 6) if b is not None and s is not None else None
            )
        return kpis

    def result(self, seed: int) -> ReplicatedKpis:
        result = ReplicatedKpis(runs=self.runs, seed=seed)
        for key in KPI_KEYS:
            if key == ON_TIME_PROBABILITY:
                result.ranges[key] = {"baseline": None, "scenario": None, "delta": None}
                continue
            result.ranges[key] = {
                part: summarize(values) for part, values in self.values[key].items()
            }
        for side in SIDES:
            result.on_time[side] = round(self.on_time[side] / self.runs, 6) if self.runs else 0.0
            result.project_on_time[side] = {
                project_id: round(self.project_on_time[side][project_id] / self.runs, 6)
                for project_id in sorted(self.projects[side])
            }
        return result
