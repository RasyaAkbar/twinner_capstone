"""Kalender kapasitas hari kerja per anggota: langkah 3 §IV.2.3 (SCRUM-25).

Untuk setiap anggota dan setiap hari kerja mulai `start_date`, kalender menghitung:

- `availability` (0..1): 0 pada akhir pekan, hari libur (D-11), ketidakhadiran (D-09, terencana
  maupun mendadak), sebelum `join_date`, dan setelah anggota keluar; pecahan selama onboarding
  (naik linier dari kapasitas awal ke 1); 1 selain itu.
- `capacity` = `capacity_reference` peran utama (D-03) x `availability`, dalam satuan beban
  yang sama dengan `load_weight` level kompleksitas (D-04). Ini penyebut utilisasi kapasitas
  harian penjadwal; Resource Load Index (FR-04.3) dihitung terpisah oleh modul Evaluasi.
- `non_project`: bagian hari yang dipakai item D-08 (operasional, tiket, hotfix) dan perbaikan
  bug skenario. Jumlahnya boleh lebih dari 1; kelebihannya terlihat sebagai beban berlebih.
- `mentoring`: bagian hari yang dipakai membimbing anggota baru.
- `free` = `capacity` x (1 - `non_project` - `mentoring`), paling kecil 0: kapasitas yang
  tersisa untuk task proyek.

Kalender tidak bergantung pada effort task, jadi satu kalender dipakai ulang oleh semua
replikasi Monte Carlo dunia yang sama.
"""

import bisect
from dataclasses import dataclass
from datetime import date, timedelta

from app.modules.simulasi.sim_world import NonProjectItem, SimResource, SimWorld

ONE_DAY = timedelta(days=1)
# Batas simulasi: tiga tahun kalender sejak start_date.
MAX_HORIZON_DAYS = 3 * 365
EPS = 1e-9

NOT_JOINED = "not_joined"
LEFT = "left"


@dataclass(frozen=True, slots=True)
class DayCapacity:
    availability: float
    capacity: float
    non_project: float = 0.0
    mentoring: float = 0.0
    # Jenis ketidakhadiran D-09, `not_joined`, atau `left`; None bila tidak absen.
    absence: str | None = None
    # D-09 is_known_at_planning untuk hari absen; None bila tidak absen.
    planned: bool | None = None

    @property
    def free(self) -> float:
        return self.capacity * max(0.0, 1.0 - self.non_project - self.mentoring)

    @property
    def working(self) -> bool:
        return self.capacity > EPS


OFF = DayCapacity(0.0, 0.0)


class CapacityCalendar:
    """Kapasitas setiap anggota pada setiap hari kerja `start_date` .. `end`."""

    def __init__(self, world: SimWorld, end: date | None = None) -> None:
        self.start = world.start_date
        self.end = end or world.start_date + timedelta(days=MAX_HORIZON_DAYS)
        self.holidays = world.holidays
        self.days: list[date] = []
        day = self.start
        while day <= self.end:
            if world.is_working_day(day):
                self.days.append(day)
            day += ONE_DAY
        self.index = {day: i for i, day in enumerate(self.days)}
        self.members: dict[str, list[DayCapacity]] = {}

        absences: dict[str, list] = {}
        for absence in world.absences:
            absences.setdefault(absence.resource_id, []).append(absence)
        items: dict[str, list[NonProjectItem]] = {}
        for item in world.items:
            items.setdefault(item.resource_id, []).append(item)

        availability: dict[str, list[tuple[float, str | None, bool | None]]] = {}
        for resource in world.resources.values():
            availability[resource.resource_id] = self._availability(
                resource, absences.get(resource.resource_id, [])
            )

        mentoring: dict[str, list[float]] = {}
        for mentor in world.mentoring:
            mentee = world.resources.get(mentor.mentee_id)
            if mentee is None or mentee.ramp is None or mentor.mentor_id not in availability:
                continue
            shares = mentoring.setdefault(mentor.mentor_id, [0.0] * len(self.days))
            first = self.first_index(mentee.ramp.start)
            for i in range(first, min(first + mentee.ramp.working_days, len(self.days))):
                shares[i] += mentor.share

        for resource_id, flags in availability.items():
            non_project = [0.0] * len(self.days)
            for item in items.get(resource_id, []):
                self._spread(item, flags, non_project)
            shares = mentoring.get(resource_id)
            reference = world.resources[resource_id].capacity_reference
            row: list[DayCapacity] = []
            for i, (factor, absence, planned) in enumerate(flags):
                if factor <= EPS:
                    row.append(DayCapacity(0.0, 0.0, 0.0, 0.0, absence, planned))
                    continue
                row.append(
                    DayCapacity(
                        availability=factor,
                        capacity=reference * factor,
                        non_project=non_project[i],
                        mentoring=shares[i] if shares else 0.0,
                    )
                )
            self.members[resource_id] = row

    # --- akses -------------------------------------------------------------------------------

    def first_index(self, day: date) -> int:
        """Indeks hari kerja pertama pada atau setelah `day`."""
        return bisect.bisect_left(self.days, day)

    def day(self, resource_id: str, day: date) -> DayCapacity:
        """Kapasitas satu anggota pada satu tanggal; hari libur dan di luar kalender = OFF."""
        i = self.index.get(day)
        row = self.members.get(resource_id)
        if i is None or row is None:
            return OFF
        return row[i]

    def at(self, resource_id: str, i: int) -> DayCapacity:
        return self.members[resource_id][i]

    def free(self, resource_id: str, day: date) -> float:
        return self.day(resource_id, day).free

    def total_free(self, resource_id: str, start: date, end: date) -> float:
        """Jumlah kapasitas bebas (satuan beban) pada hari kerja `start..end`."""
        row = self.members.get(resource_id, [])
        return sum(c.free for c in row[self.first_index(start) : self.first_index(end + ONE_DAY)])

    # --- pembentukan -------------------------------------------------------------------------

    def _availability(
        self, resource: SimResource, absences: list
    ) -> list[tuple[float, str | None, bool | None]]:
        flags: list[tuple[float, str | None, bool | None]] = []
        ramp = resource.ramp
        ramp_first = self.first_index(ramp.start) if ramp is not None else 0
        for i, day in enumerate(self.days):
            if day < resource.join_date:
                flags.append((0.0, NOT_JOINED, None))
                continue
            if resource.leave_date is not None and day >= resource.leave_date:
                flags.append((0.0, LEFT, None))
                continue
            absence = next((a for a in absences if a.start <= day <= a.end), None)
            if absence is not None:
                flags.append((0.0, absence.absence_type, absence.planned))
                continue
            factor = 1.0
            if ramp is not None and i >= ramp_first:
                factor = ramp.factor(i - ramp_first)
            flags.append((factor, None, None))
        return flags

    def _spread(
        self,
        item: NonProjectItem,
        flags: list[tuple[float, str | None, bool | None]],
        non_project: list[float],
    ) -> None:
        first = self.first_index(max(item.start, self.start))
        if item.end is not None:
            last = self.first_index(item.end + ONE_DAY)
            for i in range(first, last):
                if flags[i][0] > EPS:
                    non_project[i] += item.share
            return
        remaining = item.working_days or 0.0
        i = first
        while remaining > EPS and i < len(self.days):
            if flags[i][0] > EPS:
                portion = min(1.0, remaining)
                non_project[i] += item.share * portion
                remaining -= portion
            i += 1
