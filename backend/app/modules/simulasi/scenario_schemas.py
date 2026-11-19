"""Skema API Scenario Manager (SCRUM-22)."""

from datetime import datetime
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from app.modules.simulasi.scenario_models import NAME_MAX_LENGTH, ScenarioStatus, Situation
from app.modules.simulasi.snapshot_schemas import DataSource

ScenarioName = Annotated[
    str, StringConstraints(strip_whitespace=True, min_length=1, max_length=NAME_MAX_LENGTH)
]

PARAMETERS_DESCRIPTION = (
    "Parameter situasi (lihat GET /simulasi/scenarios/situations). Boleh belum lengkap selama "
    "skenario masih draft; kesalahannya dilaporkan di `parameter_errors`."
)


class ScenarioCreate(BaseModel):
    """Draft skenario baru dari satu situasi keputusan (UC-01)."""

    model_config = ConfigDict(extra="forbid")

    name: ScenarioName
    situation: Situation
    snapshot_id: int | None = Field(
        default=None, ge=1, description="Baseline snapshot; kosong berarti snapshot terbaru"
    )
    parameters: dict[str, Any] = Field(default_factory=dict, description=PARAMETERS_DESCRIPTION)


class ScenarioUpdate(BaseModel):
    """Field yang tidak dikirim (atau null) tidak diubah. `parameters` mengganti seluruh isi
    parameter, bukan digabung. Situasi dan snapshot tidak dapat diubah; buat skenario baru."""

    model_config = ConfigDict(extra="forbid")

    name: ScenarioName | None = None
    parameters: dict[str, Any] | None = Field(default=None, description=PARAMETERS_DESCRIPTION)


class ScenarioDuplicate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: ScenarioName | None = Field(
        default=None, description="Nama salinan; kosong berarti '<nama asal> (salinan)'"
    )


class ScenarioSummary(BaseModel):
    """Satu baris daftar skenario."""

    model_config = ConfigDict(from_attributes=True)

    scenario_id: int
    name: str
    situation: Situation
    status: ScenarioStatus = Field(
        description="draft: belum disimpan atau diubah setelah disimpan; saved: parameter valid "
        "dan siap disimulasikan"
    )
    snapshot_id: int
    data_source: DataSource = Field(description="Asal data baseline: synthetic atau puti")
    duplicated_from: int | None = Field(description="Skenario asal bila hasil duplikasi")
    created_by: str | None
    updated_by: str | None
    created_at: datetime
    updated_at: datetime
    saved_at: datetime | None = Field(description="Terakhir kali lolos validasi dan disimpan")


class ParameterIssue(BaseModel):
    """Bentuknya sama dengan satu item `detail` respons 422."""

    loc: list[str | int] = Field(examples=[["body", "parameters", "absence_period", "end"]])
    msg: str
    type: str


class ScenarioRead(ScenarioSummary):
    parameters: dict[str, Any]
    parameter_errors: list[ParameterIssue] = Field(
        description="Hasil validasi parameter terhadap baseline snapshot skenario. Kosong berarti "
        "skenario dapat disimpan"
    )


class SituationInfo(BaseModel):
    """Satu situasi keputusan beserta skema parameternya untuk formulir UC-02."""

    situation: Situation
    label: str
    description: str
    parameters_schema: dict[str, Any] = Field(description="JSON Schema parameter situasi ini")
