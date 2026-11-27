"""Job latar belakang sweep DSS (SCRUM-69).

Setelah Run and save berhasil, `simulation_router` membuat sweep `pending` dan menyerahkannya ke
penjadwal sweep, sehingga respons Run and save tidak menunggu sweep. Job (`run_sweep_job`):

1. Melewati sweep yang tidak lagi `pending`, dan membatalkan (`cancelled`) sweep yang sudah
   digantikan sweep lebih baru untuk skenario yang sama.
2. `running`: menyusun kandidat (`build_candidates`) dari simulasi tersimpan, mensimulasikannya
   (`run_sweep`) sambil memperbarui `completed_candidates` dan `updated_at` per kandidat, menilai
   (`rank_sweep`) dengan bobot yang tersimpan di sweep, menjalankan replikasi Monte Carlo untuk
   tiga kandidat teratas, lalu menyusun alasannya (`explain`).
3. `done` dengan kandidatnya; `failed` dengan pesannya bila gagal (hasil simulasi skenario tidak
   ikut berubah); `cancelled` bila digantikan di tengah jalan.

Job memakai sesi basis data sendiri dari engine yang sama dengan permintaan yang membuatnya.
`ThreadScheduler` menjalankan paling banyak DSS_MAX_CONCURRENT_SWEEPS sweep sekaligus per proses
supaya tidak menghabiskan kapasitas untuk permintaan API; sweep berikutnya mengantre.
"""

import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Protocol

from sqlalchemy import func
from sqlalchemy.orm import Session, sessionmaker

from app.core.config import Settings, get_settings
from app.modules.dss import sweep_service as service
from app.modules.dss.models import DssSweep, SweepStatus
from app.modules.dss.sweep_candidates import build_candidates
from app.modules.dss.sweep_reasons import explain
from app.modules.dss.sweep_runner import replicate, run_sweep
from app.modules.dss.sweep_scoring import ScoringWeights, rank_sweep
from app.modules.simulasi import snapshot_service
from app.modules.simulasi.sim_replication import DEFAULT_SEED
from app.modules.simulasi.simulation_models import SimulationRun

logger = logging.getLogger(__name__)


class SweepScheduler(Protocol):
    def submit(self, sweep_id: int, sessions: sessionmaker) -> None: ...


class ThreadScheduler:
    """Menjalankan sweep di thread latar belakang, paling banyak `workers` sekaligus."""

    def __init__(self, workers: int) -> None:
        self.workers = workers
        self._executor: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    def submit(self, sweep_id: int, sessions: sessionmaker) -> None:
        with self._lock:
            if self._executor is None:
                self._executor = ThreadPoolExecutor(
                    max_workers=self.workers, thread_name_prefix="dss-sweep"
                )
        self._executor.submit(run_sweep_job, sweep_id, sessions)


class InlineScheduler:
    """Menjalankan sweep langsung di dalam permintaan (tes)."""

    def submit(self, sweep_id: int, sessions: sessionmaker) -> None:
        run_sweep_job(sweep_id, sessions)


class NoScheduler:
    """Tidak menjalankan apa pun; sweep tetap `pending` (tes yang tidak memeriksa DSS)."""

    def submit(self, sweep_id: int, sessions: sessionmaker) -> None:
        return None


_default: ThreadScheduler | None = None
_default_lock = threading.Lock()


def get_sweep_scheduler() -> SweepScheduler:
    """Dependency FastAPI; tes menggantinya dengan `InlineScheduler` atau `NoScheduler`."""
    global _default
    with _default_lock:
        if _default is None:
            _default = ThreadScheduler(get_settings().dss_max_concurrent_sweeps)
    return _default


def job_sessions(db: Session) -> sessionmaker:
    """Pembuat sesi untuk job, pada engine yang sama dengan sesi permintaan `db`."""
    return sessionmaker(bind=db.get_bind(), autoflush=False, expire_on_commit=False)


class _Cancelled(Exception):
    pass


def run_sweep_job(sweep_id: int, sessions: sessionmaker, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    began = time.perf_counter()
    with sessions() as db:
        sweep = db.get(DssSweep, sweep_id)
        if sweep is None or sweep.status != SweepStatus.PENDING:
            return
        if service.superseded(db, sweep):
            _finish(db, sweep, SweepStatus.CANCELLED, service.SUPERSEDED, began)
            return
        sweep.status = SweepStatus.RUNNING.value
        sweep.started_at = func.now()
        sweep.updated_at = func.now()
        db.commit()
        try:
            _run(db, sweep, settings)
        except _Cancelled:
            db.rollback()
            _finish(db, sweep, SweepStatus.CANCELLED, service.SUPERSEDED, began)
        except Exception as exc:
            logger.exception("Sweep DSS %s gagal", sweep_id)
            db.rollback()
            _finish(db, sweep, SweepStatus.FAILED, str(exc) or repr(exc), began)
        else:
            _finish(db, sweep, SweepStatus.DONE, None, began)


def _run(db: Session, sweep: DssSweep, settings: Settings) -> None:
    run = db.get(SimulationRun, sweep.run_id)
    if run is None:
        raise ValueError(f"Simulasi tersimpan {sweep.run_id} tidak ditemukan")
    snapshot = snapshot_service.load_snapshot(db, run.snapshot_id)
    candidates = build_candidates(
        run.situation,
        run.parameters,
        snapshot,
        start_date=run.start_date,
        max_candidates=settings.dss_max_candidates,
    )
    sweep.total_candidates = len(candidates.eligible)
    sweep.omitted_candidates = candidates.omitted
    sweep.no_lever = candidates.no_lever
    sweep.updated_at = func.now()
    db.commit()

    def progress(done: int, total: int) -> None:
        if service.superseded(db, sweep):
            raise _Cancelled
        sweep.completed_candidates = done
        sweep.updated_at = func.now()
        db.commit()

    result = run_sweep(
        snapshot,
        candidates,
        start_date=run.start_date,
        runs=run.replications,
        seed=run.seed if run.seed is not None else DEFAULT_SEED,
        progress=progress,
    )
    ranking = rank_sweep(result, ScoringWeights(**sweep.weights))
    replicate(result, ranking.top())
    reasons = explain(result, ranking)
    if service.superseded(db, sweep):
        raise _Cancelled
    service.save_results(db, sweep, result, ranking, reasons)


def _finish(
    db: Session, sweep: DssSweep, status: SweepStatus, error: str | None, began: float
) -> None:
    sweep.status = status.value
    sweep.error = error
    sweep.finished_at = func.now()
    sweep.updated_at = func.now()
    sweep.duration_ms = round((time.perf_counter() - began) * 1000)
    db.commit()
