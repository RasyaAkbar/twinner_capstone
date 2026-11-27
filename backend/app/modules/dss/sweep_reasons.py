"""Alasan setiap kandidat sweep DSS dalam Bahasa Indonesia (SCRUM-67, FR-08).

Alasan disusun dari nilai kriteria (`sweep_scoring`) dan perbandingan KPI kandidat, supaya
pemangku kepentingan dapat menilai mengapa satu pilihan berada di atas pilihan lain. Untuk
kandidat yang berhasil disimulasikan, paling banyak tiga kalimat:

1. Anggota kandidat (bila ada): kecocokan peran (dengan senioritas untuk mentor) dan kapasitas
   kosongnya selama periode ia dibutuhkan.
2. Apa yang dilakukan kandidat, dengan kalimat per situasi (`_ACTION`), dan dampaknya
   dibandingkan pilihan pengguna: jadwal peluncuran (Schedule Variance) lalu jumlah KPI yang
   membaik dan memburuk.
3. Dampak pada pekerjaan anggota itu sendiri, dibandingkan keadaan acuan penilaian: pilihan
   pengguna untuk lever pengalihan, baseline untuk lever parameter.

Pilihan pengguna sendiri dijelaskan terhadap baseline. Kandidat yang dikecualikan atau gagal
disimulasikan hanya membawa alasannya.

Contoh (Member Absence):

    Budi (RES-002) sesuai peran Backend Developer; kapasitas kosongnya 4 dari 4 hari kerja sejak
    pengalihan sampai task selesai (2026-01-05 s.d. 2026-01-08). Bila TSK-0001 dialihkan ke Budi
    (RES-002) mulai 2026-01-05, peluncuran 1 hari kerja sebelum target, dibandingkan terlambat
    4 hari kerja dari target pada pilihan Anda; secara keseluruhan 3 KPI membaik dan 0 KPI
    memburuk. Dampak pada pekerjaan Budi (RES-002) sendiri dibandingkan pilihan Anda: RLI
    mingguan tertingginya naik 1.
"""

from collections.abc import Callable, Sequence
from typing import Any

from app.modules.dss.sweep_candidates import SweepCandidate
from app.modules.dss.sweep_levers import LeverKind
from app.modules.dss.sweep_runner import CandidateRun, CandidateStatus, SweepRun
from app.modules.dss.sweep_scoring import Criterion, Ranking, ScoredCandidate
from app.modules.evaluasi.kpi_definitions import SCHEDULE_VARIANCE, KpiStatus
from app.modules.simulasi.scenario_models import Situation
from app.modules.simulasi.sim_calendar import EPS

# Task yang disebut satu per satu dalam alasan; sisanya diringkas.
MAX_LISTED_TASKS = 3


def explain(sweep: SweepRun, ranking: Ranking) -> list[str]:
    """Satu alasan per kandidat, urutannya sama dengan `sweep.results`."""
    scored = {id(candidate.result): candidate for candidate in ranking.candidates}
    writer = _Writer(sweep)
    reasons: list[str] = []
    for result in sweep.results:
        if result.status == CandidateStatus.EXCLUDED:
            reasons.append(_sentence(f"Tidak disimulasikan: {_lower_first(result.error)}"))
        elif result.status == CandidateStatus.FAILED:
            reasons.append(_sentence(f"Gagal disimulasikan: {_lower_first(result.error)}"))
        else:
            reasons.append(writer.reason(scored[id(result)]))
    return reasons


# --- kalimat per situasi -----------------------------------------------------------------------


def _tasks(candidate: SweepCandidate) -> str:
    task_ids = [task_id for action in candidate.actions for task_id in action.task_ids]
    if len(task_ids) <= MAX_LISTED_TASKS:
        return _join(task_ids)
    shown = ", ".join(task_ids[:MAX_LISTED_TASKS])
    return f"{shown}, dan {len(task_ids) - MAX_LISTED_TASKS} task lain"


def _from(candidate: SweepCandidate) -> str:
    return min(action.from_date for action in candidate.actions).isoformat()


