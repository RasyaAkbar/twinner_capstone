"""Scenario Manager (SCRUM-22): membuat, membaca, mengubah, menyimpan, menduplikasi, dan
menghapus skenario what-if.

Alur UC-01/UC-02 (SCRUM-19, SCRUM-20, SCRUM-21):
1. `create_scenario` membuat draft dari satu situasi, dikaitkan ke snapshot terbaru (atau yang
   dipilih). Tanpa baseline sama sekali, skenario tidak dapat dibuat (`NoBaselineError`).
2. `update_scenario` menyimpan nama dan parameter apa adanya sebagai draft, lalu melaporkan
   kesalahan parameter tanpa menolaknya, supaya isian formulir yang belum selesai tidak hilang.
3. `save_scenario` menjalankan validasi penuh. Bila lolos, parameter disimpan dalam bentuk
   kanonis dan status menjadi `saved`; bila tidak, `ScenarioValidationError` (HTTP 422).
4. `duplicate_scenario` menyalin situasi, parameter, status, dan snapshot ke skenario baru
   dengan nama baru.

Skenario `saved` selalu berisi parameter yang valid: mengubah parameternya mengembalikan
status ke `draft`, sedangkan snapshot yang ditunjuk tidak pernah berubah.
"""

import json
from copy import deepcopy
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.modules.simulasi import snapshot_service
from app.modules.simulasi.scenario_models import (
    NAME_MAX_LENGTH,
    Scenario,
    ScenarioStatus,
    Situation,
)
from app.modules.simulasi.scenario_params import (
    SITUATIONS,
    BaselineIndex,
    ParameterError,
    validate_parameters,
)
from app.modules.simulasi.scenario_schemas import (
    ParameterIssue,
    ScenarioCreate,
    ScenarioDuplicate,
    ScenarioRead,
    ScenarioSummary,
    ScenarioUpdate,
    SituationInfo,
)
from app.modules.simulasi.snapshot_models import BaselineSnapshot

# Batas ukuran parameter (JSON), jauh di atas isian formulir terbesar (200 task x 8 situasi).
MAX_PARAMETERS_CHARS = 200_000
COPY_SUFFIX = " (salinan)"
NO_BASELINE_MESSAGE = (
    "Belum ada baseline snapshot, jadi skenario belum dapat dibuat. "
    "Impor data baseline terlebih dahulu"
)


class ScenarioNotFoundError(Exception):
    def __init__(self, scenario_id: int) -> None:
        self.message = f"Skenario {scenario_id} tidak ditemukan"
        super().__init__(self.message)


class NoBaselineError(Exception):
    """Belum ada baseline snapshot sama sekali (UC-01 alur alternatif, HTTP 409)."""

    message = NO_BASELINE_MESSAGE


class ScenarioValidationError(Exception):
    """Isi permintaan atau parameter skenario tidak valid (HTTP 422).

    `loc` setiap kesalahan relatif terhadap body permintaan, misalnya ("name",) atau
    ("parameters", "absence_period", "end").
    """

    def __init__(self, errors: list[tuple[tuple[str | int, ...], str, str]]) -> None:
        super().__init__(errors)
        self.errors = errors

    @classmethod
    def from_parameters(cls, errors: list[ParameterError]) -> "ScenarioValidationError":
        return cls([(("parameters", *e.loc), e.message, e.type) for e in errors])

    def as_detail(self) -> list[dict[str, Any]]:
        return [{"loc": ["body", *loc], "msg": msg, "type": kind} for loc, msg, kind in self.errors]


# --- situasi -----------------------------------------------------------------------------------


def list_situations() -> list[SituationInfo]:
    """Delapan situasi berurutan sesuai Tabel 4.2, beserta skema JSON parameternya."""
    return [
        SituationInfo(
            situation=spec.situation,
            label=spec.label,
            description=spec.description,
            parameters_schema=spec.model.model_json_schema(),
        )
        for spec in SITUATIONS.values()
    ]


# --- baca --------------------------------------------------------------------------------------


def list_scenarios(
    db: Session,
    status: ScenarioStatus | None = None,
    situation: Situation | None = None,
    limit: int = 100,
) -> list[ScenarioSummary]:
    """Terakhir diubah lebih dulu. Isi parameter tidak ikut dikirim."""
    statement = select(Scenario)
    if status is not None:
        statement = statement.where(Scenario.status == status)
    if situation is not None:
        statement = statement.where(Scenario.situation == situation)
    statement = statement.order_by(Scenario.updated_at.desc(), Scenario.scenario_id.desc())
    return [ScenarioSummary.model_validate(s) for s in db.scalars(statement.limit(limit))]


def get_scenario(db: Session, scenario_id: int) -> ScenarioRead:
    return _read(db, _get(db, scenario_id))


# --- tulis -------------------------------------------------------------------------------------


def create_scenario(db: Session, payload: ScenarioCreate, username: str | None) -> ScenarioRead:
    snapshot = _snapshot_for(db, payload.snapshot_id)
    _check_size(payload.parameters)
    scenario = Scenario(
        name=payload.name,
        situation=payload.situation,
        status=ScenarioStatus.DRAFT,
        snapshot_id=snapshot.snapshot_id,
        data_source=snapshot.data_source,
        parameters=payload.parameters,
        created_by=username,
        updated_by=username,
    )
    db.add(scenario)
    db.commit()
    db.refresh(scenario)
    return _read(db, scenario)


