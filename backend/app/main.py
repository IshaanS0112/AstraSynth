"""AstraSynth API entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import PlainTextResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.config import get_settings
from app.db.migrate import prepare_schema
from app.db.session import SessionLocal
from app.models import RoverConfig
from app.observability import METRICS, RequestContextMiddleware, configure_logging
from app.routers import (
    experiments,
    jobs,
    missions,
    paths,
    reports,
    risk,
    rover_configs,
    science,
    simulation,
    terrain,
)
from app.worker import WorkerPool

logger = logging.getLogger("astrasynth")

# Three reference rovers spanning the feasibility space for a ~1.3 km traverse:
# the Scout runs out of battery, the Survey class completes with reserve, and
# the Heavy class finishes inside its margin. Energy-per-metre figures are the
# right order of magnitude for solar planetary rovers (MER-class rovers drove
# on the order of 100 m per sol on roughly 0.3 kWh), but they are illustrative
# planning defaults, not manufacturer specifications.
SEED_ROVERS = [
    {
        "name": "Scout-Class (light)",
        "battery_capacity_kwh": 2.0,
        "max_traversable_slope_deg": 20.0,
        "energy_per_meter_kwh": 0.0018,
    },
    {
        "name": "Survey-Class (medium)",
        "battery_capacity_kwh": 6.0,
        "max_traversable_slope_deg": 25.0,
        "energy_per_meter_kwh": 0.0030,
    },
    {
        "name": "Heavy Lab-Class",
        "battery_capacity_kwh": 9.0,
        "max_traversable_slope_deg": 30.0,
        "energy_per_meter_kwh": 0.0062,
    },
]


def seed_rover_configs() -> None:
    with SessionLocal() as db:
        existing = set(db.scalars(select(RoverConfig.name)))
        added = 0
        for spec in SEED_ROVERS:
            if spec["name"] not in existing:
                db.add(RoverConfig(**spec))
                added += 1
        if added:
            db.commit()
            logger.info("Seeded %d rover configs", added)


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    prepare_schema(settings.database_url, settings.auto_migrate)
    seed_rover_configs()

    # Workers live in the API process by default. The queue is a table, so this
    # is one consumer of it rather than a special case - set WORKER_THREADS=0
    # and run `python -m app.worker` to move them out without changing anything
    # else.
    pool = WorkerPool(settings.worker_threads, settings)
    pool.start()
    app_instance.state.worker_pool = pool
    try:
        yield
    finally:
        pool.stop()


settings = get_settings()
configure_logging(settings.log_level, settings.log_json)

app = FastAPI(
    title="AstraSynth API",
    version="1.1.0",
    description=(
        "Planetary mission autonomy. Terrain perception with propagated uncertainty, "
        "four route planners over one cost model, multi-rover deconfliction, and "
        "traverse simulation against terrain the rover can only partly see. "
        "All figures are computed deterministically; the LLM only narrates them."
    ),
    lifespan=lifespan,
)

# Outermost, so it sees the final status of everything including CORS
# rejections and unhandled exceptions.
app.add_middleware(RequestContextMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(settings.storage_dir)), name="static")

for module in (
    missions,
    terrain,
    paths,
    risk,
    reports,
    rover_configs,
    science,
    simulation,
    experiments,
    jobs,
):
    app.include_router(module.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    """Liveness only. Deliberately does not touch the database.

    A liveness probe that fails when the database is down gets the API killed and
    restarted during a database outage, which helps nobody. Readiness is the
    check that should fail then, and it is separate for exactly that reason.
    """
    return {"status": "ok"}


@app.get("/ready", tags=["meta"])
def ready() -> dict[str, object]:
    """Readiness: can this process actually serve a request end to end?"""
    from sqlalchemy import text

    from app.db.session import SessionLocal

    checks: dict[str, object] = {"database": False, "storage": False}
    try:
        with SessionLocal() as db:
            db.execute(text("SELECT 1"))
        checks["database"] = True
    except Exception as exc:
        checks["database_error"] = str(exc)[:200]

    checks["storage"] = settings.storage_dir.is_dir()
    checks["worker_threads"] = settings.worker_threads
    checks["ready"] = bool(checks["database"] and checks["storage"])
    if not checks["ready"]:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=checks)
    return checks


@app.get("/metrics", tags=["meta"], response_class=PlainTextResponse)
def metrics() -> str:
    """Prometheus text exposition.

    Queue depth is refreshed on scrape rather than written on every state change:
    it is a property of a table, and deriving it at read time cannot drift from
    the table the way an incrementally maintained counter can.
    """
    from app.db.session import SessionLocal
    from app.services import jobs as queue

    try:
        with SessionLocal() as db:
            for status_name, count in queue.queue_depth(db).items():
                METRICS.set_gauge("astra_jobs_queue_depth", count, {"status": status_name})
    except Exception:
        logger.warning("could not read queue depth for metrics", exc_info=True)
    return METRICS.render()
