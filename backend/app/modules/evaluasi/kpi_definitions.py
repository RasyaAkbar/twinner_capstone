"""Daftar KPI evaluasi yang disepakati (FR-04.1 s.d. FR-04.10) dan parameternya (FR-04.11).

Sumber: "FR Dashboard Digital Twin - Formula KPI". Setiap KPI punya:

- `direction`: arah nilai yang lebih baik. `lower` (lebih kecil lebih baik), `higher` (lebih
  besar lebih baik), atau `target` (lebih dekat ke `target` lebih baik, misalnya EAR = 1.0).
- `tolerance`: selisih mutlak yang masih dianggap `same`. Selisih di luar toleransi menjadi
  `better` atau `worse` menurut arahnya. Toleransi dapat diganti lewat `EvaluationConfig`.
- `missing`: arti nilai kosong. `worse` berarti "tidak tercapai" (misalnya proyek yang tidak
  dapat diluncurkan karena ada task tidak terjadwal tidak punya Schedule Variance), sehingga
  sisi tanpa nilai dianggap lebih buruk. `not_applicable` berarti "tidak ada yang diukur"
  (misalnya tidak ada hari tunggu), sehingga perbandingannya `same` tanpa selisih.
"""

from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Direction(StrEnum):
    LOWER = "lower"
    HIGHER = "higher"
    TARGET = "target"


class KpiUnit(StrEnum):
    WORKING_DAYS = "working_days"
    PROBABILITY = "probability"
    INDEX = "index"
    COUNT = "count"
    RATIO = "ratio"


class Missing(StrEnum):
    WORSE = "worse"
    NOT_APPLICABLE = "not_applicable"


class KpiStatus(StrEnum):
    BETTER = "better"
    SAME = "same"
    WORSE = "worse"


@dataclass(frozen=True)
class KpiDefinition:
    key: str
    fr: str
    label: str
    unit: KpiUnit
    direction: Direction
    tolerance: float
    definition: str
    formula: str
    target: float | None = None
    missing: Missing = Missing.NOT_APPLICABLE


SCHEDULE_VARIANCE = "schedule_variance"
ON_TIME_PROBABILITY = "on_time_probability"
RESOURCE_LOAD_INDEX = "resource_load_index"
OVERLOAD_FLAGS = "overload_flags"
WORKLOAD_BALANCE_INDEX = "workload_balance_index"
SUSTAINED_OVERLOAD = "sustained_overload_indicator"
CASCADE_DELAY_IMPACT = "cascade_delay_impact"
ESTIMATE_ACCURACY = "estimate_accuracy_ratio"
REWORK_RATE = "rework_rate"
BLOCKED_RATIO = "blocked_ratio"
REALLOCATED_RATIO = "reallocated_ratio"