def update_scenario(
    db: Session, scenario_id: int, payload: ScenarioUpdate, username: str | None
) -> ScenarioRead:
    scenario = _get(db, scenario_id)
    if payload.name is not None:
        scenario.name = payload.name
    if payload.parameters is not None and payload.parameters != scenario.parameters:
        _check_size(payload.parameters)
        scenario.parameters = payload.parameters
        # Parameter baru belum divalidasi penuh, jadi skenario kembali menjadi draft.
        scenario.status = ScenarioStatus.DRAFT
    _touch(scenario, username)
    db.commit()
    db.refresh(scenario)
    return _read(db, scenario)


def save_scenario(db: Session, scenario_id: int, username: str | None) -> ScenarioRead:
    """Validasi penuh lalu tandai `saved`. Gagal: `ScenarioValidationError`, tidak ada perubahan."""
    scenario = _get(db, scenario_id)
    outcome = validate_parameters(scenario.situation, scenario.parameters, _index(db, scenario))
    if not outcome.ok or outcome.params is None:
        raise ScenarioValidationError.from_parameters(outcome.errors)
    # Bentuk kanonis: nilai bawaan terisi, teks dirapikan, tanggal ISO. Mesin simulasi membaca
    # bentuk ini tanpa perlu menebak field yang tidak dikirim formulir.
    scenario.parameters = outcome.params.model_dump(mode="json")
    scenario.status = ScenarioStatus.SAVED
    scenario.saved_at = func.now()
    _touch(scenario, username)
    db.commit()
    db.refresh(scenario)
    return _read(db, scenario, errors=[])


def duplicate_scenario(
    db: Session, scenario_id: int, payload: ScenarioDuplicate, username: str | None
) -> ScenarioRead:
    """Salinan dengan semua parameter, situasi, status, dan snapshot yang sama (SCRUM-21)."""
    source = _get(db, scenario_id)
    name = payload.name or copy_name(source.name)
    if name == source.name:
        raise ScenarioValidationError(
            [(("name",), "Nama salinan harus berbeda dari nama skenario asal", "same_name")]
        )
    copy = Scenario(
        name=name,
        situation=source.situation,
        status=source.status,
        snapshot_id=source.snapshot_id,
        data_source=source.data_source,
        parameters=deepcopy(source.parameters),
        duplicated_from=source.scenario_id,
        created_by=username,
        updated_by=username,
        # Parameter dan snapshot salinan sama persis dengan asalnya, jadi status saved tetap sah.
        saved_at=func.now() if source.status == ScenarioStatus.SAVED else None,
    )
    db.add(copy)
    db.commit()
    db.refresh(copy)
    return _read(db, copy)


def delete_scenario(db: Session, scenario_id: int) -> None:
    db.delete(_get(db, scenario_id))
    db.commit()


def copy_name(name: str) -> str:
    """'<nama> (salinan)', dipotong supaya tetap muat di kolom `name`."""
    return name[: NAME_MAX_LENGTH - len(COPY_SUFFIX)].rstrip() + COPY_SUFFIX


# --- bantuan -----------------------------------------------------------------------------------


def _get(db: Session, scenario_id: int) -> Scenario:
    scenario = db.get(Scenario, scenario_id)
    if scenario is None:
        raise ScenarioNotFoundError(scenario_id)
    return scenario


def _snapshot_for(db: Session, snapshot_id: int | None) -> BaselineSnapshot:
    if snapshot_id is None:
        current = snapshot_service.current_snapshot_id(db)
        if current is None:
            raise NoBaselineError()
        snapshot_id = current
    snapshot = db.get(BaselineSnapshot, snapshot_id)
    if snapshot is None:
        raise ScenarioValidationError(
            [
                (
                    ("snapshot_id",),
                    f"Baseline snapshot {snapshot_id} tidak ditemukan",
                    "snapshot_not_found",
                )
            ]
        )
    return snapshot


def _check_size(parameters: dict[str, Any]) -> None:
    if len(json.dumps(parameters, ensure_ascii=False)) > MAX_PARAMETERS_CHARS:
        raise ScenarioValidationError(
            [
                (
                    ("parameters",),
                    f"Parameter terlalu besar (paling banyak {MAX_PARAMETERS_CHARS} karakter)",
                    "parameters_too_large",
                )
            ]
        )


def _touch(scenario: Scenario, username: str | None) -> None:
    scenario.updated_by = username
    scenario.updated_at = func.now()


def _index(db: Session, scenario: Scenario) -> BaselineIndex:
    return BaselineIndex.build(snapshot_service.load_snapshot(db, scenario.snapshot_id))


def _read(
    db: Session, scenario: Scenario, errors: list[ParameterError] | None = None
) -> ScenarioRead:
    if errors is None:
        errors = validate_parameters(
            scenario.situation, scenario.parameters, _index(db, scenario)
        ).errors
    summary = ScenarioSummary.model_validate(scenario)
    return ScenarioRead(
        **summary.model_dump(),
        parameters=scenario.parameters,
        parameter_errors=[ParameterIssue(**error.as_detail()) for error in errors],
    )
