"""Mission report generation: structured context -> constrained LLM narrative.

The ordering here is the whole point of the module.

1. ``build_structured_context`` assembles every number the report will contain
   from work the deterministic engines already did. Nothing in it is inferred
   by a language model.
2. ``generate_narrative`` hands that context to the model with a JSON-only
   contract and an explicit instruction not to introduce facts.
3. ``_validate_narrative`` throws away any cited segment ID that is not present
   in the context. A hallucinated citation is dropped rather than surfaced.
4. If the call fails, times out, returns unparseable output, or no API key is
   configured, ``_fallback_narrative`` produces the same report from a template.
   The numbers are identical; only the prose is missing.

So the answer to "does your AI compute the risk?" is no - it renders it. Every
figure in a generated report exists in ``structured_context``, which is stored
alongside the narrative in the database and returned by the API, so the claim
is checkable rather than asserted.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from app.config import Settings
from app.services.path_planner import PlannedPath, RoverSpec
from app.services.risk_engine import RiskAssessment

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are a planetary mission-planning assistant.

You will receive a structured mission context containing a terrain summary, a
path summary, rover constraints, and a risk score and feasibility verdict that
have ALREADY been computed by deterministic engineering code.

Your job is to narrate that context, not to recompute it.

Rules:
- Cite specific numbers from the context. Do not round them beyond one decimal.
- Identify the two highest-risk path segments. You may only reference segment
  IDs that appear in path_summary.high_hazard_segments.
- Do not invent terrain features, hardware details, mission objectives, dates,
  or any figure that is not present in the context.
- Do not contradict the provided risk_score or feasibility verdict.
- If the context is insufficient to justify a claim, omit the claim.

Respond with JSON only. No preamble, no code fences. Schema:
{
  "summary": "<3-5 sentence mission risk summary citing concrete numbers>",
  "top_risks": [
    {"segment_id": <int>, "reason": "<why this segment is risky, using context numbers>"}
  ],
  "recommendation": "<one concrete go / no-go / mitigation recommendation>"
}"""


def build_structured_context(
    *,
    mission_id: str,
    mission_name: str,
    terrain_source: str | None,
    terrain_metadata: dict[str, Any],
    obstacle_count: int,
    classification: str | None,
    path: PlannedPath,
    rover: RoverSpec,
    rover_name: str,
    risk: RiskAssessment,
) -> dict[str, Any]:
    """Freeze every computed signal into the payload the LLM will narrate.

    ``terrain_metadata`` is the ``analysis_metadata`` blob persisted by the
    terrain stage, so the context is rebuilt from what was actually stored
    rather than from a second, possibly divergent, in-memory analysis.
    """
    hazard_basis = terrain_metadata["hazard_calculation_basis"]

    return {
        "mission_id": mission_id,
        "mission_name": mission_name,
        "terrain_source": terrain_source or "unspecified",
        "terrain_summary": {
            "avg_hazard_score": hazard_basis["aggregate"]["mean_hazard"],
            "max_hazard_score": hazard_basis["aggregate"]["max_hazard"],
            "obstacle_count": obstacle_count,
            "terrain_classification": classification,
            "mean_slope_deg": terrain_metadata["slope"]["mean_deg"],
            "max_slope_deg": terrain_metadata["slope"]["max_deg"],
            "area_covered_m": terrain_metadata["coverage_m"],
        },
        "path_summary": {
            "total_distance_m": path.total_distance_m,
            "total_energy_cost_kwh": round(path.total_energy_cost_kwh, 4),
            "num_waypoints": len(path.waypoints),
            "mean_path_hazard": risk.mean_path_hazard,
            "max_path_hazard": risk.max_path_hazard,
            "max_slope_encountered_deg": risk.max_slope_encountered_deg,
            "high_hazard_segments": risk.high_hazard_segments,
            "algorithm": path.metadata.get("algorithm"),
            "nodes_expanded": path.metadata.get("nodes_expanded"),
        },
        "rover_constraints": {
            "name": rover_name,
            "battery_capacity_kwh": rover.battery_capacity_kwh,
            "energy_margin_kwh": risk.energy_margin_kwh,
            "energy_utilisation": risk.energy_utilisation,
            "max_traversable_slope_deg": rover.max_traversable_slope_deg,
            "energy_per_meter_kwh": rover.energy_per_meter_kwh,
        },
        "risk_score": risk.risk_tier.value,
        "risk_score_numeric": risk.risk_score,
        "feasibility": risk.feasibility.value,
        "calculation_basis": {
            "hazard": hazard_basis,
            "path": path.metadata,
            "risk": risk.calculation_basis,
        },
    }


def _valid_segment_ids(context: dict[str, Any]) -> set[int]:
    return {
        int(segment["segment_id"])
        for segment in context["path_summary"]["high_hazard_segments"]
    }