KPI_DEFINITIONS: tuple[KpiDefinition, ...] = (
    KpiDefinition(
        SCHEDULE_VARIANCE,
        "FR-04.1",
        "Schedule Variance (SV)",
        KpiUnit.WORKING_DAYS,
        Direction.LOWER,
        0.0,
        "Selisih hari kerja tanggal launching hasil simulasi terhadap target launching, untuk "
        "proyek aktif yang paling terlambat. Positif berarti terlambat. Kosong bila ada proyek "
        "yang tidak dapat diluncurkan (punya task tidak terjadwal).",
        "SV(p) = hari kerja dari target_launch_date ke proyeksi selesai p (negatif bila lebih "
        "awal); SV = max SV(p) proyek aktif",
        missing=Missing.WORSE,
    ),
    KpiDefinition(
        ON_TIME_PROBABILITY,
        "FR-04.2",
        "On-Time Launch Probability",
        KpiUnit.PROBABILITY,
        Direction.HIGHER,
        0.05,
        "Proporsi replikasi simulasi yang semua proyek aktifnya diluncurkan tepat waktu "
        "(SV <= 0). Tanpa replikasi nilainya 1 atau 0.",
        "jumlah replikasi dengan SV(p) <= 0 untuk semua p / jumlah replikasi",
    ),
    KpiDefinition(
        RESOURCE_LOAD_INDEX,
        "FR-04.3",
        "Resource Load Index (RLI) tertinggi",
        KpiUnit.INDEX,
        Direction.LOWER,
        0.05,
        "RLI mingguan tertinggi di antara semua anggota. RLI dihitung dari tiga kategori beban "
        "PuTI: task proyek yang dikerjakan, item operasional, dan tiket terbuka pada minggu itu.",
        "RLI(r,w) = (wp x task proyek + wo x item operasional + wt x tiket terbuka) / "
        "kapasitas referensi role; nilai = max RLI(r,w)",
    ),
    KpiDefinition(
        OVERLOAD_FLAGS,
        "FR-04.4",
        "Overload Flag",
        KpiUnit.COUNT,
        Direction.LOWER,
        0.0,
        "Jumlah pasangan anggota-minggu yang RLI-nya melampaui threshold.",
        "count(RLI(r,w) > threshold)",
    ),
    KpiDefinition(
        WORKLOAD_BALANCE_INDEX,
        "FR-04.5",
        "Workload Balance Index (WBI)",
        KpiUnit.INDEX,
        Direction.HIGHER,
        0.02,
        "Kemerataan beban antar anggota; 1 berarti beban merata sempurna. Dihitung per minggu "
        "lalu dirata-rata.",
        "WBI(w) = 1 - simpangan baku populasi RLI(.,w) / rata-rata RLI(.,w); WBI = rata-rata "
        "WBI(w)",
    ),
    KpiDefinition(
        SUSTAINED_OVERLOAD,
        "FR-04.6",
        "Sustained Overload Indicator (SOI)",
        KpiUnit.INDEX,
        Direction.LOWER,
        0.05,
        "Beban berlebih yang berlangsung beberapa minggu berturut-turut, dijumlah untuk semua "
        "anggota.",
        "SOI(r) = jumlah, untuk setiap periode >= minimum minggu berturut-turut dengan RLI > "
        "threshold, panjang periode x rata-rata (RLI - threshold); nilai = jumlah SOI(r)",
    ),
    KpiDefinition(
        CASCADE_DELAY_IMPACT,
        "FR-04.7",
        "Cascade Delay Impact (CDI)",
        KpiUnit.WORKING_DAYS,
        Direction.LOWER,
        0.5,
        "Keterlambatan yang diteruskan task terlambat ke seluruh task hilirnya, dijumlah untuk "
        "semua task. Hard dependency meneruskan penuh, soft dependency sebesar faktor "
        "propagasi.",
        "CDI(t) = keterlambatan(t) x jumlah, untuk setiap task hilir u, hasil kali faktor "
        "terbesar pada jalur t -> u; nilai = jumlah CDI(t)",
    ),
    KpiDefinition(
        ESTIMATE_ACCURACY,
        "FR-04.8",
        "Estimate Accuracy Ratio (EAR)",
        KpiUnit.RATIO,
        Direction.TARGET,
        0.02,
        "Durasi aktual hasil simulasi dibanding durasi estimasi baseline. 1.0 berarti estimasi "
        "tepat. Rincian per level kompleksitas dan per role ada di hasil evaluasi.",
        "EAR = jumlah durasi aktual (hari kerja mulai s.d. selesai) / jumlah estimated_days",
        target=1.0,
    ),
    KpiDefinition(
        REWORK_RATE,
        "FR-04.9",
        "Rework Rate",
        KpiUnit.RATIO,
        Direction.LOWER,
        0.01,
        "Proporsi task yang pernah dikembalikan QA ke developer di antara task yang sudah "
        "masuk fase QA. Mesin simulasi tidak memodelkan loop QA, jadi nilainya berasal dari "
        "snapshot dan hanya berubah bila skenario mengubah scope.",
        "count(qa_reject_count > 0) / count(task in_qa, done, atau qa_reject_count > 0)",
    ),
    KpiDefinition(
        BLOCKED_RATIO,
        "FR-04.10",
        "Blocked Time Ratio",
        KpiUnit.RATIO,
        Direction.LOWER,
        0.01,
        "Bagian hari kerja anggota yang habis menunggu (dependency atau blocker) tanpa "
        "pekerjaan pengganti.",
        "hari tunggu tanpa task lain dikerjakan / total hari kerja anggota",
    ),
    KpiDefinition(
        REALLOCATED_RATIO,
        "FR-04.10",
        "Reallocated Time Ratio",
        KpiUnit.RATIO,
        Direction.HIGHER,
        0.05,
        "Bagian hari tunggu yang terisi task independen (praktik pengalihan di PuTI).",
        "hari tunggu dengan task lain dikerjakan / total hari tunggu",
    ),
)
KPI_BY_KEY = {definition.key: definition for definition in KPI_DEFINITIONS}
KPI_KEYS = tuple(KPI_BY_KEY)


class EvaluationConfig(BaseModel):
    """Parameter rumus KPI (kolom "Parameter Default") yang dapat dikonfigurasi (FR-04.11)."""

    model_config = ConfigDict(extra="forbid")

    wp: float = Field(1.0, ge=0, description="Bobot Work Planned (task proyek) pada RLI")
    wo: float = Field(0.5, ge=0, description="Bobot Work Operational (item operasional) pada RLI")
    wt: float = Field(0.3, ge=0, description="Bobot Work Target (tiket terbuka) pada RLI")
    capacity_reference: float | None = Field(
        None,
        gt=0,
        description="Kapasitas referensi untuk semua anggota; kosong berarti capacity_reference "
        "peran utama anggota (D-03, bawaan 3.0)",
    )
    overload_threshold: float = Field(1.0, gt=0, description="Threshold Overload Flag")
    soi_min_weeks: int = Field(
        2, ge=1, le=52, description="Minimum minggu berturut-turut untuk SOI"
    )
    hard_factor: float = Field(
        1.0, gt=0, le=1, description="Faktor propagasi hard dependency pada CDI"
    )
    soft_factor: float | None = Field(
        None,
        gt=0,
        le=1,
        description="Faktor propagasi soft dependency pada CDI; kosong berarti "
        "propagation_factor dependency (D-06, bawaan 0.3)",
    )
    tolerances: dict[str, float] = Field(
        default_factory=dict,
        description="Toleransi status `same` per KPI; KPI yang tidak diisi memakai bawaannya",
    )

    @field_validator("tolerances")
    @classmethod
    def _known_keys(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - set(KPI_BY_KEY))
        if unknown:
            raise ValueError(f"KPI tidak dikenal: {', '.join(unknown)}")
        negative = sorted(key for key, tolerance in value.items() if tolerance < 0)
        if negative:
            raise ValueError(f"Toleransi tidak boleh negatif: {', '.join(negative)}")
        return value

    def tolerance(self, key: str) -> float:
        return self.tolerances.get(key, KPI_BY_KEY[key].tolerance)

    def resolved(self) -> "EvaluationConfig":
        """Konfigurasi dengan toleransi semua KPI terisi, untuk disimpan dan di-hash."""
        return self.model_copy(
            update={"tolerances": {key: self.tolerance(key) for key in KPI_KEYS}}
        )
