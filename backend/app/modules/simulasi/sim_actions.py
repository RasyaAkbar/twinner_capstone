"""Respons keputusan yang diterapkan di atas skenario (DSS per skenario, SCRUM-61, SCRUM-63).

Skenario menggambarkan kejadiannya (anggota cuti, task overrun, ...). Respons adalah keputusan
untuk menanggapinya, misalnya memindahkan task ke anggota lain. Mesin simulasi tidak pernah
mengalihkan task sendiri karena pengalihan adalah keputusan (docs/SIMULATION_ENGINE.md,
"Ketidakhadiran"); DSS mencoba setiap respons yang mungkin sebagai kandidat lalu
menyimulasikannya.

`apply_actions` menerapkan respons pada salinan dunia hasil `sim_rules.apply_scenario`, jadi
dunia baseline tidak pernah berubah. Respons yang tidak masuk akal (anggota atau task tidak
ada, peran tidak cocok, anggota sudah keluar tim atau dirotasi dari proyek task) ditolak dengan
`ResponseActionError` sebelum respons itu mengubah apa pun; kandidat DSS-nya gagal.

`ReassignTasks` memindahkan task terbuka ke `to_resource_id` mulai `from_date` (sebelum tanggal
mulai simulasi berarti sejak hari pertama). Pemilik baru memegang peran task sebagai peran
utama, atau sebagai peran sekunder bila `cross_role_capable` (D-02). Pada hari itu penjadwal
lebih dulu menjalankan pelepasan task (rotasi, penugasan berakhir), lalu memindahkan task dari
pemilik lama atau dari pool ke pemilik baru; task yang sudah berjalan dilanjutkan dengan sisa
effort-nya. Task yang sudah selesai, dibatalkan, atau on hold dilewati dan dicatat. Ketidakhadiran
pemilik baru berlaku seperti biasa: task menunggunya.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from typing import Any

from app.modules.simulasi.sim_rules import AppliedScenario
from app.modules.simulasi.sim_world import SimResource, SimTask, SimWorld, Transfer


@dataclass(frozen=True)
class ReassignTasks:
    """Pindahkan task terbuka ke anggota lain mulai `from_date`."""

    task_ids: tuple[str, ...]
    to_resource_id: str
    from_date: date

    def as_json(self) -> dict[str, Any]:
        return {
            "type": "reassign_tasks",
            "task_ids": list(self.task_ids),
            "to_resource_id": self.to_resource_id,
            "from_date": self.from_date.isoformat(),
        }


# Jenis respons yang dikenal mesin; bertambah bila DSS mendapat jenis lever baru.
ResponseAction = ReassignTasks


class ResponseActionError(ValueError):
    """Respons tidak dapat diterapkan pada skenario ini."""


def apply_actions(applied: AppliedScenario, actions: Iterable[ResponseAction]) -> None:
    """Terapkan `actions` berurutan pada `applied.world` (salinan skenario)."""
    for action in actions:
        _reassign(applied, action)


def _reassign(applied: AppliedScenario, action: ReassignTasks) -> None:
    w = applied.world
    resource = w.resources.get(action.to_resource_id)
    if resource is None:
        raise ResponseActionError(f"Anggota {action.to_resource_id} tidak ada di baseline")
    if resource.leave_date is not None and resource.leave_date <= action.from_date:
        raise ResponseActionError(
            f"{resource.resource_id} sudah keluar tim pada {resource.leave_date.isoformat()}"
        )

    moving: list[SimTask] = []
    skipped: list[str] = []
    for task_id in dict.fromkeys(action.task_ids):
        task = w.tasks.get(task_id)
        if task is None:
            raise ResponseActionError(f"Task {task_id} tidak ada di skenario")
        if not w.schedulable(task):
            skipped.append(f"{task_id} ({task.status})")
            continue
        _check_owner(w, task, resource, action.from_date)
        # Sudah milik anggota itu dan tidak akan dilepas: tidak ada yang dipindahkan.
        if task.owner == resource.resource_id and not task.releases:
            continue
        moving.append(task)

    for task in moving:
        task.transfers.append(Transfer(action.from_date, resource.resource_id))
    applied.targets.add_tasks(w, [t.task_id for t in moving])
    if moving:
        applied.targets.resources.add(resource.resource_id)
        applied.notes.append(
            f"Respons: {', '.join(t.task_id for t in moving)} dialihkan ke "
            f"{resource.resource_id} mulai {action.from_date.isoformat()}; task yang sudah "
            "berjalan dilanjutkan dengan sisa effort-nya."
        )
    if skipped:
        applied.notes.append(
            f"Respons: {', '.join(skipped)} tidak dialihkan karena tidak lagi dijadwalkan."
        )


def _check_owner(w: SimWorld, task: SimTask, resource: SimResource, on: date) -> None:
    """Pemilik baru memegang peran task (utama, atau sekunder bila cross-role capable) dan masih
    mengerjakan proyek task pada `on`."""
    role = task.required_role_id
    if resource.primary_role_id != role and not (
        resource.cross_role_capable and role in resource.secondary_role_ids
    ):
        raise ResponseActionError(
            f"{resource.resource_id} tidak memegang peran {role} untuk {task.task_id}"
        )
    if not resource.works_on(task.project_id, on):
        raise ResponseActionError(
            f"{resource.resource_id} tidak lagi mengerjakan proyek {task.project_id} mulai "
            f"{resource.excluded_projects[task.project_id].isoformat()}"
        )