def _fallback_narrative(context: dict[str, Any], reason: str) -> dict[str, Any]:
    """Template report built from the same numbers, no model involved."""
    segments = context["path_summary"]["high_hazard_segments"][:2]
    path_summary = context["path_summary"]
    rover = context["rover_constraints"]

    worst = segments[0] if segments else None
    worst_text = (
        f"Highest-hazard segment: {worst['segment_id']} at hazard {worst['hazard_score']}."
        if worst
        else "No high-hazard segments recorded."
    )

    summary = (
        f"Path risk: {context['risk_score']} (score {context['risk_score_numeric']}). "
        f"Feasibility: {context['feasibility']}. "
        f"Traverse covers {path_summary['total_distance_m']} m across "
        f"{path_summary['num_waypoints']} waypoints, drawing "
        f"{path_summary['total_energy_cost_kwh']} kWh of a "
        f"{rover['battery_capacity_kwh']} kWh battery "
        f"({rover['energy_margin_kwh']} kWh margin). {worst_text}"
    )

    return {
        "summary": summary,
        "top_risks": [
            {
                "segment_id": segment["segment_id"],
                "reason": (
                    f"Hazard score {segment['hazard_score']} at "
                    f"({segment['x']}, {segment['y']}), local slope "
                    f"{segment['slope_deg']} deg."
                ),
            }
            for segment in segments
        ],
        "recommendation": _fallback_recommendation(context),
        "generated_by": "template_fallback",
        "fallback_reason": reason,
    }


def _fallback_recommendation(context: dict[str, Any]) -> str:
    feasibility = context["feasibility"]
    if feasibility == "INFEASIBLE":
        return (
            "Do not execute: projected energy draw exceeds battery capacity. "
            "Shorten the traverse or select a lower-hazard corridor."
        )
    if feasibility == "FEASIBLE_WITH_MARGIN":
        return (
            f"Execute with contingency planning: only "
            f"{context['rover_constraints']['energy_margin_kwh']} kWh of reserve remains."
        )
    return "Execute as planned; energy reserve and hazard exposure are both within limits."


def _validate_narrative(raw: str, context: dict[str, Any]) -> dict[str, Any]:
    """Parse the model output and strip anything not backed by the context."""
    text = raw.strip()
    if text.startswith("```"):
        # Defensive: the contract says no code fences, models sometimes add them.
        text = text.split("```")[1]
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("Model returned JSON that is not an object")
    for key in ("summary", "top_risks", "recommendation"):
        if key not in parsed:
            raise ValueError(f"Model response missing required key: {key}")

    allowed = _valid_segment_ids(context)
    verified: list[dict] = []
    dropped: list[Any] = []
    for risk in parsed.get("top_risks") or []:
        try:
            segment_id = int(risk["segment_id"])
        except (KeyError, TypeError, ValueError):
            dropped.append(risk)
            continue
        if segment_id in allowed:
            verified.append({"segment_id": segment_id, "reason": str(risk.get("reason", ""))})
        else:
            dropped.append(risk)

    if dropped:
        logger.warning(
            "Dropped %d hallucinated segment citation(s) not present in context: %s",
            len(dropped),
            dropped,
        )

    return {
        "summary": str(parsed["summary"]),
        "top_risks": verified,
        "recommendation": str(parsed["recommendation"]),
        "generated_by": "llm",
        "dropped_citations": len(dropped),
    }


def generate_narrative(context: dict[str, Any], settings: Settings) -> dict[str, Any]:
    """Return ``(narrative)`` from the LLM, or the template fallback on any failure."""
    if not settings.anthropic_api_key:
        return _fallback_narrative(context, "no ANTHROPIC_API_KEY configured")

    try:
        from anthropic import Anthropic

        client = Anthropic(
            api_key=settings.anthropic_api_key, timeout=settings.llm_timeout_seconds
        )
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=settings.llm_max_tokens,
            system=SYSTEM_PROMPT,
            messages=[
                {
                    "role": "user",
                    "content": json.dumps(context, indent=2),
                },
                # Prefilling the opening brace makes the JSON-only contract
                # mechanical rather than a request the model may preamble past.
                {"role": "assistant", "content": "{"},
            ],
        )
        raw = "{" + response.content[0].text
        return _validate_narrative(raw, context)

    except json.JSONDecodeError as exc:
        logger.warning("LLM returned unparseable JSON, falling back: %s", exc)
        return _fallback_narrative(context, f"unparseable model output: {exc}")
    except Exception as exc:  # noqa: BLE001 - the report must degrade, never 500
        logger.warning("LLM narrative generation failed, falling back: %s", exc)
        return _fallback_narrative(context, f"{type(exc).__name__}: {exc}")
