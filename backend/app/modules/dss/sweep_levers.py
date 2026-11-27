"""Lever setiap situasi keputusan untuk DSS per skenario (SCRUM-61, SCRUM-62, SCRUM-64).

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

Opsi lever parameter (SCRUM-62); opsi yang dikecualikan tetap ditampilkan beserta alasannya:

- New Member: tanpa mentor, lalu setiap anggota yang sudah bergabung pada `join_date`.
  Dikecualikan: rotasi keluar yang bertumpuk dengan periode onboarding, atau tidak hadir pada
  setiap hari kerja onboarding.
- Critical Bug: setiap anggota yang sudah bergabung pada tanggal mulai simulasi. Dikecualikan:
  tidak hadir pada setiap hari kerja periode perbaikan.
- Priority Change: peringkat 1 sampai `max_priority_rank`. Peringkat sekarang proyek aktif
  dikecualikan oleh `check_priority_change`.

Rinciannya:

- Periode onboarding adalah `onboarding_working_days(onboarding_weeks)` hari kerja pertama sejak
  `join_date`, sama dengan periode mentoring di kalender kapasitas. Opsi mentor menyimpan
  `mentor_share` skenario pengguna; opsi tanpa mentor mengosongkannya.
- Periode perbaikan adalah `fixing_days` hari kerja pertama (dibulatkan ke atas) sejak tanggal
  mulai simulasi. Mesin menggeser perbaikan melewati hari tidak hadir, jadi anggota yang tidak
  hadir sebagian tetap layak; keterlambatannya terlihat di KPI.
- Kecocokan peran bukan syarat; nilainya dihitung saat penilaian (SCRUM-66). Anggota diurutkan
  menurut kecocokan peran supaya yang paling relevan tidak terpotong batas kandidat: peran utama
  sama, peran sekunder sama (bila cross-role capable), lalu lainnya, masing-masing menurut
  `resource_id`.

Opsi lever pengalihan (SCRUM-64) dibaca dari dunia skenario pengguna (`LeverContext.scenario`,
setelah aturan situasinya), jadi pemilik task, task yang dilepas, dan ID task tambahan sama
dengan yang disimulasikan:

| Situasi | Task yang dialihkan | Mulai | Dikecualikan dari calon |
| --- | --- | --- | --- |
| Member Absence | `affected_task_ids` (lihat catatan) | awal ketidakhadiran | anggota itu |
| Member Rotation or Withdrawal | task yang dilepas anggota itu | `transfer_date` | anggota itu |
| Task Overrun | `task_id` (sisa pekerjaannya) | mulai simulasi | pemilik task |
| Task Blocker | task independen yang belum mulai | mulai simulasi | pemilik task terblokir |
| Mid-sprint Change Request | task tambahan (`CR-001`, ...) | awal periode terdampak | - |

- Tanpa `affected_task_ids`, Member Absence mengalihkan task terbuka anggota itu yang sudah
  berjalan atau dapat dimulai sebelum ketidakhadirannya berakhir. Task yang baru dimulai jauh
  sesudahnya tidak ikut, karena pengalihan berlaku sampai task selesai.
- Task dikelompokkan menurut perannya. Setiap kelompok punya kandidat sendiri, satu per anggota
  yang memegang peran itu (utama, atau sekunder bila cross-role capable), tanpa perkalian antar
  kelompok, sehingga jumlah kandidat tetap linier. Kelompok terbesar lebih dulu; bila ada lebih
  dari satu kelompok, labelnya menyebut perannya.
- Calon sudah bergabung, belum keluar tim, dan masih mengerjakan proyek task-task itu pada
  tanggal mulai pengalihan, sama dengan syarat `sim_actions.ReassignTasks`. Untuk Member
  Absence, calon yang tidak hadir pada setiap hari kerja periode ketidakhadiran dikecualikan.
- Bila tidak ada task yang dapat dialihkan (misalnya Task Blocker tanpa task independen yang
  belum mulai), generator melempar `NoLeverError`; sweep hanya berisi pilihan pengguna beserta
  alasannya.
"""

import math
from collections.abc import Callable, Collection, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, timedelta
from enum import StrEnum
from typing import Any

