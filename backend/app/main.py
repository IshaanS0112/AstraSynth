"""AstraSynth API entrypoint."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from app.config import get_settings
from app.db.session import Base, SessionLocal, engine
from app.models import RoverConfig
from app.routers import missions, paths, reports, risk, rover_configs, terrain

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
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
        existing = {name for name in db.scalars(select(RoverConfig.name))}
        added = 0
        for spec in SEED_ROVERS:
            if spec["name"] not in existing:
                db.add(RoverConfig(**spec))
                added += 1
        if added:
            db.commit()
            logger.info("Seeded %d rover configs", added)


@asynccontextmanager
async def lifespan(_: FastAPI):
    # create_all is adequate here because the schema is append-only for V1.
    # A migration tool (Alembic) is the correct answer the moment a column
    # needs to change shape - noted in docs/architecture.md.
    Base.metadata.create_all(bind=engine)
    seed_rover_configs()
    yield


settings = get_settings()

app = FastAPI(
    title="AstraSynth API",
    version="1.0.0",
    description=(
        "AI-assisted planetary mission intelligence. Terrain hazard analysis, "
        "energy-aware A* path planning, and battery-feasibility risk assessment. "
        "All risk figures are computed deterministically; the LLM only narrates them."
    ),
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(settings.storage_dir)), name="static")

for module in (missions, terrain, paths, risk, reports, rover_configs):
    app.include_router(module.router)


@app.get("/health", tags=["meta"])
def health() -> dict[str, str]:
    return {"status": "ok"}
