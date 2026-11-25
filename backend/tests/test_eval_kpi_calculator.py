"""SCRUM-32: KPI evaluasi dari keluaran simulasi (jadwal task dan beban harian anggota).

Dunia kecil dari `sim_fixtures` (kapasitas peran 1.0, bobot CX-M 1.0: satu anggota mengerjakan
satu task pada satu waktu, satu hari effort per hari kerja), dihitung manual.

Minggu 1: Sen 05, Sel 06, Rab 07, Kam 08, Jum 09. Minggu 2: Sen 12 s.d. Jum 16.

CHAIN (SV, CDI, EAR, hari tunggu):
    PRJ-001 target Rab 07.
    TSK-0001 3 hari, RES-001: 05-07 (rencana selesai 07, tepat).
    TSK-0002 2 hari, RES-002, hard <- TSK-0001: siap 08, dikerjakan 08-09 (rencana selesai 06,
        terlambat 3 hari kerja: 07, 08, 09).
    TSK-0003 1 hari, RES-001, soft 0.3 <- TSK-0002: siap 09 (TSK-0002 sudah 1 dari 2 hari),
        dikerjakan 09 (rencana selesai 05, terlambat 4 hari kerja).
    Proyek selesai 09: SV = 2 (08, 09).

LOADS (RLI, WBI, SOI):
    RES-001 TSK-0001..0003 masing-masing 3 hari: 05-07, 08-12, 13-15.
    RES-003 (FE, tanpa task): item operasional 05-06, tiket 08-13.
"""

import pytest

from app.modules.evaluasi.kpi_calculator import (
    Reference,
    SimulationOutput,
    calculate,
    item_days,
    rework_rate,
    schedule_variances,
    waiting_days,
)
from app.modules.evaluasi.kpi_definitions import (
    BLOCKED_RATIO,
    CASCADE_DELAY_IMPACT,
    ESTIMATE_ACCURACY,
    ON_TIME_PROBABILITY,
    OVERLOAD_FLAGS,
    REALLOCATED_RATIO,
    RESOURCE_LOAD_INDEX,
    REWORK_RATE,
    SCHEDULE_VARIANCE,
    SUSTAINED_OVERLOAD,
    WORKLOAD_BALANCE_INDEX,
    EvaluationConfig,
)
from app.modules.simulasi.sim_calendar import CapacityCalendar
from app.modules.simulasi.sim_kpi import member_loads
from app.modules.simulasi.sim_scheduler import TaskState, simulate
from tests.sim_fixtures import (
    absence,
    assignment,
    d,
    dependency,
    project,
    resource,
    task,
    workload,
    world,
)

CONFIG = EvaluationConfig()


def output(sim_world) -> SimulationOutput:
    calendar = CapacityCalendar(sim_world)
    schedule = simulate(sim_world, calendar)
    loads = member_loads(sim_world, schedule, calendar, schedule.end_date)
    return SimulationOutput(sim_world, schedule, loads, calendar, schedule.end_date)


CHAIN = world(
    projects=[project("PRJ-001", target="2026-01-07")],
    resources=[resource("RES-001"), resource("RES-002")],
    tasks=[task("TSK-0001", 3), task("TSK-0002", 2), task("TSK-0003", 1)],
    task_dependencies=[
        dependency("DEP-001", "TSK-0002", "TSK-0001"),
        dependency("DEP-002", "TSK-0003", "TSK-0002", kind="soft", factor=0.3),
    ],
    assignments=[
        assignment("ASG-0001", "TSK-0001", "RES-001"),
        assignment("ASG-0002", "TSK-0002", "RES-002"),
        assignment("ASG-0003", "TSK-0003", "RES-001"),
    ],
)
CHAIN_OUT = output(CHAIN)


def test_chain_schedule_matches_the_hand_calculation():
    tasks = CHAIN_OUT.schedule.tasks
    assert [(tasks[t].start, tasks[t].finish) for t in ("TSK-0001", "TSK-0002", "TSK-0003")] == [
        (d(5), d(7)),
        (d(8), d(9)),
        (d(9), d(9)),
    ]
    assert CHAIN_OUT.until == d(9)