# Kalimat kandidat selain pilihan pengguna: (kandidat, nama anggota) -> awal kalimat.
_ACTION: dict[Situation, Callable[[SweepCandidate, str], str]] = {
    Situation.CHANGE_REQUEST: lambda c, who: (
        f"Bila task tambahan {_tasks(c)} dikerjakan {who} mulai {_from(c)}"
    ),
    Situation.MEMBER_ABSENCE: lambda c, who: (
        f"Bila {_tasks(c)} dialihkan ke {who} mulai {_from(c)}"
    ),
    Situation.NEW_MEMBER: lambda c, who: (
        f"Dengan {who} sebagai mentor" if c.value is not None else "Tanpa mentor"
    ),
    Situation.MEMBER_ROTATION: lambda c, who: (
        f"Bila task yang dilepas ({_tasks(c)}) dialihkan ke {who} mulai {_from(c)}"
    ),
    Situation.TASK_OVERRUN: lambda c, who: f"Bila sisa pekerjaan {_tasks(c)} dialihkan ke {who}",
    Situation.TASK_BLOCKER: lambda c, who: (
        f"Bila {who} mengambil task independen {_tasks(c)} selama blokir"
    ),
    Situation.PRIORITY_CHANGE: lambda c, who: (
        f"Dengan {c.parameters['project_id']} di peringkat prioritas {c.value}"
    ),
    Situation.CRITICAL_BUG: lambda c, who: f"Bila {who} yang memperbaiki bug",
}

# Pilihan pengguna: "Ini skenario pilihan Anda, ...".
_CHOICE: dict[Situation, Callable[[SweepCandidate, str], str]] = {
    Situation.NEW_MEMBER: lambda c, who: (
        f"dengan {who} sebagai mentor" if c.value is not None else "tanpa mentor"
    ),
    Situation.PRIORITY_CHANGE: lambda c, who: (
        f"dengan {c.parameters['project_id']} di peringkat prioritas {c.value}"
    ),
    Situation.CRITICAL_BUG: lambda c, who: f"dengan {who} memperbaiki bug",
}

_PERIOD = {
    Situation.NEW_MEMBER: "selama onboarding",
    Situation.CRITICAL_BUG: "selama periode perbaikan",
}


# --- penyusun alasan ---------------------------------------------------------------------------


