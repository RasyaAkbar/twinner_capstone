"""Skema baseline snapshot (SCRUM-15).

Model baris D-01 s.d. D-11 di sini dipakai di tiga tempat sekaligus: validasi CSV saat impor,
respons API, dan keluaran loader untuk mesin simulasi. Nama dan urutan kolomnya sama dengan
Data Dictionary (DATA_CONTRACT bagian 6); tes memastikan keduanya sama dengan model
`app.modules.data.models`. Nilai enum dan pola ID diambil dari `app.modules.data.enums`, jadi
daftarnya tetap ditulis di satu tempat.

Aturan format mengikuti DATA_CONTRACT bagian 5: tanggal ISO `YYYY-MM-DD`, boolean `true`/`false`,
titik sebagai pemisah desimal, sel kosong berarti NULL.
"""

import re
from datetime import date, datetime
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StringConstraints

from app.modules.data.enums import (
    ASSIGNMENT_ID_PATTERN,
    AVAILABILITY_ID_PATTERN,
    COMPLEXITY_ID_PATTERN,
    DEPENDENCY_ID_PATTERN,
    EVENT_ID_PATTERN,
    PROJECT_ID_PATTERN,
    RESOURCE_ID_PATTERN,
    ROLE_ID_PATTERN,
    SECONDARY_ROLE_IDS_PATTERN,
    TASK_ID_PATTERN,
    WORKLOAD_ITEM_ID_PATTERN,
    AbsenceType,
    ChangeEventType,
    Decision,
    DependencyType,
    ProjectStatus,
    ResolutionMode,
    Seniority,
    Severity,
    TaskPhase,
    TaskStatus,
    WorkloadItemType,
)

ISO_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _iso_date(value: Any) -> Any:
    """Hanya `YYYY-MM-DD`. Bentuk lain (`05/10/2026`, `5 Okt 2026`) ditolak, bukan ditebak."""
    if isinstance(value, str):
        if not ISO_DATE.fullmatch(value):
            raise ValueError("Tanggal harus berformat YYYY-MM-DD")
        try:
            return date.fromisoformat(value)
        except ValueError:
            raise ValueError(f"Tanggal {value} tidak ada di kalender") from None
    return value


def _csv_bool(value: Any) -> Any:
    if isinstance(value, str):
        lowered = value.lower()
        if lowered not in ("true", "false"):
            raise ValueError("Isi true atau false")
        return lowered == "true"
    return value


IsoDate = Annotated[date, BeforeValidator(_iso_date)]
CsvBool = Annotated[bool, BeforeValidator(_csv_bool)]


def _id(pattern: str) -> Any:
    return Annotated[str, StringConstraints(pattern=pattern, max_length=20)]


def _text(max_length: int) -> Any:
    return Annotated[str, StringConstraints(min_length=1, max_length=max_length)]


ProjectId = _id(PROJECT_ID_PATTERN)
ResourceId = _id(RESOURCE_ID_PATTERN)
RoleId = _id(ROLE_ID_PATTERN)
ComplexityId = _id(COMPLEXITY_ID_PATTERN)
TaskId = _id(TASK_ID_PATTERN)

# Contoh yang ditampilkan saat format ID salah, dicari dari pola regex-nya.
ID_EXAMPLES = {
    PROJECT_ID_PATTERN: "PRJ-001",
    RESOURCE_ID_PATTERN: "RES-001",
    ROLE_ID_PATTERN: "ROLE-BE",
    COMPLEXITY_ID_PATTERN: "CX-M",
    TASK_ID_PATTERN: "TSK-0001",
    DEPENDENCY_ID_PATTERN: "DEP-001",
    ASSIGNMENT_ID_PATTERN: "ASG-0001",
    WORKLOAD_ITEM_ID_PATTERN: "WLI-0001",
    AVAILABILITY_ID_PATTERN: "AVL-001",
    EVENT_ID_PATTERN: "EVT-001",
    SECONDARY_ROLE_IDS_PATTERN: "ROLE-DM;ROLE-FE",
}