from app.modules.data.enums import AbsenceType
from app.modules.data.workdays import is_working_day, working_days_between
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.scenario_params import (
    BaselineIndex,
    ChangeRequestParams,
    CriticalBugParams,
    MemberAbsenceParams,
    MemberRotationParams,
    NewMemberParams,
    Params,
    PriorityChangeParams,
    TaskBlockerParams,
    TaskOverrunParams,
    max_priority_rank,
)
from app.modules.simulasi.sim_actions import ReassignTasks, ResponseAction, holds_role
from app.modules.simulasi.sim_world import SimTask, SimWorld, onboarding_working_days
from app.modules.simulasi.snapshot_schemas import AvailabilityRow, ResourceRow

ONE_DAY = timedelta(days=1)


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


@dataclass(frozen=True)
class LeverContext:
    """Masukan generator selain parameter skenario."""

    index: BaselineIndex
    # Tanggal mulai simulasi yang disapu, sama dengan simulasi skenario pengguna.
    start_date: date
    holidays: frozenset[date]
    # Dunia skenario pengguna setelah aturan situasinya; hanya dibaca generator.
    scenario: SimWorld

    def working_days_from(self, start: date, count: int) -> list[date]:
        """`count` hari kerja pertama pada atau sesudah `start`."""
        days: list[date] = []
        day = start
        while len(days) < count:
            if is_working_day(day, self.holidays):
                days.append(day)
            day += ONE_DAY
        return days


# Menerima parameter skenario pengguna yang sudah lolos validasi. Opsi yang paling relevan
# disebut lebih dulu: bila jumlah kandidat dibatasi, opsi di akhir daftar yang terpotong.
OptionGenerator = Callable[[Any, LeverContext], Iterable[LeverOption]]


class NoLeverError(Exception):
    """Skenario ini tidak punya nilai lever lain untuk dicoba; pesannya adalah alasannya."""


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


# --- lever parameter (SCRUM-62) ----------------------------------------------------------------


def mentor_options(p: NewMemberParams, context: LeverContext) -> Iterator[LeverOption]:
    """Tanpa mentor, lalu setiap anggota yang sudah bergabung saat anggota baru masuk."""
    yield LeverOption(None, changes={"mentor_share": None})
    onboarding = context.working_days_from(p.join_date, onboarding_working_days(p.onboarding_weeks))
    period = _period(onboarding[0], onboarding[-1])
    joined = [r for r in context.index.resources.values() if r.join_date <= p.join_date]
    for resource in _by_role(joined, {p.role_id}):
        rows = _absences(context, resource.resource_id)
        rotation = next(
            (
                row
                for row in rows
                if row.absence_type == AbsenceType.ROTATION_OUT and _overlaps(row, onboarding)
            ),
            None,
        )
        reason = None
        if rotation is not None:
            reason = (
                f"Rotasi keluar {_period(rotation.date_start, rotation.date_end)}, bertumpuk "
                f"dengan onboarding {period}"
            )
        elif covering := _absent_throughout(rows, onboarding):
            reason = f"Tidak hadir sepanjang onboarding {period}: {_listing(covering)}"
        yield LeverOption(resource.resource_id, excluded_reason=reason)


def fixer_options(p: CriticalBugParams, context: LeverContext) -> Iterator[LeverOption]:
    """Setiap anggota yang sudah bergabung pada tanggal mulai simulasi."""
    fixing = context.working_days_from(context.start_date, math.ceil(p.fixing_days - 1e-9))
    period = _period(fixing[0], fixing[-1])
    roles = {
        context.index.tasks[task_id].required_role_id
        for task_id in p.affected_task_ids
        if task_id in context.index.tasks
    }
    active = [r for r in context.index.resources.values() if r.join_date <= context.start_date]
    for resource in _by_role(active, roles):
        reason = None
        if covering := _absent_throughout(_absences(context, resource.resource_id), fixing):
            reason = f"Tidak hadir sepanjang perbaikan {period}: {_listing(covering)}"
        yield LeverOption(resource.resource_id, excluded_reason=reason)


def rank_options(p: PriorityChangeParams, context: LeverContext) -> Iterator[LeverOption]:
    """Setiap peringkat yang dapat diberikan ke proyeknya, dari yang tertinggi."""
    project = context.index.projects[p.project_id]
    for rank in range(1, max_priority_rank(project, context.index) + 1):
        yield LeverOption(rank)


# --- lever pengalihan (SCRUM-64) ---------------------------------------------------------------


