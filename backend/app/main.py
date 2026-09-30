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
from app.security import ApiKeyMiddleware, declare_security_scheme, validate_api_key
from app.worker import WorkerPool

logger = logging.getLogger("astrasynth")

# Three reference rovers spanning the feasibility space for a ~1.3 km traverse:
# Scout runs out of battery, Survey completes with reserve, Heavy finishes inside
# its margin. Illustrative planning defaults, not manufacturer specifications.
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

    # The queue is a table, so an in-process worker is just one consumer of it.
    # WORKER_THREADS=0 plus `python -m app.worker` moves them out.
    pool = WorkerPool(settings.worker_threads, settings)
    pool.start()
    app_instance.state.worker_pool = pool
    try:
        yield
    finally:
        pool.stop()


settings = get_settings()
configure_logging(settings.log_level, settings.log_json)
# At startup, not on first request: a bad key should stop a deployment.
validate_api_key(settings.api_key)

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

# `add_middleware` inserts at the front, so the LAST added is the outermost.
# Reading outside in, this builds:
#
#   RequestContextMiddleware   correlation id, log line, metrics
#     CORSMiddleware           response headers, preflight
#       ApiKeyMiddleware       401 when a shared secret is configured
#         routers
#
# Context outermost so 401s and preflights are still logged and counted; CORS
# outside the key check so a 401 carries CORS headers instead of surfacing in a
# browser as an unexplained CORS failure.
app.add_middleware(ApiKeyMiddleware, api_key=settings.api_key)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.add_middleware(RequestContextMiddleware)

# Only declared when a key is set; a padlock on an open API would be a lie.
declare_security_scheme(app, settings.api_key)

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
    """Liveness only. Deliberately does not touch the database."""
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
    """Prometheus text exposition."""
    from app.db.session import SessionLocal
    from app.services import jobs as queue

    try:
        with SessionLocal() as db:
            for status_name, count in queue.queue_depth(db).items():
                METRICS.set_gauge("astra_jobs_queue_depth", count, {"status": status_name})
    except Exception:
        logger.warning("could not read queue depth for metrics", exc_info=True)
    return METRICS.render()
