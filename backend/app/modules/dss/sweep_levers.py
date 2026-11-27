"""Lever setiap situasi keputusan untuk DSS per skenario (SCRUM-61).

DSS per skenario mengulang situasi skenario yang sudah disimulasikan untuk setiap nilai yang
mungkin dari satu keputusan, yaitu lever-nya, lalu memeringkat hasilnya (§IV.2.4, FR-07).
Contohnya, untuk skenario New Member setiap anggota yang dapat menjadi mentor dicoba satu per
satu.

Ada dua jenis lever:

- `parameter`: keputusannya sudah menjadi parameter skenario. Setiap kandidat adalah parameter
  skenario pengguna dengan field lever diganti, lalu divalidasi ulang terhadap snapshot.
- `reassignment`: keputusannya berupa pemindahan task ke anggota lain. Parameter skenario tidak
  berubah; kandidat membawa respons `ReassignTasks` yang diterapkan mesin di atas skenario.

| Situasi | Lever | Jenis |
| --- | --- | --- |
| Mid-sprint Change Request | pemilik task tambahan, per peran | reassignment |
| Member Absence | pengganti untuk task terdampak | reassignment |
| New Member | `mentor_resource_id`, termasuk tanpa mentor | parameter |
| Member Rotation or Withdrawal | pengganti untuk task yang dilepas | reassignment |
| Task Overrun | pengganti untuk sisa pekerjaan | reassignment |
| Task Blocker | anggota yang mengambil task independen | reassignment |
| Priority Change | `new_priority_rank` | parameter |
| Critical Bug | `resource_id`, anggota yang memperbaiki | parameter |

Generator opsi lever parameter dibuat di SCRUM-62 dan lever pengalihan di SCRUM-64. Sampai saat
itu generatornya belum mengusulkan opsi, sehingga sweep hanya berisi pilihan pengguna sendiri.
"""

from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import BaselineIndex, Params
from app.modules.simulasi.sim_actions import ResponseAction


class LeverKind(StrEnum):
    PARAMETER = "parameter"
    REASSIGNMENT = "reassignment"


# Nilai lever: ID anggota, peringkat prioritas, atau kosong (tanpa mentor, tanpa pengalihan).
LeverValue = str | int | None


@dataclass(frozen=True)
class LeverOption:
    """Satu nilai lever yang diusulkan generator.

    - `changes`: field parameter lain yang ikut diganti bersama lever parameter, misalnya
      `mentor_share` dikosongkan untuk opsi tanpa mentor.
    - `actions`: respons yang dicoba, hanya untuk lever pengalihan.
    - `excluded_reason`: diisi bila generator menilai opsi ini tidak memenuhi syarat (misalnya
      anggota cuti sepanjang periode). Kandidatnya tetap ditampilkan sebagai dikecualikan.
    - `label`: kosong berarti label bawaan lever (`LeverSpec.describe`).
    """

    value: LeverValue
    changes: Mapping[str, Any] = field(default_factory=dict)
    actions: tuple[ResponseAction, ...] = ()
    excluded_reason: str | None = None
    label: str | None = None


# Menerima parameter skenario pengguna yang sudah lolos validasi. Opsi yang paling relevan
# disebut lebih dulu: bila jumlah kandidat dibatasi, opsi di akhir daftar yang terpotong.
OptionGenerator = Callable[[Params, BaselineIndex], Iterable[LeverOption]]


@dataclass(frozen=True)
class LeverSpec:
    situation: Situation
    # Nama lever; untuk lever parameter sekaligus nama field parameternya.
    lever: str
    label: str
    kind: LeverKind
    options: OptionGenerator
    # Label nilai kosong: pilihan tanpa mentor atau skenario tanpa pengalihan.
    none_label: str = "Tanpa pengalihan"

    def current_value(self, params: Params) -> LeverValue:
        """Nilai lever pada skenario pengguna; lever pengalihan selalu tanpa pengalihan."""
        if self.kind == LeverKind.PARAMETER:
            return getattr(params, self.lever)
        return None

    def describe(self, value: LeverValue, index: BaselineIndex) -> str:
        if value is None:
            return self.none_label
        if isinstance(value, str):
            resource = index.resources.get(value)
            if resource is not None:
                return f"{resource.resource_name} ({value})"
        return f"{self.label} {value}"


def _pending(params: Params, index: BaselineIndex) -> Iterable[LeverOption]:
    """Belum ada opsi: generator situasi ini dibuat di SCRUM-62 atau SCRUM-64."""
    return ()


LEVERS: dict[Situation, LeverSpec] = {
    spec.situation: spec
    for spec in (
        LeverSpec(
            Situation.CHANGE_REQUEST,
            "added_task_owner_id",
            "Pemilik task tambahan",
            LeverKind.REASSIGNMENT,
            _pending,
        ),
        LeverSpec(
            Situation.MEMBER_ABSENCE,
            "replacement_resource_id",
            "Pengganti",
            LeverKind.REASSIGNMENT,
            _pending,
        ),
        LeverSpec(
            Situation.NEW_MEMBER,
            "mentor_resource_id",
            "Mentor",
            LeverKind.PARAMETER,
            _pending,
            none_label="Tanpa mentor",
        ),
        LeverSpec(
            Situation.MEMBER_ROTATION,
            "replacement_resource_id",
            "Pengganti",
            LeverKind.REASSIGNMENT,
            _pending,
        ),
        LeverSpec(
            Situation.TASK_OVERRUN,
            "replacement_resource_id",
            "Pengganti",
            LeverKind.REASSIGNMENT,
            _pending,
        ),
        LeverSpec(
            Situation.TASK_BLOCKER,
            "independent_task_owner_id",
            "Pengambil task independen",
            LeverKind.REASSIGNMENT,
            _pending,
        ),
        LeverSpec(
            Situation.PRIORITY_CHANGE,
            "new_priority_rank",
            "Peringkat prioritas",
            LeverKind.PARAMETER,
            _pending,
        ),
        LeverSpec(
            Situation.CRITICAL_BUG,
            "resource_id",
            "Perbaikan oleh",
            LeverKind.PARAMETER,
            _pending,
        ),
    )
}