def absence_options(p: MemberAbsenceParams, context: LeverContext) -> Iterator[LeverOption]:
    """Pengganti untuk task anggota yang tidak hadir, mulai awal ketidakhadirannya."""
    w = context.scenario
    period = p.absence_period
    if p.affected_task_ids:
        tasks = [w.tasks[task_id] for task_id in p.affected_task_ids]
    else:
        tasks = [
            t
            for t in w.tasks.values()
            if t.owner == p.resource_id and (t.started or w.earliest(t) <= period.end)
        ]
    tasks = _open(w, tasks)
    if not tasks:
        raise NoLeverError(f"Tidak ada task terbuka {p.resource_id} yang terdampak ketidakhadiran")
    first = max(period.start, context.start_date)
    days = working_days_between(first, period.end, context.holidays) if first <= period.end else []
    yield from _reassignments(
        context,
        tasks,
        period.start,
        exclude={p.resource_id},
        absence_days=days,
        absence_label=f"periode ketidakhadiran {p.resource_id}",
    )


def rotation_options(p: MemberRotationParams, context: LeverContext) -> Iterator[LeverOption]:
    """Pengganti untuk task yang dilepas anggota yang pindah atau keluar, mulai tanggal pindah."""
    w = context.scenario
    released = [
        t
        for t in w.tasks.values()
        if any(r.on == p.transfer_date and r.resource_id == p.resource_id for r in t.releases)
    ]
    tasks = _open(w, released)
    if not tasks:
        raise NoLeverError(
            f"Tidak ada task yang dilepas {p.resource_id} pada {p.transfer_date.isoformat()}"
        )
    yield from _reassignments(context, tasks, p.transfer_date, exclude={p.resource_id})


def overrun_options(p: TaskOverrunParams, context: LeverContext) -> Iterator[LeverOption]:
    """Pengganti untuk sisa pekerjaan task yang overrun, mulai tanggal mulai simulasi."""
    w = context.scenario
    task = w.tasks[p.task_id]
    if not _open(w, [task]):
        raise NoLeverError(f"{task.task_id} tidak dijadwalkan ({task.status})")
    owner = {task.owner} if task.owner else set()
    yield from _reassignments(context, [task], context.start_date, exclude=owner)


def blocker_options(p: TaskBlockerParams, context: LeverContext) -> Iterator[LeverOption]:
    """Anggota lain yang mengambil task independen selama blokir, mulai tanggal mulai simulasi."""
    w = context.scenario
    independent = _open(w, [w.tasks[task_id] for task_id in p.independent_task_ids])
    tasks = [t for t in independent if not t.started]
    if not tasks:
        raise NoLeverError("Tidak ada task independen yang belum mulai untuk dialihkan")
    owner = w.tasks[p.task_id].owner
    yield from _reassignments(
        context, tasks, context.start_date, exclude={owner} if owner else set()
    )


def change_request_options(p: ChangeRequestParams, context: LeverContext) -> Iterator[LeverOption]:
    """Pemilik task tambahan change request, mulai awal periode terdampak."""
    w = context.scenario
    tasks = _open(w, [t for t in w.tasks.values() if t.added and t.project_id == p.project_id])
    if not tasks:
        raise NoLeverError("Change request ini tidak menambah task")
    yield from _reassignments(context, tasks, p.affected_period.start, exclude=set())


def _reassignments(
    context: LeverContext,
    tasks: list[SimTask],
    on: date,
    *,
    exclude: Collection[str],
    absence_days: list[date] | None = None,
    absence_label: str = "",
) -> Iterator[LeverOption]:
    """Satu opsi per anggota yang dapat mengambil setiap kelompok peran `tasks` mulai `on`."""
    w = context.scenario
    groups: dict[str, list[SimTask]] = {}
    for task in sorted(tasks, key=lambda t: t.task_id):
        groups.setdefault(task.required_role_id, []).append(task)
    for role, group in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0])):
        task_ids = tuple(t.task_id for t in group)
        projects = {t.project_id for t in group}
        takers = sorted(
            (
                r
                for r in w.resources.values()
                if r.resource_id not in exclude
                and holds_role(r, role)
                and r.join_date <= on
                and (r.leave_date is None or r.leave_date > on)
                and all(r.works_on(project_id, on) for project_id in projects)
            ),
            key=lambda r: (r.primary_role_id != role, r.resource_id),
        )
        for resource in takers:
            reason = None
            rows = _absences(context, resource.resource_id)
            if absence_days and (covering := _absent_throughout(rows, absence_days)):
                period = _period(absence_days[0], absence_days[-1])
                reason = f"Tidak hadir sepanjang {absence_label} {period}: {_listing(covering)}"
            label = f"{resource.name} ({resource.resource_id}) untuk {role}"
            yield LeverOption(
                resource.resource_id,
                actions=(ReassignTasks(task_ids, resource.resource_id, on),),
                excluded_reason=reason,
                label=label if len(groups) > 1 else None,
            )


