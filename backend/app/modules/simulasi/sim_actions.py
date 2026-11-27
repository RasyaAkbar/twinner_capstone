"""Respons keputusan yang diterapkan di atas skenario (DSS per skenario, SCRUM-61).

Skenario menggambarkan kejadiannya (anggota cuti, task overrun, ...). Respons adalah keputusan
untuk menanggapinya, misalnya memindahkan task ke anggota lain. Mesin simulasi tidak pernah
mengalihkan task sendiri karena pengalihan adalah keputusan (docs/SIMULATION_ENGINE.md,
"Ketidakhadiran"); DSS mencoba setiap respons yang mungkin sebagai kandidat lalu
menyimulasikannya.

Modul ini hanya berisi bentuk datanya. Mesin simulasi menerapkannya pada salinan skenario mulai
SCRUM-63.
"""

from dataclasses import dataclass
from datetime import date
from typing import Any


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
