"""Application configuration. Every tunable constant in the pipeline lives here."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Infrastructure -----------------------------------------------------
    database_url: str = "postgresql+psycopg2://astra:astra@localhost:5432/astrasynth"
    storage_dir: Path = Path(__file__).resolve().parent.parent / "storage"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"
    api_key: str = ""  # empty = every endpoint open; see app/security.py

    # --- LLM ----------------------------------------------------------------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 1200

    # --- Terrain interpretation ---------------------------------------------
    # Grayscale intensity 0-255 maps linearly onto [0, elevation_range_m]. These
    # two turn pixel gradients into real slope angles.
    meters_per_pixel: float = 2.0
    elevation_range_m: float = 40.0
    roughness_window: int = 9  # NxN window for local-variance roughness

    # Canny thresholds are derived per image, not fixed: a fixed threshold finds
    # nothing on a smooth DEM and everything on a high-contrast one.
    canny_gradient_percentile: float = 97.0  # high threshold = this percentile
    canny_low_ratio: float = 0.5  # low threshold = ratio * high threshold
    morph_close_kernel: int = 5  # joins broken Canny fragments into regions
    min_obstacle_area_px: int = 40  # contours smaller than this are noise

    # --- Hazard scoring weights (must sum to 1.0) ---------------------------
    hazard_w_slope: float = 0.5
    hazard_w_obstacle: float = 0.3
    hazard_w_roughness: float = 0.2
    slope_reference_deg: float = 30.0  # slope at which the slope term saturates

    # --- Terrain classification thresholds ----------------------------------
    crater_field_area_fraction: float = 0.20  # craters are few but large: use area
    sandy_plain_max_slope_deg: float = 6.0
    sandy_plain_max_roughness: float = 0.12
    sandy_plain_max_area_fraction: float = 0.10

    # --- Schema -------------------------------------------------------------
    auto_migrate: bool = True  # off where a deploy pipeline runs alembic itself

    # --- Observability ------------------------------------------------------
    log_level: str = "INFO"
    log_json: bool = True  # false for a readable console

    # --- Background jobs ----------------------------------------------------
    worker_threads: int = 1  # 0 when workers run as their own deployment
    worker_poll_seconds: float = 1.0
    job_lease_seconds: float = 90.0  # must far exceed the 2 s heartbeat interval

    # --- Hazard uncertainty -------------------------------------------------
    # Each component's sigma comes from a known error source; they combine by
    # quadrature. See hazard_mapper.py.
    dem_quantisation_levels: int = 256  # 8-bit DEM: range / 256 per level
    obstacle_position_sigma_px: float = 1.0  # Canny edge localisation, in pixels
    uncertainty_planning_weight: float = 0.0  # sigmas charged to the planner

    # --- Path planning ------------------------------------------------------
    planning_grid_max_dim: int = 192  # hazard map is downsampled to this for A*
    energy_slope_coefficient: float = 0.5  # k in energy_factor = 1 + k*|slope|
    lethal_hazard_threshold: float = 0.85  # at or above: removed from the graph

    # --- Risk tiering -------------------------------------------------------
    risk_weight_hazard: float = 0.6
    risk_weight_energy: float = 0.4
    risk_threshold_low: float = 0.3
    risk_threshold_medium: float = 0.6
    energy_margin_fraction: float = 0.85  # above this -> FEASIBLE_WITH_MARGIN

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    settings.storage_dir.mkdir(parents=True, exist_ok=True)
    return settings