class SnapshotRow(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class ProjectRow(SnapshotRow):
    """D-01 projects"""

    project_id: ProjectId
    project_name: _text(200)
    process_owner: _text(200)
    priority_rank: int = Field(ge=1)
    start_date: IsoDate
    target_launch_date: IsoDate
    actual_launch_date: IsoDate | None
    status: ProjectStatus
    dor_fulfilled: CsvBool


class ResourceRow(SnapshotRow):
    """D-02 resources"""

    resource_id: ResourceId
    resource_name: _text(200)
    primary_role_id: RoleId
    secondary_role_ids: (
        Annotated[str, StringConstraints(pattern=SECONDARY_ROLE_IDS_PATTERN, max_length=200)] | None
    )
    cross_role_capable: CsvBool
    join_date: IsoDate
    seniority: Seniority


class RoleRow(SnapshotRow):
    """D-03 roles (capacity_reference: penyebut Resource Load Index)"""

    role_id: RoleId
    role_name: _text(100)
    capacity_reference: float = Field(gt=0)
    description: str | None


class ComplexityLevelRow(SnapshotRow):
    """D-04 complexity_levels (min_days dan max_days untuk replikasi simulasi)"""

    complexity_id: ComplexityId
    complexity_name: _text(50)
    min_days: float = Field(ge=0)
    max_days: float = Field(ge=0)
    load_weight: float = Field(ge=0)


class TaskRow(SnapshotRow):
    """D-05 tasks"""

    task_id: TaskId
    project_id: ProjectId
    feature_name: _text(200)
    task_name: _text(300)
    required_role_id: RoleId
    complexity_id: ComplexityId
    priority_rank: int = Field(ge=1)
    estimated_days: float = Field(gt=0)
    phase: TaskPhase
    planned_start: IsoDate
    planned_finish: IsoDate
    actual_start: IsoDate | None
    actual_finish: IsoDate | None
    status: TaskStatus
    qa_reject_count: int = Field(ge=0)
    dod_fulfilled: CsvBool


class TaskDependencyRow(SnapshotRow):
    """D-06 task_dependencies (task_id bergantung pada depends_on_task_id)"""

    dependency_id: _id(DEPENDENCY_ID_PATTERN)
    task_id: TaskId
    depends_on_task_id: TaskId
    dependency_type: DependencyType
    propagation_factor: float = Field(gt=0, le=1)


class AssignmentRow(SnapshotRow):
    """D-07 assignments"""

    assignment_id: _id(ASSIGNMENT_ID_PATTERN)
    task_id: TaskId
    resource_id: ResourceId
    role_in_task: RoleId
    assigned_date: IsoDate
    released_date: IsoDate | None
    is_takeover: CsvBool


class WorkloadItemRow(SnapshotRow):
    """D-08 workload_items (beban non-proyek yang mengurangi kapasitas anggota)"""

    item_id: _id(WORKLOAD_ITEM_ID_PATTERN)
    resource_id: ResourceId
    item_type: WorkloadItemType
    related_project_id: ProjectId | None
    received_date: IsoDate
    started_date: IsoDate | None
    resolved_date: IsoDate | None
    severity: Severity | None
    is_hotfix: CsvBool


class AvailabilityRow(SnapshotRow):
    """D-09 availability (ketidakhadiran dan rotasi)"""

    availability_id: _id(AVAILABILITY_ID_PATTERN)
    resource_id: ResourceId
    date_start: IsoDate
    date_end: IsoDate
    absence_type: AbsenceType
    is_known_at_planning: CsvBool


class ChangeEventRow(SnapshotRow):
    """D-10 change_events"""

    event_id: _id(EVENT_ID_PATTERN)
    project_id: ProjectId
    event_type: ChangeEventType
    event_date: IsoDate
    requested_by: Annotated[str, StringConstraints(max_length=100)] | None
    decision: Decision | None
    resolution_mode: ResolutionMode | None
    affected_task_id: TaskId | None
    extension_days: float | None = Field(ge=0)


class HolidayRow(SnapshotRow):
    """D-11 holidays"""

    holiday_date: IsoDate
    holiday_name: _text(200)


ROW_MODELS: dict[str, type[SnapshotRow]] = {
    "roles": RoleRow,
    "complexity_levels": ComplexityLevelRow,
    "holidays": HolidayRow,
    "projects": ProjectRow,
    "resources": ResourceRow,
    "tasks": TaskRow,
    "task_dependencies": TaskDependencyRow,
    "assignments": AssignmentRow,
    "workload_items": WorkloadItemRow,
    "availability": AvailabilityRow,
    "change_events": ChangeEventRow,
}

# Kolom pertama setiap model adalah primary key-nya.
PRIMARY_KEYS: dict[str, str] = {
    table: next(iter(m.model_fields)) for table, m in ROW_MODELS.items()
}

DataSource = Literal["synthetic", "puti"]

# Batas ukuran impor (per berkas sama dengan impor D-08) dan jumlah berkas per permintaan.
MAX_FILE_CHARS = 5_000_000
MAX_TOTAL_CHARS = 20_000_000
MAX_FILES = 20


class SnapshotImportRequest(BaseModel):
    """11 berkas CSV final sources (UTF-8, pemisah koma, baris pertama nama kolom)."""

    model_config = ConfigDict(extra="forbid")

    data_source: DataSource = Field(description="Asal data baseline: synthetic atau puti")
    label: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)] | None = Field(
        default=None, description="Catatan bebas, misalnya 'sintetis seed 42 per 2026-11-06'"
    )
    files: dict[str, Annotated[str, Field(max_length=MAX_FILE_CHARS)]] = Field(
        min_length=1,
        max_length=MAX_FILES,
        description=(
            "Nama berkas ke isi CSV, satu per tabel D-01 s.d. D-11, misalnya "
            '{"projects.csv": "project_id,project_name,...", ...}. Akhiran .csv dan folder '
            "di depan nama berkas boleh ada atau tidak."
        ),
    )
    dry_run: bool = Field(
        default=True, strict=True, description="true hanya memeriksa, tanpa menyimpan"
    )


