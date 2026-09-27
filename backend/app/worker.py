"""The background worker.

Runs either inside the API process as a daemon thread, or on its own::

    python -m app.worker --workers 2

Both use the same loop, because the difference between them is a deployment
decision rather than a behavioural one: the queue is in PostgreSQL, so an
in-process worker and a separate one are simply two consumers of the same table
and can run at the same time without coordinating.

In-process is the default because a single-node research deployment should not
need a second thing to start, and because a study that takes ten seconds does not
need a fleet. Splitting the worker out is one flag away when it does.

Why a thread and not asyncio
----------------------------
The work is CPU-bound numpy and pure-Python search. An async task would hold the
event loop for the whole study and stall every request on the process. A thread
releases the GIL inside numpy, and the Monte Carlo path escapes to real processes
anyway; the thread is there to own the database session and the lease, not to
provide parallelism.
"""

from __future__ import annotations

import argparse
import logging
import os
import signal
import socket
import threading
import time
import uuid

from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.session import SessionLocal
from app.models import Job, Mission
from app.observability import METRICS
from app.services import autonomy, jobs
from app.services.simulation.monte_carlo import Perturbations

logger = logging.getLogger("astrasynth.worker")

# How often a running job renews its lease and publishes progress. Frequent
# enough that a cancel is noticed promptly, rare enough that a 500-trial study
# does not spend its time writing rows.
HEARTBEAT_EVERY_SECONDS = 2.0


class JobCancelled(RuntimeError):
    """Raised inside a handler when the API asked the job to stop."""


def worker_identity() -> str:
    return f"{socket.gethostname()}/{os.getpid()}/{uuid.uuid4().hex[:6]}"