class _Writer:
    def __init__(self, sweep: SweepRun) -> None:
        lever = sweep.candidates.lever
        self.situation = lever.situation
        assert sweep.current.side is not None
        self.world = sweep.current.side.world
        self.reference = "pilihan Anda" if lever.kind == LeverKind.REASSIGNMENT else "baseline"

    def reason(self, scored: ScoredCandidate) -> str:
        candidate = scored.result.candidate
        value = candidate.value
        member = self.world.resources.get(value) if isinstance(value, str) else None
        who = f"{member.name} ({member.resource_id})" if member else candidate.label
        sentences: list[str] = []
        if candidate.is_current:
            choice = _CHOICE.get(self.situation, lambda c, w: "tanpa pengalihan task")
            sentences.append(f"Ini skenario pilihan Anda, {choice(candidate, who)}.")
        if member is not None:
            sentences.append(self._member(scored, who))
        if candidate.is_current:
            sentences.append(self._against_baseline(scored.result))
        else:
            sentences.append(self._effect(scored, who))
        if member is not None:
            sentences.append(self._own_work(scored, who))
        return " ".join(sentence for sentence in sentences if sentence)

    def _member(self, scored: ScoredCandidate, who: str) -> str:
        role = scored.criteria[Criterion.ROLE_MATCH]
        capacity = scored.criteria[Criterion.AVAILABLE_CAPACITY]
        text = who
        if role.score is not None:
            text = f"{who} {self._role(role.facts)}"
        if capacity.score is not None:
            facts = capacity.facts
            period = _PERIOD.get(self.situation, "sejak pengalihan sampai task selesai")
            text += (
                f"{';' if role.score is not None else ':'} kapasitas kosongnya "
                f"{_num(facts['available_days'])} dari {facts['working_days']} hari kerja {period} "
                f"({facts['start']} s.d. {facts['end']})"
            )
        return f"{text}." if text != who else ""

    def _role(self, facts: dict[str, Any]) -> str:
        names = _join([self._role_name(role) for role in facts["roles"]], "atau")
        seniority = f" ({facts['seniority']})" if facts.get("seniority") else ""
        if facts["match"] == "primary":
            return f"sesuai peran {names}{seniority}"
        if facts["match"] == "secondary":
            return f"memegang {names} sebagai peran sekunder{seniority}"
        return f"tidak memegang peran {names}{seniority}"

    def _role_name(self, role_id: str) -> str:
        role = self.world.roles.get(role_id)
        return role.name if role is not None else role_id

    def _effect(self, scored: ScoredCandidate, who: str) -> str:
        action = _ACTION[self.situation](scored.result.candidate, who)
        items = scored.result.vs_current
        schedule = _schedule(items, "sama dengan pilihan Anda", "pada pilihan Anda")
        facts = scored.criteria[Criterion.KPI_EFFECT].facts
        return f"{action}, {schedule}; {_counts(facts['better'], facts['worse'])}."

    def _against_baseline(self, result: CandidateRun) -> str:
        items = result.vs_baseline
        better = sum(1 for item in items if item.status == KpiStatus.BETTER)
        worse = sum(1 for item in items if item.status == KpiStatus.WORSE)
        schedule = _schedule(items, "tidak berubah", "pada baseline")
        return f"Dibandingkan baseline, {schedule}; {_counts(better, worse)}."

    def _own_work(self, scored: ScoredCandidate, who: str) -> str:
        work = scored.criteria[Criterion.CURRENT_WORK]
        if work.score is None:
            return ""
        facts = work.facts
        parts: list[str] = []
        if facts["own_delay_days"] > 0:
            parts.append(f"task miliknya mundur total {facts['own_delay_days']:g} hari kerja")
        overloaded = facts["overloaded_weeks"]
        if overloaded:
            change = "bertambah" if overloaded > 0 else "berkurang"
            parts.append(f"minggu berbeban berlebihnya {change} {abs(overloaded):g}")
        peak = facts["peak_rli"]
        if abs(peak) > EPS:
            change = "naik" if peak > 0 else "turun"
            parts.append(f"RLI mingguan tertingginya {change} {_num(abs(peak))}")
        if not parts:
            return f"Pekerjaan {who} sendiri tidak terdampak dibandingkan {self.reference}."
        return f"Dampak pada pekerjaan {who} sendiri dibandingkan {self.reference}: {_join(parts)}."


# --- bantuan -----------------------------------------------------------------------------------


def _schedule(items: Sequence[Any], unchanged: str, other_side: str) -> str:
    """Jadwal peluncuran dari Schedule Variance: sisi kandidat dibandingkan sisi pembanding."""
    item = next((i for i in items if i.key == SCHEDULE_VARIANCE), None)
    if item is None or item.scenario == item.baseline:
        return f"jadwal peluncuran {unchanged}"
    return (
        f"peluncuran {_launch(item.scenario)}, dibandingkan {_launch(item.baseline)} {other_side}"
    )


def _launch(sv: float | None) -> str:
    if sv is None:
        return "tidak dapat dihitung karena ada task yang tidak terjadwal"
    if sv > EPS:
        return f"terlambat {_num(sv)} hari kerja dari target"
    if sv < -EPS:
        return f"{_num(-sv)} hari kerja sebelum target"
    return "tepat pada target"


def _num(value: float) -> str:
    """Angka untuk kalimat: paling banyak dua desimal (median replikasi bisa pecahan)."""
    return f"{round(value, 2):g}"


def _counts(better: int, worse: int) -> str:
    if not better and not worse:
        return "KPI lain tidak berubah"
    return f"secara keseluruhan {better} KPI membaik dan {worse} KPI memburuk"


def _join(items: Sequence[str], word: str = "dan") -> str:
    if len(items) <= 1:
        return "".join(items)
    if len(items) == 2:
        return f"{items[0]} {word} {items[1]}"
    return f"{', '.join(items[:-1])}, {word} {items[-1]}"


def _sentence(text: str) -> str:
    text = text.rstrip()
    return text if text.endswith((".", "!", "?")) else f"{text}."


def _lower_first(text: str | None) -> str:
    """Huruf pertama pesan dikecilkan supaya menyambung setelah titik dua; ID tetap utuh."""
    if not text:
        return "tanpa keterangan"
    first = text.split(" ", 1)[0]
    return text if first.isupper() else text[0].lower() + text[1:]
