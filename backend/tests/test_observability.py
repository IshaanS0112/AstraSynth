"""Metrics and structured logging.

Hand-rolled metrics are usually a mistake, and the reason is always the
histogram: Prometheus buckets are *cumulative* and must carry a ``+Inf`` equal
to the observation count. A scraper fed non-cumulative buckets does not error -
it reports numbers that are quietly wrong. So that invariant is tested directly
rather than eyeballed in a sample of output.
"""

from __future__ import annotations

import json
import logging

import pytest

from app.observability import LATENCY_BUCKETS, JsonFormatter, Metrics, request_id_var


@pytest.fixture
def metrics() -> Metrics:
    return Metrics()


class TestCounters:
    def test_counters_accumulate_per_label_set(self, metrics):
        metrics.increment("astra_jobs_total", {"kind": "monte_carlo", "outcome": "SUCCEEDED"})
        metrics.increment("astra_jobs_total", {"kind": "monte_carlo", "outcome": "SUCCEEDED"})
        metrics.increment("astra_jobs_total", {"kind": "route_study", "outcome": "FAILED"})

        rendered = metrics.render()
        assert 'astra_jobs_total{kind="monte_carlo",outcome="SUCCEEDED"} 2' in rendered
        assert 'astra_jobs_total{kind="route_study",outcome="FAILED"} 1' in rendered

    def test_label_order_does_not_create_a_second_series(self):
        """Labels are a set, not a sequence; ordering them keeps that true."""
        metrics = Metrics()
        metrics.increment("m", {"a": "1", "b": "2"})
        metrics.increment("m", {"b": "2", "a": "1"})
        assert 'm{a="1",b="2"} 2' in metrics.render()

    def test_a_gauge_replaces_rather_than_accumulates(self, metrics):
        metrics.set_gauge("astra_jobs_queue_depth", 5, {"status": "QUEUED"})
        metrics.set_gauge("astra_jobs_queue_depth", 2, {"status": "QUEUED"})
        assert 'astra_jobs_queue_depth{status="QUEUED"} 2' in metrics.render()


class TestHistogram:
    def test_buckets_are_cumulative_and_end_at_the_count(self, metrics):
        observations = [0.001, 0.03, 0.4, 3.0, 40.0]
        for value in observations:
            metrics.observe("astra_http_request_seconds", value, {"route": "/x"})

        lines = metrics.render().splitlines()
        buckets = [line for line in lines if "_bucket{" in line]
        values = [float(line.rsplit(" ", 1)[1]) for line in buckets]

        assert values == sorted(values), "buckets must be non-decreasing"
        assert values[-1] == len(observations), "+Inf must equal the observation count"
        assert f'astra_http_request_seconds_count{{route="/x"}} {len(observations)}' in lines
        total = next(line for line in lines if "_sum{" in line)
        assert float(total.rsplit(" ", 1)[1]) == pytest.approx(sum(observations))

    def test_every_declared_bucket_is_emitted_plus_inf(self, metrics):
        metrics.observe("h", 0.01)
        buckets = [line for line in metrics.render().splitlines() if "_bucket" in line]
        assert len(buckets) == len(LATENCY_BUCKETS) + 1

    def test_an_observation_on_a_bound_falls_inside_it(self, metrics):
        """Prometheus buckets are `le` - less than *or equal*."""
        metrics.observe("h", LATENCY_BUCKETS[0])
        first = next(line for line in metrics.render().splitlines() if "_bucket" in line)
        assert first.endswith(" 1")

    def test_the_timer_records_an_observation(self, metrics):
        with metrics.timer("astra_job_seconds", {"kind": "route_study"}):
            pass
        assert 'astra_job_seconds_count{kind="route_study"} 1' in metrics.render()


class TestExpositionFormat:
    def test_every_metric_declares_its_type(self, metrics):
        metrics.increment("c")
        metrics.set_gauge("g", 1)
        metrics.observe("h", 0.5)
        rendered = metrics.render()
        assert "# TYPE c counter" in rendered
        assert "# TYPE g gauge" in rendered
        assert "# TYPE h histogram" in rendered

    def test_an_unlabelled_metric_renders_without_braces(self, metrics):
        metrics.increment("astra_thing_total")
        assert "astra_thing_total 1" in metrics.render()

    def test_output_ends_with_a_newline(self, metrics):
        """Scrapers reject a final line without one."""
        metrics.increment("c")
        assert metrics.render().endswith("\n")


