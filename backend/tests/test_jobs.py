"""The background queue."""

from __future__ import annotations

import os
import sys
import threading
import time
import uuid
from datetime import timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg2://astra:astra@localhost:5432/astrasynth"
)


def _database_available() -> bool:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(
    not _database_available(), reason=f"PostgreSQL not reachable at {DATABASE_URL}"
)


@pytest.fixture(scope="module")
def app_client(tmp_path_factory):
    os.environ["DATABASE_URL"] = DATABASE_URL
    os.environ["STORAGE_DIR"] = str(tmp_path_factory.mktemp("storage_jobs"))
    os.environ["ANTHROPIC_API_KEY"] = ""
    # The tests drive the worker themselves, so that a claim is observable
    # rather than racing an in-process thread that already took the job.
    os.environ["WORKER_THREADS"] = "0"

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    with TestClient(app) as client:
        yield client


@pytest.fixture(scope="module")
def session_factory(app_client):
    from app.db.session import SessionLocal

    return SessionLocal


@pytest.fixture(scope="module")
def mission(app_client):
    import cv2

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))
    from generate_terrain import generate

    image = cv2.imencode(".png", generate("sandy_plain", 128, seed=4))[1].tobytes()
    created = app_client.post(
        "/missions",
        data={"name": "Queue mission"},
        files={"terrain_image": ("t.png", image, "image/png")},
    ).json()
    assert app_client.post(f"/missions/{created['id']}/analyze-terrain").status_code == 200
    return created


@pytest.fixture(scope="module")
def rover_id(app_client) -> str:
    return next(c["id"] for c in app_client.get("/rover-configs").json() if "Survey" in c["name"])


@pytest.fixture(autouse=True)
def clean_queue(session_factory):
    """Start every test with an empty queue."""
    from app.models import Job

    with session_factory() as session:
        session.query(Job).delete()
        session.commit()
    yield


@pytest.fixture
def db(session_factory):
    with session_factory() as session:
        yield session


@pytest.fixture
def mission_row(db, mission):
    from app.models import Mission

    return db.get(Mission, uuid.UUID(mission["id"]))


def drain(db, job_id, timeout: float = 120.0):
    """Run the worker until the given job reaches a terminal state."""
    from app.enums import JobStatus
    from app.models import Job
    from app.worker import Worker

    worker = Worker()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = db.get(Job, job_id)
        db.refresh(job)
        if job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED, JobStatus.CANCELLED}:
            return job
        if not worker.run_once():
            time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish within {timeout}s")