def test_schedule_variance_and_on_time():
    kpis = calculate(CHAIN_OUT, Reference(), CONFIG)
    assert kpis.project_sv == {"PRJ-001": 2}
    assert kpis.values[SCHEDULE_VARIANCE] == 2.0
    assert (kpis.on_time, kpis.values[ON_TIME_PROBABILITY]) == (False, 0.0)


def test_project_with_unscheduled_task_cannot_launch():
    sim_world = world(
        projects=[project("PRJ-001"), project("PRJ-002", rank=2)],
        resources=[resource("RES-001")],
        tasks=[
            task("TSK-0001", 2),
            # Tidak ada anggota dengan ROLE-QA: tidak terjadwal.
            task("TSK-0101", 1, project_id="PRJ-002", role_id="ROLE-QA"),
        ],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    )
    # PRJ-001 selesai Sel 06, target Jum 30: SV = -(07..30) = -18 hari kerja.
    assert schedule_variances(sim_world, simulate(sim_world)) == {"PRJ-001": -18, "PRJ-002": None}
    kpis = calculate(output(sim_world), Reference(), CONFIG)
    assert kpis.values[SCHEDULE_VARIANCE] is None
    assert kpis.on_time is False


def test_cascade_delay_impact():
    kpis = calculate(CHAIN_OUT, Reference(), CONFIG)
    # TSK-0001 tepat waktu; TSK-0002 terlambat 3 hari dan meneruskan 0.3 ke TSK-0003.
    assert kpis.task_delay == {"TSK-0001": 0.0, "TSK-0002": 3.0, "TSK-0003": 4.0}
    assert kpis.cdi == pytest.approx({"TSK-0002": 0.9, "TSK-0003": 0.0})
    assert kpis.values[CASCADE_DELAY_IMPACT] == pytest.approx(0.9)


def test_cascade_delay_factors_are_configurable():
    # Faktor soft 1.0 (bukan 0.3 dari D-06): 3 x 1.0 = 3.
    kpis = calculate(CHAIN_OUT, Reference(), EvaluationConfig(soft_factor=1.0))
    assert kpis.values[CASCADE_DELAY_IMPACT] == pytest.approx(3.0)


def test_estimate_accuracy_uses_the_baseline_estimate():
    kpis = calculate(CHAIN_OUT, Reference(), CONFIG)
    # Durasi 3, 2, 1 hari = estimasi 3, 2, 1.
    assert kpis.values[ESTIMATE_ACCURACY] == 1.0
    # Estimasi baseline TSK-0001 2 hari (skenario menaikkannya menjadi 3): (3 + 2 + 1) / 5.
    kpis = calculate(CHAIN_OUT, Reference(estimates={"TSK-0001": 2.0}), CONFIG)
    assert kpis.values[ESTIMATE_ACCURACY] == pytest.approx(1.2)
    assert kpis.ear_by_complexity == pytest.approx({"CX-M": 1.2})
    assert kpis.ear_by_role == pytest.approx({"ROLE-BE": 1.2})


def test_blocked_and_reallocated_time():
    waits = waiting_days(CHAIN_OUT)
    # RES-002 menunggu TSK-0002 (siap 08) pada 05-07.
    # RES-001 menunggu TSK-0003 (siap 09) pada 05-08, sambil mengerjakan TSK-0001 pada 05-07.
    assert waits == {"RES-002": {d(5), d(6), d(7)}, "RES-001": {d(5), d(6), d(7), d(8)}}
    kpis = calculate(CHAIN_OUT, Reference(), CONFIG)
    # Hari kerja: 2 anggota x 5 hari. Tunggu tanpa pekerjaan: RES-002 3 + RES-001 1 (08).
    # Tunggu yang terisi task lain: RES-001 05-07.
    assert (kpis.working_days, kpis.blocked_days, kpis.reallocated_days) == (10, 4, 3)
    assert kpis.values[BLOCKED_RATIO] == pytest.approx(0.4)
    assert kpis.values[REALLOCATED_RATIO] == pytest.approx(3 / 7, abs=1e-6)


