"""What the rover believes, as distinct from what is true.

The distinction this module enforces
------------------------------------
V1 had one hazard map and every component read it. That is a simulation in which
the rover is omniscient: it plans around a boulder it has no way of having seen.
Every result derived from it - route cost, replan counts, mission success rates -
is optimistic by an unmeasurable amount.

So there are now three separate things, and only two of them are ever visible to
a planner:

* **Reality** - the ground-truth hazard field. Held by the simulator. A planner
  never receives it.
* **Belief** - what the rover currently holds to be true. Starts as the orbital
  prior and is revised by observation. This is what planners search.
* **Observation** - a noisy sensor reading of reality at one place and time.

Belief is a distribution, not a number
--------------------------------------
Each cell carries a mean and a standard deviation. The orbital prior is a wide
distribution centred on the orbital hazard estimate; an observation is a narrow
one centred on the noisy reading; the posterior is the standard product of two
Gaussians:

    1/sigma_post^2 = 1/sigma_prior^2 + 1/sigma_obs^2
    mu_post        = sigma_post^2 * (mu_prior/sigma_prior^2 + z/sigma_obs^2)

Repeated observations of the same cell therefore tighten the estimate rather
than overwriting it, which is what makes the second look at a cell cost the
planner nothing (:meth:`update` returns no change) instead of triggering a
spurious replan.

Uncertainty has to reach the planner
------------------------------------
:meth:`planning_hazard` returns ``mu + k * sigma``, not ``mu``. A planner given
only the mean treats "known 0.4" and "could be anywhere between 0.1 and 0.7"
as the same ground, and takes the shortcut through the unsurveyed region every
time. Charging ``k`` standard deviations makes uncertain ground cost more than
equally-hazardous known ground, so a longer route over surveyed terrain can win
- which is the behaviour an actual mission planner wants and the reason the
uncertainty is modelled at all. ``k = 0`` recovers the V1 behaviour exactly.
"""

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
        """Start from an orbital map of the given per-cell uncertainty.

        ``prior_sigma`` is the standard deviation attributed to *every* cell of
        an orbital estimate. 0.25 on a hazard scale bounded to [0, 1] says the
        orbital map is informative but not to be trusted within a quarter of the
        whole range - deliberately wide, because that is the situation that
        makes surveying worth doing.
        """
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
        """The hazard surface a planner should search: ``mu + k * sigma``.

        Clipped to [0, 1] so the lethal-hazard threshold and the risk tiers keep
        meaning what they meant in V1.
        """
        if uncertainty_weight < 0:
            raise ValueError("uncertainty_weight must be non-negative")
        return np.clip(self.mean + uncertainty_weight * self.sigma, 0.0, 1.0)

    def confidence(self) -> np.ndarray:
        """Per-cell confidence in [0, 1], for the uncertainty map layer.

        ``1 - sigma / sigma_max``, where ``sigma_max`` is the widest belief
        currently held anywhere on the map. Relative rather than absolute
        because the useful question for an operator looking at the map is which
        ground is least known, not what the number is.
        """
        widest = float(self.sigma.max())
        if widest <= 0:
            return np.ones(self.shape, dtype=np.float64)
        return 1.0 - (self.sigma / widest)

    def update(
        self, observations: dict[Cell, float], observation_sigma: float
    ) -> dict[Cell, float]:
        """Fold noisy readings into the belief; return the cells that moved.

        The return value is the *changed* set, filtered by
        ``SIGNIFICANT_BELIEF_CHANGE``, in the form D* Lite wants. Observing
        ground the rover already knows well produces an empty dictionary and
        therefore no repair, which is the property that keeps the replan count
        an honest measure of how much the world surprised the rover.
        """
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