class Worker:
    """One consumer of the jobs table."""

    def __init__(
        self,
        settings: Settings | None = None,
        poll_interval: float = 1.0,
        lease_seconds: float = jobs.DEFAULT_LEASE_SECONDS,
        worker_id: str | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.poll_interval = poll_interval
        self.lease_seconds = lease_seconds
        self.worker_id = worker_id or worker_identity()
        self.processed = 0

    # --- the loop -------------------------------------------------------------

    def run_forever(self, stop: threading.Event) -> None:
        logger.info("worker %s started", self.worker_id)
        while not stop.is_set():
            try:
                worked = self.run_once()
            except Exception:
                logger.exception("worker %s loop error", self.worker_id)
                worked = False
            if not worked:
                stop.wait(self.poll_interval)
        logger.info("worker %s stopped after %d jobs", self.worker_id, self.processed)

    def run_once(self) -> bool:
        """Claim and run at most one job. ``True`` if there was work."""
        with SessionLocal() as db:
            # Recovery runs on the way to work, not on a timer: a worker looking
            # for a job is exactly when abandoned work is worth noticing, and it
            # keeps the recovery path exercised outside of incidents.
            jobs.reclaim_stale(db, self.lease_seconds)
            job = jobs.claim(db, self.worker_id)
            if job is None:
                return False

            logger.info(
                "job claimed",
                extra={
                    "astra_job": str(job.id),
                    "astra_kind": job.kind,
                    "astra_worker": self.worker_id,
                },
            )
            outcome = "SUCCEEDED"
            with METRICS.timer("astra_job_seconds", {"kind": job.kind}):
                try:
                    experiment = self._execute(db, job)
                except JobCancelled:
                    outcome = "CANCELLED"
                    jobs.finish_cancelled(db, job)
                except Exception as exc:
                    outcome = "FAILED"
                    logger.exception("job failed", extra={"astra_job": str(job.id)})
                    jobs.fail(db, job, f"{type(exc).__name__}: {exc}")
                else:
                    jobs.succeed(db, job, experiment.id if experiment else None)
            METRICS.increment("astra_jobs_total", {"kind": job.kind, "outcome": outcome})
            logger.info(
                "job finished",
                extra={"astra_job": str(job.id), "astra_kind": job.kind, "astra_outcome": outcome},
            )
            self.processed += 1
            return True

    # --- dispatch -------------------------------------------------------------

    def _execute(self, db: Session, job: Job):
        mission = db.get(Mission, job.mission_id)
        if mission is None:
            raise RuntimeError(f"mission {job.mission_id} no longer exists")

        handler = getattr(self, f"_run_{job.kind}", None)
        if handler is None:
            raise RuntimeError(f"no handler for job kind {job.kind!r}")
        return handler(db, job, mission)

    def _reporter(self, db: Session, job: Job):
        """A progress callback that also renews the lease and checks for cancel.

        Throttled to :data:`HEARTBEAT_EVERY_SECONDS`, but always fires on the
        final trial so a finished study never sits at 97%.
        """
        state = {"last": 0.0}

        def report(done: int, total: int) -> bool:
            now = time.monotonic()
            if now - state["last"] < HEARTBEAT_EVERY_SECONDS and done < total:
                return True
            state["last"] = now
            alive = jobs.heartbeat(db, job.id, done / total, f"{done}/{total} trials")
            return alive

        return report

    def _run_monte_carlo(self, db: Session, job: Job, mission: Mission):
        payload = job.payload
        rover = autonomy.resolve_rover(db, uuid.UUID(payload["rover_config_id"]))
        perturbations = Perturbations(
            terrain_sigma=payload.get("terrain_sigma", 0.08),
            obstacle_count=payload.get("unmapped_obstacles", 12),
            energy_factor_sigma=payload.get("energy_factor_sigma", 0.12),
        )
        report = self._reporter(db, job)
        cancelled = {"flag": False}

        def progress(done: int, total: int) -> bool:
            if not report(done, total):
                cancelled["flag"] = True
                return False
            return True

        experiment = autonomy.run_monte_carlo_study(
            db=db,
            mission=mission,
            rover_config=rover,
            start=payload["start"],
            goal=payload["goal"],
            settings=self.settings,
            trials=payload["trials"],
            seed=payload.get("seed", 0),
            uncertainty_weight=payload.get("uncertainty_weight", 0.0),
            sensor_range_m=payload.get("sensor_range_m"),
            perturbations=perturbations,
            workers=payload.get("workers"),
            progress=progress,
        )
        if cancelled["flag"]:
            # The partial study is still written - a cancelled 400-trial run that
            # got to 300 is a result, not a loss - but the job reports CANCELLED
            # so nobody reads it as the study they asked for.
            raise JobCancelled
        return experiment

    def _run_route_study(self, db: Session, job: Job, mission: Mission):
        payload = job.payload
        jobs.heartbeat(db, job.id, 0.1, "sweeping weights")
        return autonomy.run_route_study(
            db=db,
            mission=mission,
            rover_config=autonomy.resolve_rover(db, uuid.UUID(payload["rover_config_id"])),
            start=payload["start"],
            goal=payload["goal"],
            settings=self.settings,
        )

    def _run_fleet_plan(self, db: Session, job: Job, mission: Mission):
        payload = job.payload
        jobs.heartbeat(db, job.id, 0.1, "deconflicting")
        assignments = [
            {
                "label": item.get("label"),
                "rover_config": autonomy.resolve_rover(db, uuid.UUID(item["rover_config_id"])),
                "start": item["start"],
                "goal": item["goal"],
            }
            for item in payload["assignments"]
        ]
        return autonomy.run_fleet_plan(
            db=db,
            mission=mission,
            assignments=assignments,
            settings=self.settings,
            max_nodes=payload.get("max_high_level_nodes", 600),
            time_budget_seconds=payload.get("time_budget_seconds", 120.0),
            coordination_max_dim=payload.get("coordination_max_dim"),
        )

    def _run_science_tour(self, db: Session, job: Job, mission: Mission):
        payload = job.payload
        jobs.heartbeat(db, job.id, 0.1, "selecting targets")
        return autonomy.run_science_tour(
            db=db,
            mission=mission,
            rover_config=autonomy.resolve_rover(db, uuid.UUID(payload["rover_config_id"])),
            start=payload["start"],
            settings=self.settings,
            energy_budget_kwh=payload.get("energy_budget_kwh"),
            time_budget_seconds=payload.get("time_budget_seconds", 4 * 88775.0),
            instruments=payload.get("instruments", []),
            relay_at=payload.get("relay_at"),
        )


# --- embedding in the API process ---------------------------------------------


class WorkerPool:
    """Worker threads owned by the API process, started and stopped by lifespan."""

    def __init__(self, count: int, settings: Settings | None = None) -> None:
        self.count = count
        self.settings = settings
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []

    def start(self) -> None:
        if self.count <= 0:
            logger.info("in-process workers disabled")
            return
        for index in range(self.count):
            worker = Worker(self.settings, worker_id=f"{worker_identity()}#{index}")
            thread = threading.Thread(
                target=worker.run_forever,
                args=(self._stop,),
                daemon=True,
                name=f"astra-worker-{index}",
            )
            thread.start()
            self._threads.append(thread)
        logger.info("started %d in-process worker(s)", self.count)

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        for thread in self._threads:
            thread.join(timeout=timeout)
        self._threads.clear()


def main() -> int:
    parser = argparse.ArgumentParser(description="AstraSynth background worker")
    parser.add_argument("--workers", type=int, default=1, help="threads in this process")
    parser.add_argument("--poll-interval", type=float, default=1.0)
    parser.add_argument("--once", action="store_true", help="drain the queue and exit")
    args = parser.parse_args()

    from app.observability import configure_logging

    settings = get_settings()
    configure_logging(settings.log_level, settings.log_json)

    if args.once:
        worker = Worker(poll_interval=args.poll_interval)
        while worker.run_once():
            pass
        print(f"drained {worker.processed} job(s)")
        return 0

    stop = threading.Event()
    # SIGTERM is what a container runtime sends; without this the worker is
    # killed mid-job and its lease has to expire before anything else picks it up.
    for received in (signal.SIGINT, signal.SIGTERM):
        signal.signal(received, lambda *_: stop.set())

    pool = WorkerPool(args.workers)
    pool.start()
    while not stop.is_set():
        stop.wait(1.0)
    pool.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