class SnapshotImportError(BaseModel):
    file: str | None = Field(description="Nama tabel/berkas, kosong untuk kesalahan permintaan")
    row: int | None = Field(description="Nomor baris di berkas; baris judul kolom adalah baris 1")
    record_id: str | None = Field(description="Primary key baris, bila terbaca")
    field: str | None
    message: str


class SnapshotSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    snapshot_id: int
    data_source: DataSource
    label: str | None
    imported_by: str | None
    created_at: datetime
    row_counts: dict[str, int]
    checksum: str = Field(description="SHA-256 isi snapshot; sama berarti isinya identik")
    is_current: bool = Field(False, description="Snapshot terbaru, dipakai skenario baru")


class SnapshotImportResult(BaseModel):
    dry_run: bool
    applied: bool = Field(description="true bila snapshot benar-benar disimpan")
    row_counts: dict[str, int] = Field(description="Jumlah baris data per tabel")
    error_count: int
    errors: list[SnapshotImportError] = Field(description="Paling banyak 500 kesalahan pertama")
    snapshot: SnapshotSummary | None = Field(description="Snapshot baru bila applied")


class BaselineSnapshotData(SnapshotSummary):
    """Snapshot lengkap: masukan langkah 1 simulasi (§IV.2.3).

    Setiap baris diurutkan menurut primary key-nya. Objek ini hasil muat ulang dari basis data,
    jadi aman diubah atau di-`model_copy(deep=True)` oleh aturan skenario tanpa mengubah baseline
    yang tersimpan.
    """

    roles: list[RoleRow]
    complexity_levels: list[ComplexityLevelRow]
    holidays: list[HolidayRow]
    projects: list[ProjectRow]
    resources: list[ResourceRow]
    tasks: list[TaskRow]
    task_dependencies: list[TaskDependencyRow]
    assignments: list[AssignmentRow]
    workload_items: list[WorkloadItemRow]
    availability: list[AvailabilityRow]
    change_events: list[ChangeEventRow]