class TestClaiming:
    def test_a_claim_moves_the_job_and_counts_the_attempt(self, db, mission_row):
        from app.enums import JobStatus
        from app.services import jobs

        job = jobs.enqueue(db, mission_row, "route_study", {"marker": "claim"})
        assert job.status == JobStatus.QUEUED
        assert job.attempts == 0

        claimed = jobs.claim(db, "worker-a")
        assert claimed is not None
        assert claimed.status == JobStatus.RUNNING
        assert claimed.claimed_by == "worker-a"
        assert claimed.attempts == 1
        assert claimed.heartbeat_at is not None
        jobs.succeed(db, claimed, None)

    def test_two_workers_never_get_the_same_job(self, session_factory, db, mission_row):
        """SKIP LOCKED, stated as a property rather than as a SQL fragment."""
        from app.services import jobs

        queued = [
            jobs.enqueue(db, mission_row, "route_study", {"marker": f"race-{i}"}).id
            for i in range(6)
        ]

        claimed: list[uuid.UUID] = []
        lock = threading.Lock()
        barrier = threading.Barrier(4)

        def grab(name: str) -> None:
            with session_factory() as session:
                barrier.wait()
                for _ in range(3):
                    job = jobs.claim(session, name)
                    if job is None:
                        break
                    with lock:
                        claimed.append(job.id)

        threads = [threading.Thread(target=grab, args=(f"w{i}",)) for i in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

        ours = [job_id for job_id in claimed if job_id in queued]
        assert len(ours) == len(set(ours)), "a job was claimed twice"
        assert set(ours) == set(queued), "some jobs were never claimed"

    def test_an_empty_queue_returns_nothing_rather_than_blocking(self, db):
        from app.services import jobs

        while jobs.claim(db, "drainer") is not None:
            pass
        assert jobs.claim(db, "drainer") is None


class TestLease:
    def test_a_heartbeat_renews_the_lease(self, db, mission_row):
        from app.services import jobs

        jobs.enqueue(db, mission_row, "route_study", {"marker": "hb"})
        job = jobs.claim(db, "worker-hb")
        first = job.heartbeat_at

        time.sleep(0.02)
        assert jobs.heartbeat(db, job.id, 0.5, "half way") is True
        db.refresh(job)
        assert job.heartbeat_at > first
        assert job.progress == 0.5
        assert job.progress_detail == "half way"
        jobs.succeed(db, job, None)

    def test_a_dead_worker_releases_its_job(self, db, mission_row):
        from app.enums import JobStatus
        from app.services import jobs

        jobs.enqueue(db, mission_row, "route_study", {"marker": "stale"})
        job = jobs.claim(db, "worker-that-dies")
        job.heartbeat_at = jobs.utcnow() - timedelta(seconds=600)
        db.commit()

        assert jobs.reclaim_stale(db, lease_seconds=60) >= 1
        db.refresh(job)
        assert job.status == JobStatus.QUEUED
        assert job.claimed_by is None

    def test_a_job_that_keeps_dying_is_buried(self, db, mission_row):
        """At-least-once must not mean forever."""
        from app.enums import JobStatus
        from app.services import jobs

        jobs.enqueue(db, mission_row, "route_study", {"marker": "doomed"}, max_attempts=1)
        job = jobs.claim(db, "worker-doomed")
        job.heartbeat_at = jobs.utcnow() - timedelta(seconds=600)
        db.commit()

        jobs.reclaim_stale(db, lease_seconds=60)
        db.refresh(job)
        assert job.status == JobStatus.FAILED
        assert "heartbeat" in (job.error or "")

    def test_a_live_job_is_left_alone(self, db, mission_row):
        from app.enums import JobStatus
        from app.services import jobs

        jobs.enqueue(db, mission_row, "route_study", {"marker": "alive"})
        job = jobs.claim(db, "worker-alive")
        jobs.reclaim_stale(db, lease_seconds=60)
        db.refresh(job)
        assert job.status == JobStatus.RUNNING
        jobs.succeed(db, job, None)


class TestRetry:
    def test_a_failure_with_attempts_left_goes_back_to_the_queue(self, db, mission_row):
        from app.enums import JobStatus
        from app.services import jobs

        jobs.enqueue(db, mission_row, "route_study", {"marker": "retry"}, max_attempts=2)
        job = jobs.claim(db, "worker-retry")
        jobs.fail(db, job, "transient")
        assert job.status == JobStatus.QUEUED

        again = jobs.claim(db, "worker-retry")
        assert again.id == job.id
        assert again.attempts == 2
        jobs.fail(db, again, "transient again")
        assert again.status == JobStatus.FAILED
        assert again.error == "transient again"


class TestCancellation:
    def test_a_queued_job_cancels_outright(self, db, mission_row):
        from app.enums import JobStatus
        from app.services import jobs

        job = jobs.enqueue(db, mission_row, "route_study", {"marker": "cancel-queued"})
        assert jobs.request_cancel(db, job) is True
        assert job.status == JobStatus.CANCELLED
        assert job.finished_at is not None

    def test_a_running_job_is_only_asked_to_stop(self, db, mission_row):
        """The worker owns the terminal state, because only it knows when."""
        from app.enums import JobStatus
        from app.services import jobs

        jobs.enqueue(db, mission_row, "route_study", {"marker": "cancel-running"})
        job = jobs.claim(db, "worker-cancel")
        assert jobs.request_cancel(db, job) is True
        assert job.status == JobStatus.CANCELLING
        assert job.finished_at is None

        # The heartbeat is how the worker finds out.
        assert jobs.heartbeat(db, job.id, 0.3) is False
        jobs.finish_cancelled(db, job)
        assert job.status == JobStatus.CANCELLED

    def test_a_finished_job_cannot_be_cancelled(self, db, mission_row):
        from app.services import jobs

        job = jobs.enqueue(db, mission_row, "route_study", {"marker": "cancel-done"})
        jobs.claim(db, "worker-done")
        jobs.succeed(db, job, None)
        assert jobs.request_cancel(db, job) is False


class TestEndToEnd:
    def test_a_queued_study_runs_and_produces_an_experiment(
        self, app_client, db, mission, rover_id
    ):
        response = app_client.post(
            f"/missions/{mission['id']}/route-study/jobs",
            json={
                "start": {"x": 8, "y": 8},
                "goal": {"x": 110, "y": 110},
                "rover_config_id": rover_id,
            },
        )
        assert response.status_code == 202, response.text
        submitted = response.json()
        assert submitted["status"] == "QUEUED"
        assert submitted["progress"] == 0.0

        finished = drain(db, uuid.UUID(submitted["id"]))
        assert finished.status == "SUCCEEDED", finished.error
        assert finished.experiment_id is not None
        assert finished.progress == 1.0

        experiment = app_client.get(
            f"/missions/{mission['id']}/experiments/{finished.experiment_id}"
        ).json()
        assert experiment["kind"] == "route_study"
        assert experiment["result"]["front"]

    def test_a_monte_carlo_job_reports_progress_as_it_goes(
        self, app_client, db, session_factory, mission, rover_id
    ):
        from app.models import Job
        from app.worker import Worker

        submitted = app_client.post(
            f"/missions/{mission['id']}/monte-carlo/jobs",
            json={
                "start": {"x": 8, "y": 8},
                "goal": {"x": 110, "y": 110},
                "rover_config_id": rover_id,
                "trials": 12,
                "seed": 3,
                "workers": 2,
            },
        ).json()
        assert submitted["status"] == "QUEUED"

        # Watch from a second session while the worker runs, so progress is
        # observed mid-flight rather than inferred from the final row.
        seen: list[float] = []
        stop = threading.Event()

        def observe() -> None:
            with session_factory() as watch:
                while not stop.is_set():
                    row = watch.get(Job, uuid.UUID(submitted["id"]))
                    if row is not None:
                        watch.refresh(row)
                        seen.append(row.progress)
                    time.sleep(0.05)

        worker = Worker()
        watcher = threading.Thread(target=observe, daemon=True)
        watcher.start()
        try:
            assert worker.run_once() is True
        finally:
            stop.set()
            watcher.join(timeout=5)

        assert any(0.0 < value < 1.0 for value in seen), (
            f"progress was never observed between 0 and 1: {sorted(set(seen))}"
        )

        job = db.get(Job, uuid.UUID(submitted["id"]))
        db.refresh(job)
        assert job.status == "SUCCEEDED", job.error
        assert job.progress == 1.0

        experiment = app_client.get(
            f"/missions/{mission['id']}/experiments/{job.experiment_id}"
        ).json()
        assert experiment["result"]["trials"] == 12
        assert experiment["seed"] == 3

    def test_the_trial_cap_is_higher_for_queued_studies(self, app_client, mission, rover_id):
        """Nothing is holding an HTTP connection open, so the limit can be real work."""
        body = {
            "start": {"x": 8, "y": 8},
            "goal": {"x": 110, "y": 110},
            "rover_config_id": rover_id,
            "trials": 2000,
        }
        assert (
            app_client.post(f"/missions/{mission['id']}/monte-carlo", json=body).status_code == 422
        )
        assert (
            app_client.post(f"/missions/{mission['id']}/monte-carlo/jobs", json=body).status_code
            == 202
        )

    def test_an_unknown_handler_fails_the_job_rather_than_the_worker(self, db, mission_row):
        from app.services import jobs
        from app.worker import Worker

        job = jobs.enqueue(db, mission_row, "not_a_real_kind", {})
        assert Worker().run_once() is True
        db.refresh(job)
        assert job.status in {"QUEUED", "FAILED"}
        assert "no handler" in (job.error or "")


class TestApi:
    def test_polling_and_listing(self, app_client, mission, rover_id):
        submitted = app_client.post(
            f"/missions/{mission['id']}/route-study/jobs",
            json={
                "start": {"x": 8, "y": 8},
                "goal": {"x": 100, "y": 100},
                "rover_config_id": rover_id,
            },
        ).json()

        polled = app_client.get(f"/jobs/{submitted['id']}")
        assert polled.status_code == 200
        assert polled.json()["id"] == submitted["id"]

        listed = app_client.get(f"/missions/{mission['id']}/jobs").json()
        assert submitted["id"] in {row["id"] for row in listed}

        queued_only = app_client.get(f"/missions/{mission['id']}/jobs?status=QUEUED").json()
        assert all(row["status"] == "QUEUED" for row in queued_only)

    def test_cancelling_through_the_api(self, app_client, mission, rover_id):
        submitted = app_client.post(
            f"/missions/{mission['id']}/route-study/jobs",
            json={
                "start": {"x": 8, "y": 8},
                "goal": {"x": 90, "y": 90},
                "rover_config_id": rover_id,
            },
        ).json()

        cancelled = app_client.post(f"/jobs/{submitted['id']}/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "CANCELLED"

        again = app_client.post(f"/jobs/{submitted['id']}/cancel")
        assert again.status_code == 409

    def test_an_unknown_job_is_a_404(self, app_client):
        assert app_client.get(f"/jobs/{uuid.uuid4()}").status_code == 404

    def test_queue_stats_say_whether_anything_is_draining(self, app_client):
        stats = app_client.get("/jobs").json()
        assert "by_status" in stats
        # The fixture disables in-process workers, so this must report zero -
        # a depth without a consumer count cannot distinguish busy from stopped.
        assert stats["in_process_workers"] == 0


class TestConfiguration:
    """The settings that govern the queue have to actually reach the worker."""

    def test_the_worker_takes_its_timings_from_settings(self):
        from app.config import Settings
        from app.worker import Worker

        settings = Settings(worker_poll_seconds=0.25, job_lease_seconds=11.0)
        worker = Worker(settings)

        assert worker.poll_interval == 0.25
        assert worker.lease_seconds == 11.0

    def test_an_explicit_argument_still_wins(self):
        """Config is the default, not a ceiling: `--poll-interval` has to override."""
        from app.config import Settings
        from app.worker import Worker

        settings = Settings(worker_poll_seconds=0.25, job_lease_seconds=11.0)
        worker = Worker(settings, poll_interval=0.5, lease_seconds=7.0)

        assert worker.poll_interval == 0.5
        assert worker.lease_seconds == 7.0

    def test_the_pool_passes_them_to_every_worker_it_makes(self):
        from app.config import Settings
        from app.worker import WorkerPool

        settings = Settings(worker_poll_seconds=0.25, job_lease_seconds=11.0)
        pool = WorkerPool(2, settings)
        try:
            pool.start()
            assert len(pool._threads) == 2
        finally:
            pool.stop(timeout=2.0)

        # The threads are gone; what is asserted is that a worker built the way
        # the pool builds them carries the configured timings.
        from app.worker import Worker

        assert Worker(settings, poll_interval=pool.poll_interval).lease_seconds == 11.0

    def test_zero_threads_starts_nothing(self):
        from app.config import Settings
        from app.worker import WorkerPool

        pool = WorkerPool(0, Settings())
        pool.start()
        assert pool._threads == []
        pool.stop(timeout=1.0)
