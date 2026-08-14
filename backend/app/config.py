"""Application configuration.

Every tunable constant in the analysis pipeline lives here so that the exact
parameter set used for a mission can be recorded alongside its report. Nothing
in the hazard / path / risk math reads a magic number that isn't declared here.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Infrastructure -----------------------------------------------------
    database_url: str = "postgresql+psycopg2://astra:astra@localhost:5432/astrasynth"
    storage_dir: Path = Path(__file__).resolve().parent.parent / "storage"
    cors_origins: str = "http://localhost:5173,http://localhost:3000"

    # --- LLM ----------------------------------------------------------------
    anthropic_api_key: str = ""
    anthropic_model: str = "claude-sonnet-4-5"
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 1200

    # --- Terrain interpretation --------------------------------------------
    # A grayscale terrain image is interpreted as a coarse DEM: intensity 0-255
    # maps linearly onto [0, elevation_range_m]. These two numbers are what turn
    # pixel gradients into real slope angles, so they are recorded per mission.
    meters_per_pixel: float = 2.0
    elevation_range_m: float = 40.0
    roughness_window: int = 9  # NxN window for local-variance roughness

    # Canny thresholds are derived per image from the gradient distribution
    # rather than hard-coded. Fixed thresholds detect essentially nothing on a
    # smooth DEM and everything on a high-contrast one; see docs/architecture.md
    # ("Bugs found"). High threshold = this percentile of gradient magnitude.
    canny_gradient_percentile: float = 97.0
    canny_low_ratio: float = 0.5  # low threshold = ratio * high threshold
    morph_close_kernel: int = 5  # joins broken Canny fragments into regions
    min_obstacle_area_px: int = 40  # contours smaller than this are noise

    # --- Hazard scoring weights (must sum to 1.0) --------------------------
    hazard_w_slope: float = 0.5
    hazard_w_obstacle: float = 0.3
    hazard_w_roughness: float = 0.2
    slope_reference_deg: float = 30.0  # slope at which the slope term saturates

    # --- Terrain classification thresholds ---------------------------------
    # Crater fields are a few LARGE closed features, so obstacle *area* is the
    # discriminating signal - a rock field has a far higher obstacle count but
    # covers much less ground.
    crater_field_area_fraction: float = 0.20
    sandy_plain_max_slope_deg: float = 6.0
    sandy_plain_max_roughness: float = 0.12
    sandy_plain_max_area_fraction: float = 0.10

    # --- Path planning ------------------------------------------------------
    planning_grid_max_dim: int = 192  # hazard map is downsampled to this for A*
    energy_slope_coefficient: float = 0.5  # k in energy_factor = 1 + k*|slope|
    # Lethal-hazard layer: cells at or above this are removed from the graph.
    # The (1 + hazard) cost term alone caps out at 2x and can never outweigh a
    # detour of more than one cell, so cost shaping needs a hard layer beside it.
    lethal_hazard_threshold: float = 0.85

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