class TestStructuredLogging:
    def _record(self, **extra) -> dict:
        record = logging.LogRecord(
            "astrasynth.test", logging.INFO, __file__, 1, "something happened", None, None
        )
        for key, value in extra.items():
            setattr(record, key, value)
        return json.loads(JsonFormatter().format(record))

    def test_a_line_is_one_json_object(self):
        payload = self._record()
        assert payload["level"] == "INFO"
        assert payload["message"] == "something happened"
        assert payload["logger"] == "astrasynth.test"

    def test_extra_fields_ride_along_without_a_bespoke_formatter(self):
        payload = self._record(astra_route="/missions/{id}", astra_duration_ms=12.5)
        assert payload["route"] == "/missions/{id}"
        assert payload["duration_ms"] == 12.5

    def test_unprefixed_attributes_are_not_leaked(self):
        """Only `astra_`-prefixed extras are ours; the rest is LogRecord internals."""
        payload = self._record(pathname="/secret/path", astra_kind="monte_carlo")
        assert payload["kind"] == "monte_carlo"
        assert "pathname" not in payload

    def test_the_request_id_is_attached_when_one_is_in_scope(self):
        token = request_id_var.set("abc123")
        try:
            assert self._record()["request_id"] == "abc123"
        finally:
            request_id_var.reset(token)

    def test_no_request_id_outside_a_request(self):
        assert "request_id" not in self._record()


# --- the endpoints these feed ------------------------------------------------
#
# Needs a database, because readiness is only meaningful if it actually checks
# one and /metrics reads the queue depth.

import os  # noqa: E402
import sys  # noqa: E402
from pathlib import Path  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

DATABASE_URL = os.getenv(
    "TEST_DATABASE_URL", "postgresql+psycopg2://astra:astra@localhost:5432/astrasynth"
)


def _database_available() -> bool:
    try:
        from sqlalchemy import create_engine, text

        engine = create_engine(DATABASE_URL, connect_args={"connect_timeout": 2})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    if not _database_available():
        pytest.skip(f"PostgreSQL not reachable at {DATABASE_URL}")
    os.environ["DATABASE_URL"] = DATABASE_URL
    os.environ["STORAGE_DIR"] = str(tmp_path_factory.mktemp("storage_obs"))
    os.environ["ANTHROPIC_API_KEY"] = ""
    os.environ["WORKER_THREADS"] = "0"

    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import app

    get_settings.cache_clear()
    with TestClient(app) as test_client:
        yield test_client


class TestEndpoints:
    def test_every_response_carries_a_request_id(self, client):
        response = client.get("/health")
        assert response.status_code == 200
        assert response.headers["X-Request-ID"]

    def test_a_supplied_request_id_is_echoed_rather_than_replaced(self, client):
        """So a caller can correlate its own logs with the server's."""
        response = client.get("/health", headers={"X-Request-ID": "caller-chosen-id"})
        assert response.headers["X-Request-ID"] == "caller-chosen-id"

    def test_liveness_does_not_depend_on_the_database(self, client):
        """A liveness probe that fails during a DB outage just kills the API."""
        assert client.get("/health").json() == {"status": "ok"}

    def test_readiness_reports_what_it_checked(self, client):
        payload = client.get("/ready").json()
        assert payload["ready"] is True
        assert payload["database"] is True
        assert payload["storage"] is True

    def test_metrics_are_exposed_in_prometheus_format(self, client):
        client.get("/health")
        body = client.get("/metrics").text

        assert "# TYPE astra_http_requests_total counter" in body
        assert "astra_http_request_seconds_bucket" in body
        assert 'le="+Inf"' in body

    def test_requests_are_labelled_by_route_template_not_path(self, client):
        """Labelling by path mints a series per mission id and never stops."""
        missing = "00000000-0000-0000-0000-000000000000"
        client.get(f"/missions/{missing}")
        body = client.get("/metrics").text

        assert missing not in body, "a path parameter leaked into a metric label"
        assert 'route="/missions/{mission_id}"' in body


