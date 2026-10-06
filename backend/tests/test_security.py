"""The optional shared-secret layer."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.security import (
    API_KEY_HEADER,
    MIN_KEY_LENGTH,
    ApiKeyMiddleware,
    MisconfiguredKeyError,
    declare_security_scheme,
    validate_api_key,
)

GOOD_KEY = "k" * 40


def build_app(api_key: str, *, static_dir: Path | None = None) -> FastAPI:
    """The same middleware order as app/main.py, over two trivial routes."""
    app = FastAPI(title="test", version="0")

    @app.get("/health")
    def health():
        return {"status": "ok"}

    @app.get("/missions")
    def missions():
        return {"missions": []}

    if static_dir is not None:
        from fastapi.staticfiles import StaticFiles

        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    app.add_middleware(ApiKeyMiddleware, api_key=api_key)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    declare_security_scheme(app, api_key)
    return app


class TestDisabledByDefault:
    def test_no_key_configured_leaves_everything_open(self):
        """The offline demo, the Compose quick start and CI all depend on this."""
        client = TestClient(build_app(""))
        assert client.get("/missions").status_code == 200
        assert client.get("/health").status_code == 200

    def test_an_open_api_does_not_advertise_a_padlock(self):
        schema = TestClient(build_app("")).get("/openapi.json").json()
        assert "security" not in schema
        assert "securitySchemes" not in schema.get("components", {})


class TestEnforcement:
    def test_a_request_with_no_credential_is_401(self):
        response = TestClient(build_app(GOOD_KEY)).get("/missions")
        assert response.status_code == 401
        assert API_KEY_HEADER in response.json()["detail"]

    def test_the_documented_header_is_accepted(self):
        client = TestClient(build_app(GOOD_KEY))
        assert client.get("/missions", headers={API_KEY_HEADER: GOOD_KEY}).status_code == 200

    def test_a_bearer_token_is_accepted_too(self):
        """Every HTTP client already knows how to send this one."""
        client = TestClient(build_app(GOOD_KEY))
        response = client.get("/missions", headers={"Authorization": f"Bearer {GOOD_KEY}"})
        assert response.status_code == 200

    @pytest.mark.parametrize(
        "presented",
        [
            "",  # empty
            "wrong-key-entirely-but-long-enough",
            GOOD_KEY[:-1],  # one character short
            GOOD_KEY + "x",  # correct prefix, extra suffix
            GOOD_KEY.upper(),  # case must matter
            f" {GOOD_KEY}",  # not silently trimmed
        ],
    )
    def test_a_wrong_credential_is_401(self, presented):
        client = TestClient(build_app(GOOD_KEY))
        assert client.get("/missions", headers={API_KEY_HEADER: presented}).status_code == 401

    def test_a_correct_prefix_is_not_enough(self):
        """The check is over the whole value, not a startswith."""
        client = TestClient(build_app(GOOD_KEY))
        assert client.get("/missions", headers={API_KEY_HEADER: "k"}).status_code == 401


class TestExemptions:
    @pytest.mark.parametrize("path", ["/health", "/openapi.json", "/docs"])
    def test_probes_and_docs_answer_without_a_credential(self, path):
        """A liveness probe that needs a secret causes the outage it should survive."""
        client = TestClient(build_app(GOOD_KEY))
        assert client.get(path).status_code == 200

    def test_a_browser_preflight_is_not_rejected(self):
        """The preflight exists to ask whether the key header may be sent."""
        client = TestClient(build_app(GOOD_KEY))
        response = client.options(
            "/missions",
            headers={
                "Origin": "http://localhost:5173",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": API_KEY_HEADER,
            },
        )
        assert response.status_code == 200
        assert "access-control-allow-origin" in response.headers

    def test_a_401_still_carries_cors_headers(self):
        """Without this a browser reports a CORS error and hides the 401."""
        client = TestClient(build_app(GOOD_KEY))
        response = client.get("/missions", headers={"Origin": "http://localhost:5173"})
        assert response.status_code == 401
        assert response.headers.get("access-control-allow-origin") == "http://localhost:5173"

    def test_the_static_mount_is_protected(self, tmp_path):
        """The case a per-router dependency would have missed."""
        (tmp_path / "terrain.png").write_bytes(b"\x89PNG\r\n\x1a\n")
        client = TestClient(build_app(GOOD_KEY, static_dir=tmp_path))

        assert client.get("/static/terrain.png").status_code == 401
        assert (
            client.get("/static/terrain.png", headers={API_KEY_HEADER: GOOD_KEY}).status_code == 200
        )


class TestConfigurationIsChecked:
    def test_an_empty_key_is_valid_and_means_open(self):
        assert validate_api_key("") == ""

    def test_a_long_enough_key_passes_through(self):
        assert validate_api_key(GOOD_KEY) == GOOD_KEY

    @pytest.mark.parametrize("short", ["x", "hunter2", "k" * (MIN_KEY_LENGTH - 1)])
    def test_a_short_key_stops_the_deployment(self, short):
        """False protection is worse than none: someone stops guarding the network."""
        with pytest.raises(MisconfiguredKeyError) as raised:
            validate_api_key(short)
        assert str(MIN_KEY_LENGTH) in str(raised.value)

    def test_a_closed_api_advertises_the_scheme(self):
        schema = TestClient(build_app(GOOD_KEY)).get("/openapi.json").json()
        assert schema["security"] == [{"ApiKeyHeader": []}]
        scheme = schema["components"]["securitySchemes"]["ApiKeyHeader"]
        assert scheme == {"type": "apiKey", "in": "header", "name": API_KEY_HEADER}


class TestTheRealAppIsWiredThisWay:
    """The tests above build their own stack; this one checks the shipped one."""

    def test_the_stack_is_context_then_cors_then_key(self):
        from app.main import app

        assert [m.cls.__name__ for m in app.user_middleware] == [
            "RequestContextMiddleware",
            "CORSMiddleware",
            "ApiKeyMiddleware",
        ]

    def test_the_shipped_default_is_open(self):
        """Shipping a default credential would mean shipping a published one."""
        from app.config import Settings

        assert Settings().api_key == ""
