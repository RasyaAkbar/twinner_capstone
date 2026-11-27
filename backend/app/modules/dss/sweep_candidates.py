"""Kandidat DSS per skenario (SCRUM-61).

`build_candidates` menyusun kandidat sweep untuk satu skenario yang sudah disimulasikan:

1. Parameter skenario pengguna divalidasi penuh terhadap snapshot-nya dan menjadi kandidat
   acuan (`is_current`), selalu di urutan pertama. Hasil sweep dibandingkan dengannya.
2. Generator lever situasinya (`sweep_levers.LEVERS`) mengusulkan nilai lain. Nilai yang sama
   dengan pilihan pengguna, atau yang sudah diusulkan sebelumnya, dilewati.
3. Kandidat lever parameter divalidasi ulang dengan `validate_parameters`. Kandidat yang tidak
   lolos, atau yang ditolak generatornya, dicatat sebagai dikecualikan beserta alasannya dan
   tidak disimulasikan.
4. Kandidat yang layak dibatasi `max_candidates` (termasuk acuan) supaya lama sweep terkendali.
   Sisanya tidak disimulasikan dan sweep ditandai `truncated`.

Urutannya: acuan, kandidat layak, lalu kandidat dikecualikan, masing-masing menurut urutan
generator. Masukan yang sama karena itu selalu menghasilkan daftar yang sama.

Modul ini tidak menyentuh basis data.
"""

import copy
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from app.modules.dss.sweep_levers import LEVERS, LeverKind, LeverOption, LeverSpec, LeverValue
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import (
    BaselineIndex,
    ParameterError,
    validate_parameters,
)
from app.modules.simulasi.sim_actions import ResponseAction

# Bawaan batas kandidat yang disimulasikan per sweep, termasuk pilihan pengguna. Job sweep
# meneruskan `Settings.dss_max_candidates` (env DSS_MAX_CANDIDATES).
MAX_CANDIDATES = 50


class SweepReferenceError(Exception):
    """Parameter skenario pengguna tidak valid terhadap snapshot-nya, jadi tidak dapat menjadi
    acuan sweep."""

    def __init__(self, errors: list[ParameterError]) -> None:
        super().__init__("; ".join(error.message for error in errors))
        self.errors = errors


@dataclass(frozen=True)
class SweepCandidate:
    situation: Situation
    lever: str
    value: LeverValue
    label: str
    # Parameter skenario yang disimulasikan untuk kandidat ini.
    parameters: dict[str, Any]
    # Respons yang diterapkan di atas skenario (lever pengalihan).
    actions: tuple[ResponseAction, ...] = ()
    # Pilihan pengguna sendiri: skenario yang sudah disimulasikan, apa adanya.
    is_current: bool = False
    excluded_reason: str | None = None

    @property
    def eligible(self) -> bool:
        return self.excluded_reason is None

    def as_json(self) -> dict[str, Any]:
        return {
            "situation": self.situation.value,
            "lever": self.lever,
            "value": self.value,
            "label": self.label,
            "parameters": self.parameters,
            "actions": [action.as_json() for action in self.actions],
            "is_current": self.is_current,
            "excluded_reason": self.excluded_reason,
        }


@dataclass(frozen=True)
class CandidateSet:
    lever: LeverSpec
    candidates: list[SweepCandidate]
    # Kandidat layak yang tidak disimulasikan karena batas `max_candidates`.
    omitted: int = 0

    @property
    def truncated(self) -> bool:
        return self.omitted > 0

    @property
    def current(self) -> SweepCandidate:
        return self.candidates[0]

    @property
    def eligible(self) -> list[SweepCandidate]:
        """Kandidat yang disimulasikan, acuan lebih dulu."""
        return [c for c in self.candidates if c.eligible]

    @property
    def excluded(self) -> list[SweepCandidate]:
        return [c for c in self.candidates if not c.eligible]


def build_candidates(
    situation: Situation | str,
    parameters: Mapping[str, Any],
    index: BaselineIndex,
    *,
    max_candidates: int = MAX_CANDIDATES,
) -> CandidateSet:
    """Kandidat sweep untuk skenario `situation` dengan parameter pengguna `parameters`.

    `index` dibangun dari snapshot skenario. `SweepReferenceError` bila parameter pengguna tidak
    valid terhadap snapshot itu.
    """
    if max_candidates < 1:
        raise ValueError("max_candidates paling sedikit 1 (pilihan pengguna)")
    spec = LEVERS[Situation(situation)]
    outcome = validate_parameters(spec.situation, parameters, index)
    if outcome.params is None or not outcome.ok:
        raise SweepReferenceError(outcome.errors)
    reference = outcome.params.model_dump(mode="json")

    current_value = spec.current_value(outcome.params)
    current = SweepCandidate(
        spec.situation,
        spec.lever,
        current_value,
        spec.describe(current_value, index),
        copy.deepcopy(reference),
        is_current=True,
    )
    seen: set[tuple[LeverValue, tuple[ResponseAction, ...]]] = {(current_value, ())}
    eligible: list[SweepCandidate] = []
    excluded: list[SweepCandidate] = []
    for option in spec.options(outcome.params, index):
        key = (option.value, option.actions)
        if key in seen:
            continue
        seen.add(key)
        candidate = _candidate(spec, option, reference, index)
        (eligible if candidate.eligible else excluded).append(candidate)

    room = max_candidates - 1
    return CandidateSet(
        lever=spec,
        candidates=[current, *eligible[:room], *excluded],
        omitted=max(0, len(eligible) - room),
    )


def _candidate(
    spec: LeverSpec, option: LeverOption, reference: dict[str, Any], index: BaselineIndex
) -> SweepCandidate:
    reason = option.excluded_reason
    parameters = copy.deepcopy(reference)
    if spec.kind == LeverKind.PARAMETER:
        if option.actions:
            raise ValueError(f"Lever parameter {spec.lever} tidak membawa respons")
        parameters.update(option.changes)
        parameters[spec.lever] = option.value
        if reason is None:
            outcome = validate_parameters(spec.situation, parameters, index)
            if outcome.params is not None and outcome.ok:
                parameters = outcome.params.model_dump(mode="json")
            else:
                reason = "; ".join(error.message for error in outcome.errors)
    elif option.changes or (not option.actions and reason is None):
        # Parameter skenario tetap; tanpa respons, kandidat ini sama dengan pilihan pengguna.
        raise ValueError(f"Opsi lever pengalihan {spec.lever} harus membawa respons saja")
    return SweepCandidate(
        spec.situation,
        spec.lever,
        option.value,
        option.label or spec.describe(option.value, index),
        parameters,
        option.actions,
        excluded_reason=reason,
    )
