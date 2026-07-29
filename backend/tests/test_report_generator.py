"""Report generation: the "AI narrates, it does not compute" boundary.

Three things are being defended here:

1. Without an API key the report still contains every number - only the prose
   is missing. That is the fallback path, and it is the default in CI.
2. A model that cites a segment ID which is not in the structured context has
   that citation discarded rather than surfaced.
3. Malformed model output degrades to the fallback instead of raising.

No network call is made by any test in this file.
"""

from __future__ import annotations

import json

import pytest

from app.config import Settings
from app.services.report_generator import (
    _fallback_narrative,
    _validate_narrative,
    build_structured_context,
    generate_narrative,
)
from app.services.path_planner import RoverSpec
from app.services.risk_engine import assess_mission_risk

from tests.test_risk_engine import make_path


@pytest.fixture
def context(settings: Settings) -> dict:
    rover = RoverSpec(6.0, 25.0, 0.003)
    path = make_path([0.1, 0.85, 0.2, 0.6, 0.3], energy_kwh=3.9, distance_m=1287.0)
    risk = assess_mission_risk(path, rover, settings)

    return build_structured_context(
        mission_id="00000000-0000-0000-0000-000000000001",
        mission_name="Test traverse",
        terrain_source="synthetic",
        terrain_metadata={
            "slope": {"mean_deg": 2.4, "max_deg": 20.5},
            "coverage_m": {"width": 1024.0, "height": 1024.0},
            "hazard_calculation_basis": {
                "aggregate": {"mean_hazard": 0.061, "max_hazard": 0.653},
                "weights": {"slope": 0.5, "obstacle_proximity": 0.3, "roughness": 0.2},
            },
        },
        obstacle_count=13,
        classification="sandy_plain",
        path=path,
        rover=rover,
        rover_name="Survey-Class (medium)",
        risk=risk,
    )


class TestStructuredContext:
    def test_carries_every_number_the_report_needs(self, context):
        assert context["risk_score"] in {"LOW", "MEDIUM", "HIGH"}
        assert context["feasibility"] in {"FEASIBLE", "FEASIBLE_WITH_MARGIN", "INFEASIBLE"}
        assert context["path_summary"]["total_distance_m"] == 1287.0
        assert context["terrain_summary"]["obstacle_count"] == 13
        assert context["rover_constraints"]["battery_capacity_kwh"] == 6.0

    def test_includes_the_calculation_basis_for_every_stage(self, context):
        basis = context["calculation_basis"]
        assert "hazard" in basis and "path" in basis and "risk" in basis
        assert "risk_formula" in basis["risk"]
        assert basis["hazard"]["weights"]["slope"] == 0.5

    def test_is_json_serialisable(self, context):
        """It is stored in a JSONB column and sent to the model as JSON."""
        assert json.loads(json.dumps(context))["mission_name"] == "Test traverse"


class TestFallback:
    def test_used_when_no_api_key_is_configured(self, context):
        settings = Settings(_env_file=None, anthropic_api_key="")
        narrative = generate_narrative(context, settings)

        assert narrative["generated_by"] == "template_fallback"
        assert "no ANTHROPIC_API_KEY" in narrative["fallback_reason"]

    def test_fallback_contains_the_same_figures_as_the_context(self, context):
        narrative = _fallback_narrative(context, "test")

        assert str(context["path_summary"]["total_distance_m"]) in narrative["summary"]
        assert str(context["rover_constraints"]["battery_capacity_kwh"]) in narrative["summary"]
        assert context["feasibility"] in narrative["summary"]

    def test_fallback_cites_only_real_segments(self, context):
        narrative = _fallback_narrative(context, "test")
        valid = {s["segment_id"] for s in context["path_summary"]["high_hazard_segments"]}

        assert narrative["top_risks"]
        assert all(risk["segment_id"] in valid for risk in narrative["top_risks"])

    @pytest.mark.parametrize(
        ("feasibility", "expected"),
        [
            ("INFEASIBLE", "Do not execute"),
            ("FEASIBLE_WITH_MARGIN", "contingency"),
            ("FEASIBLE", "Execute as planned"),
        ],
    )
    def test_recommendation_tracks_feasibility(self, context, feasibility, expected):
        context = {**context, "feasibility": feasibility}
        assert expected in _fallback_narrative(context, "test")["recommendation"]


class TestNarrativeValidation:
    def _valid_segment(self, context) -> int:
        return context["path_summary"]["high_hazard_segments"][0]["segment_id"]

    def test_accepts_a_well_formed_response(self, context):
        segment = self._valid_segment(context)
        raw = json.dumps(
            {
                "summary": "Traverse is feasible.",
                "top_risks": [{"segment_id": segment, "reason": "steep"}],
                "recommendation": "Proceed.",
            }
        )
        result = _validate_narrative(raw, context)

        assert result["generated_by"] == "llm"
        assert result["top_risks"] == [{"segment_id": segment, "reason": "steep"}]
        assert result["dropped_citations"] == 0

    def test_drops_hallucinated_segment_ids(self, context):
        """The single most important guard in this module."""
        segment = self._valid_segment(context)
        raw = json.dumps(
            {
                "summary": "Two risky segments identified.",
                "top_risks": [
                    {"segment_id": segment, "reason": "real"},
                    {"segment_id": 99999, "reason": "invented"},
                ],
                "recommendation": "Proceed with care.",
            }
        )
        result = _validate_narrative(raw, context)

        assert [r["segment_id"] for r in result["top_risks"]] == [segment]
        assert result["dropped_citations"] == 1

    def test_strips_code_fences(self, context):
        """The prompt forbids fences; models add them anyway."""
        raw = "```json\n" + json.dumps(
            {"summary": "s", "top_risks": [], "recommendation": "r"}
        ) + "\n```"
        assert _validate_narrative(raw, context)["summary"] == "s"

    def test_rejects_a_response_missing_a_required_key(self, context):
        raw = json.dumps({"summary": "s", "top_risks": []})  # no recommendation
        with pytest.raises(ValueError, match="missing required key: recommendation"):
            _validate_narrative(raw, context)

    def test_rejects_non_object_json(self, context):
        with pytest.raises(ValueError, match="not an object"):
            _validate_narrative("[1, 2, 3]", context)

    def test_rejects_malformed_json(self, context):
        with pytest.raises(json.JSONDecodeError):
            _validate_narrative("{not json at all", context)


class TestFailureIsolation:
    def test_model_exception_degrades_instead_of_propagating(self, context, monkeypatch):
        """A mission report must never 500 because the model was unavailable."""
        settings = Settings(_env_file=None, anthropic_api_key="sk-test-not-real")

        class ExplodingClient:
            def __init__(self, *args, **kwargs):
                raise RuntimeError("connection refused")

        import anthropic

        monkeypatch.setattr(anthropic, "Anthropic", ExplodingClient)
        narrative = generate_narrative(context, settings)

        assert narrative["generated_by"] == "template_fallback"
        assert "connection refused" in narrative["fallback_reason"]
        # The numbers survive the failure - that is the entire point.
        assert str(context["path_summary"]["total_distance_m"]) in narrative["summary"]