class TestCorrelationReachesTheSummaryLine:
    """The line carrying route, status and duration must carry the id too.

    The reset used to sit in a `finally` around `call_next`, which put it *before*
    the summary line - so the single most useful line in a request was the only
    one without a correlation id on it, and joining a slow request to what it did
    was impossible. Nothing failed; the field was simply absent.
    """

    def _captured(self, app, path: str, **kwargs):
        import io
        import json
        import logging

        from fastapi.testclient import TestClient

        from app.observability import JsonFormatter

        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        root = logging.getLogger()
        previous, level = root.handlers[:], root.level
        root.handlers[:] = [handler]
        root.setLevel(logging.INFO)
        try:
            response = TestClient(app, raise_server_exceptions=False).get(path, **kwargs)
        finally:
            root.handlers[:] = previous
            root.setLevel(level)
        lines = [json.loads(line) for line in stream.getvalue().splitlines() if line.strip()]
        return response, lines

    def _app(self):
        from fastapi import FastAPI

        from app.observability import RequestContextMiddleware

        app = FastAPI()

        @app.get("/things/{thing_id}")
        def thing(thing_id: str):
            return {"id": thing_id}

        @app.get("/boom")
        def boom():
            raise RuntimeError("deliberate")

        app.add_middleware(RequestContextMiddleware)
        return app

    def test_the_summary_line_carries_the_request_id(self):
        response, lines = self._captured(self._app(), "/things/abc")

        summary = [line for line in lines if line.get("message") == "request"]
        assert summary, f"no summary line in {lines}"
        assert summary[0]["request_id"] == response.headers["X-Request-ID"]
        assert summary[0]["route"] == "/things/{thing_id}"

    def test_an_incoming_id_is_honoured_so_a_trace_survives_a_proxy(self):
        response, lines = self._captured(
            self._app(), "/things/abc", headers={"X-Request-ID": "from-the-proxy"}
        )

        assert response.headers["X-Request-ID"] == "from-the-proxy"
        summary = [line for line in lines if line.get("message") == "request"]
        assert summary[0]["request_id"] == "from-the-proxy"

    def test_a_failing_request_is_still_correlated(self):
        _, lines = self._captured(self._app(), "/boom")

        failed = [line for line in lines if line.get("message") == "request failed"]
        assert failed and failed[0]["request_id"]


class TestRouteLabelForShortCircuitedResponses:
    """A 401 on a real endpoint must not be labelled the same as a 404.

    `scope["route"]` is only set once the router runs, so a response produced by
    an inner middleware had no route - and reporting it as `unmatched` lost which
    endpoint was called in exactly the case an operator cares about, while
    colliding with the label for URLs that genuinely do not exist.
    """

    def _app(self, reject: bool):
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from starlette.middleware.base import BaseHTTPMiddleware

        from app.observability import RequestContextMiddleware

        app = FastAPI()

        @app.get("/missions/{mission_id}")
        def mission(mission_id: str):
            return {"id": mission_id}

        @app.post("/missions")
        def create():
            return {"created": True}

        class Gate(BaseHTTPMiddleware):
            async def dispatch(self, request, call_next):
                if reject:
                    return JSONResponse(status_code=401, content={"detail": "no"})
                return await call_next(request)

        app.add_middleware(Gate)
        app.add_middleware(RequestContextMiddleware)
        return app

    def _label_for(self, app, method: str, path: str) -> str:
        from fastapi.testclient import TestClient

        from app.observability import METRICS

        before = METRICS.render()
        TestClient(app).request(method, path)
        added = set(METRICS.render().splitlines()) - set(before.splitlines())
        routes = [
            line.split('route="', 1)[1].split('"', 1)[0]
            for line in added
            if "astra_http_requests_total{" in line and 'route="' in line
        ]
        assert routes, f"no new request series in {added}"
        return routes[0]

    def test_a_rejection_is_labelled_with_the_endpoint_it_hit(self):
        assert self._label_for(self._app(reject=True), "GET", "/missions/abc") == (
            "/missions/{mission_id}"
        )

    def test_a_url_that_does_not_exist_is_still_unmatched(self):
        assert self._label_for(self._app(reject=True), "GET", "/nope/nope") == "unmatched"

    def test_a_wrong_method_on_a_real_path_keeps_that_path_as_the_label(self):
        """A 405 is about the method, not the path; the path is the useful label."""
        assert self._label_for(self._app(reject=True), "DELETE", "/missions") == "/missions"

    def test_the_router_path_is_still_preferred_when_the_router_ran(self):
        assert self._label_for(self._app(reject=False), "GET", "/missions/abc") == (
            "/missions/{mission_id}"
        )