def _open(w: SimWorld, tasks: Iterable[SimTask]) -> list[SimTask]:
    """Task yang masih dijadwalkan (belum selesai, tidak dibatalkan atau on hold), tanpa ganda."""
    return [t for t in {t.task_id: t for t in tasks}.values() if w.schedulable(t)]


def _by_role(resources: Iterable[ResourceRow], role_ids: Collection[str]) -> list[ResourceRow]:
    """Peran utama cocok, lalu peran sekunder cocok (cross-role capable), lalu lainnya."""

    def order(resource: ResourceRow) -> tuple[int, str]:
        if resource.primary_role_id in role_ids:
            return 0, resource.resource_id
        secondary = (resource.secondary_role_ids or "").split(";")
        if resource.cross_role_capable and any(role in role_ids for role in secondary):
            return 1, resource.resource_id
        return 2, resource.resource_id

    return sorted(resources, key=order)


def _absences(context: LeverContext, resource_id: str) -> list[AvailabilityRow]:
    """Ketidakhadiran D-09 anggota; rotation_in adalah kedatangan, bukan ketidakhadiran."""
    return [
        row
        for row in context.index.availability_by_resource.get(resource_id, [])
        if row.absence_type != AbsenceType.ROTATION_IN
    ]


def _overlaps(row: AvailabilityRow, days: list[date]) -> bool:
    return row.date_start <= days[-1] and days[0] <= row.date_end


def _absent_throughout(rows: list[AvailabilityRow], days: list[date]) -> list[AvailabilityRow]:
    """Ketidakhadiran yang bersama-sama menutup setiap hari di `days`; kosong bila ada hari
    yang tidak tertutup."""
    covering = [row for row in rows if _overlaps(row, days)]
    if all(any(row.date_start <= day <= row.date_end for row in covering) for day in days):
        return covering
    return []


def _listing(rows: list[AvailabilityRow]) -> str:
    return "; ".join(
        f"{row.absence_type.value} {_period(row.date_start, row.date_end)}" for row in rows
    )


def _period(start: date, end: date) -> str:
    return f"{start.isoformat()} s.d. {end.isoformat()}"


LEVERS: dict[Situation, LeverSpec] = {
    spec.situation: spec
    for spec in (
        LeverSpec(
            Situation.CHANGE_REQUEST,
            "added_task_owner_id",
            "Pemilik task tambahan",
            LeverKind.REASSIGNMENT,
            change_request_options,
        ),
        LeverSpec(
            Situation.MEMBER_ABSENCE,
            "replacement_resource_id",
            "Pengganti",
            LeverKind.REASSIGNMENT,
            absence_options,
        ),
        LeverSpec(
            Situation.NEW_MEMBER,
            "mentor_resource_id",
            "Mentor",
            LeverKind.PARAMETER,
            mentor_options,
            none_label="Tanpa mentor",
        ),
        LeverSpec(
            Situation.MEMBER_ROTATION,
            "replacement_resource_id",
            "Pengganti",
            LeverKind.REASSIGNMENT,
            rotation_options,
        ),
        LeverSpec(
            Situation.TASK_OVERRUN,
            "replacement_resource_id",
            "Pengganti",
            LeverKind.REASSIGNMENT,
            overrun_options,
        ),
        LeverSpec(
            Situation.TASK_BLOCKER,
            "independent_task_owner_id",
            "Pengambil task independen",
            LeverKind.REASSIGNMENT,
            blocker_options,
        ),
        LeverSpec(
            Situation.PRIORITY_CHANGE,
            "new_priority_rank",
            "Peringkat prioritas",
            LeverKind.PARAMETER,
            rank_options,
        ),
        LeverSpec(
            Situation.CRITICAL_BUG,
            "resource_id",
            "Perbaikan oleh",
            LeverKind.PARAMETER,
            fixer_options,
        ),
    )
}