def test_blocker_days_count_as_waiting():
    sim_world = world(
        resources=[resource("RES-001")],
        tasks=[task("TSK-0001", 1), task("TSK-0002", 1, rank=2)],
        assignments=[
            assignment("ASG-0001", "TSK-0001", "RES-001"),
            assignment("ASG-0002", "TSK-0002", "RES-001"),
        ],
    )
    # Task Blocker 2 hari pada TSK-0001: tertahan 05-06, dikerjakan 07. RES-001 mengerjakan
    # TSK-0002 pada 05 lalu menganggur 06.
    sim_world.tasks["TSK-0001"].block_days = 2
    out = output(sim_world)
    assert out.schedule.tasks["TSK-0001"].start == d(7)
    kpis = calculate(out, Reference(), CONFIG)
    assert (kpis.working_days, kpis.blocked_days, kpis.reallocated_days) == (3, 1, 1)
    assert kpis.values[REALLOCATED_RATIO] == 0.5


def test_no_waiting_means_reallocated_ratio_is_not_applicable():
    sim_world = world(
        resources=[resource("RES-001")],
        tasks=[task("TSK-0001", 2)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
    )
    kpis = calculate(output(sim_world), Reference(), CONFIG)
    assert (kpis.values[BLOCKED_RATIO], kpis.values[REALLOCATED_RATIO]) == (0.0, None)


# --- beban mingguan ------------------------------------------------------------------------------


def operational(item_id, resource_id, received, resolved):
    return {
        **workload(item_id, resource_id, received, resolved=resolved),
        "item_type": "operational",
    }


LOADS = world(
    resources=[resource("RES-001"), resource("RES-003", role_id="ROLE-FE")],
    tasks=[task("TSK-0001", 3), task("TSK-0002", 3), task("TSK-0003", 3)],
    assignments=[
        assignment("ASG-0001", "TSK-0001", "RES-001"),
        assignment("ASG-0002", "TSK-0002", "RES-001"),
        assignment("ASG-0003", "TSK-0003", "RES-001"),
    ],
    workload_items=[
        operational("WLI-0001", "RES-003", "2026-01-05", "2026-01-06"),
        workload("WLI-0002", "RES-003", "2026-01-08", resolved="2026-01-13"),
    ],
)
LOADS_OUT = output(LOADS)


def test_weekly_rli_counts_tasks_and_items():
    tasks = LOADS_OUT.schedule.tasks
    assert [(tasks[t].start, tasks[t].finish) for t in ("TSK-0001", "TSK-0002", "TSK-0003")] == [
        (d(5), d(7)),
        (d(8), d(12)),
        (d(13), d(15)),
    ]
    kpis = calculate(LOADS_OUT, Reference(), CONFIG)
    weeks = {
        rid: {week: (w.tasks, w.operational, w.tickets, w.rli) for week, w in member.items()}
        for rid, member in kpis.member_weeks.items()
    }
    assert weeks == {
        # Minggu 1: TSK-0001, TSK-0002 -> 2 / 1.0. Minggu 2: TSK-0002, TSK-0003 -> 2.
        "RES-001": {d(5): (2, 0, 0, 2.0), d(12): (2, 0, 0, 2.0)},
        # Minggu 1: 0.5 x 1 operasional + 0.3 x 1 tiket = 0.8. Minggu 2: tiket 12-13 = 0.3.
        "RES-003": {d(5): (0, 1, 1, 0.8), d(12): (0, 0, 1, 0.3)},
    }


def test_load_kpis_from_weekly_rli():
    kpis = calculate(LOADS_OUT, Reference(), CONFIG)
    assert kpis.values[RESOURCE_LOAD_INDEX] == 2.0
    # RES-001 dua minggu di atas 1.0.
    assert kpis.values[OVERLOAD_FLAGS] == 2.0
    # Minggu 1: [2.0, 0.8] rata-rata 1.4, simpangan baku 0.6 -> 1 - 0.6/1.4 = 0.571429.
    # Minggu 2: [2.0, 0.3] rata-rata 1.15, simpangan baku 0.85 -> 1 - 0.85/1.15 = 0.260870.
    assert kpis.wbi_weeks == pytest.approx({d(5): 1 - 0.6 / 1.4, d(12): 1 - 0.85 / 1.15})
    assert kpis.values[WORKLOAD_BALANCE_INDEX] == pytest.approx(
        (2 - 0.6 / 1.4 - 0.85 / 1.15) / 2, abs=1e-6
    )
    # RES-001: dua minggu berturut-turut dengan ekses 1.0 -> 2 x 1.0 = 2.0.
    assert kpis.soi == {"RES-001": 2.0, "RES-003": 0.0}
    assert kpis.values[SUSTAINED_OVERLOAD] == 2.0


def test_capacity_and_threshold_are_configurable():
    config = EvaluationConfig(capacity_reference=2.0, overload_threshold=0.9)
    kpis = calculate(LOADS_OUT, Reference(), config)
    # RES-001 2 task / 2.0 = 1.0 > 0.9 dua minggu: SOI = 2 x 0.1.
    assert kpis.values[RESOURCE_LOAD_INDEX] == 1.0
    assert kpis.values[OVERLOAD_FLAGS] == 2.0
    assert kpis.values[SUSTAINED_OVERLOAD] == pytest.approx(0.2)


def test_item_days_skip_days_the_member_does_not_work():
    sim_world = world(
        resources=[resource("RES-001")],
        availability=[absence("AVL-001", "RES-001", "2026-01-06", "2026-01-06")],
        workload_items=[workload("WLI-0001", "RES-001", "2026-01-05", resolved="2026-01-07")],
    )
    assert item_days(sim_world.items[0], CapacityCalendar(sim_world)) == [d(5), d(7)]


def test_week_on_leave_has_no_rli():
    sim_world = world(
        resources=[resource("RES-001")],
        tasks=[task("TSK-0001", 6)],
        assignments=[assignment("ASG-0001", "TSK-0001", "RES-001")],
        # Cuti seminggu penuh pada minggu 2.
        availability=[absence("AVL-001", "RES-001", "2026-01-12", "2026-01-16")],
    )
    out = output(sim_world)
    # 05-09 (5 hari), cuti 12-16, 19 (1 hari).
    assert out.schedule.tasks["TSK-0001"].finish == d(19)
    kpis = calculate(out, Reference(), CONFIG)
    assert sorted(kpis.member_weeks["RES-001"]) == [d(5), d(19)]


# --- FR-04.9 -------------------------------------------------------------------------------------


def test_rework_rate_counts_tasks_that_reached_qa():
    sim_world = world(
        resources=[resource("RES-001")],
        tasks=[
            task(
                "TSK-0001", 1, status="done", actual_start="2025-12-01", actual_finish="2025-12-01"
            ),
            task(
                "TSK-0002", 1, status="done", actual_start="2025-12-02", actual_finish="2025-12-02"
            ),
            task("TSK-0003", 1, status="in_qa", actual_start="2025-12-03"),
            task("TSK-0004", 1, status="in_progress", actual_start="2025-12-04"),
            task("TSK-0005", 1),
            task("TSK-0006", 1, status="dropped"),
        ],
    )
    rejects = {"TSK-0001": 2, "TSK-0004": 1, "TSK-0006": 3}
    # Masuk QA: 0001, 0002, 0003 (done/in_qa) dan 0004 (pernah dikembalikan) = 4.
    # Dikembalikan: 0001, 0004 = 2. Task dropped tidak dihitung.
    assert rework_rate(sim_world, rejects) == 0.5
    kpis = calculate(output(sim_world), Reference(qa_rejects=rejects), CONFIG)
    assert kpis.values[REWORK_RATE] == 0.5
    # Change Request mengeluarkan TSK-0001 dari scope: 1 / 3.
    sim_world.tasks["TSK-0001"].removed = True
    assert rework_rate(sim_world, rejects) == pytest.approx(1 / 3, abs=1e-6)


def test_task_states_used_by_the_calculator():
    assert CHAIN_OUT.schedule.tasks["TSK-0003"].state == TaskState.SCHEDULED
