"""What the rover believes, as distinct from what is true."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from app.services.planning.grid import Cell

# Below this, a change in believed hazard is numerical noise rather than news,
# and triggering a D* Lite repair for it would be pure cost.
SIGNIFICANT_BELIEF_CHANGE = 1e-4


@dataclass(slots=True)
class BeliefState:
    """A Gaussian belief over the hazard field, one distribution per cell."""

    mean: np.ndarray
    sigma: np.ndarray
    observation_count: np.ndarray

    @classmethod
    def from_prior(cls, prior_hazard: np.ndarray, prior_sigma: float = 0.25) -> BeliefState:
        """Start from an orbital map of the given per-cell uncertainty."""
        if prior_sigma <= 0:
            raise ValueError("prior_sigma must be positive")
        mean = np.asarray(prior_hazard, dtype=np.float64).copy()
        return cls(
            mean=mean,
            sigma=np.full(mean.shape, float(prior_sigma), dtype=np.float64),
            observation_count=np.zeros(mean.shape, dtype=np.int32),
        )

    @property
    def shape(self) -> tuple[int, int]:
        return self.mean.shape  # type: ignore[return-value]

    def planning_hazard(self, uncertainty_weight: float = 0.0) -> np.ndarray:
        """The hazard surface a planner should search: ``mu + k * sigma``."""
        if uncertainty_weight < 0:
            raise ValueError("uncertainty_weight must be non-negative")
        return np.clip(self.mean + uncertainty_weight * self.sigma, 0.0, 1.0)

    def confidence(self) -> np.ndarray:
        """Per-cell confidence in [0, 1], for the uncertainty map layer."""
        widest = float(self.sigma.max())
        if widest <= 0:
            return np.ones(self.shape, dtype=np.float64)
        return 1.0 - (self.sigma / widest)

    def update(
        self, observations: dict[Cell, float], observation_sigma: float
    ) -> dict[Cell, float]:
        """Fold noisy readings into the belief; return the cells that moved."""
        if observation_sigma <= 0:
            raise ValueError("observation_sigma must be positive")

        changed: dict[Cell, float] = {}
        obs_precision = 1.0 / (observation_sigma**2)

        for cell, reading in observations.items():
            prior_mean = float(self.mean[cell])
            prior_sigma = float(self.sigma[cell])
            prior_precision = 1.0 / (prior_sigma**2)

            posterior_precision = prior_precision + obs_precision
            posterior_mean = (
                prior_mean * prior_precision + float(reading) * obs_precision
            ) / posterior_precision
            posterior_sigma = float(np.sqrt(1.0 / posterior_precision))

            self.mean[cell] = posterior_mean
            self.sigma[cell] = posterior_sigma
            self.observation_count[cell] += 1

            if abs(posterior_mean - prior_mean) >= SIGNIFICANT_BELIEF_CHANGE:
                changed[cell] = posterior_mean

        return changed

    def summary(self) -> dict:
        observed = int((self.observation_count > 0).sum())
        total = int(self.mean.size)
        return {
            "cells_total": total,
            "cells_observed": observed,
            "observed_fraction": round(observed / total, 4) if total else 0.0,
            "mean_sigma": round(float(self.sigma.mean()), 4),
            "mean_sigma_observed": (
                round(float(self.sigma[self.observation_count > 0].mean()), 4) if observed else None
            ),
            "mean_sigma_unobserved": (
                round(float(self.sigma[self.observation_count == 0].mean()), 4)
                if observed < total
                else None
            ),
        }
