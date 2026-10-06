"""Structured logging, request correlation, and metrics."""

from __future__ import annotations

import json
import logging
import threading
import time
import uuid
from collections import defaultdict
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.routing import Match

request_id_var: ContextVar[str | None] = ContextVar("astra_request_id", default=None)

# Seconds, spaced around what this system does: sub-ms reads, tens of ms for a
# plan, seconds for a study. Uniform buckets would collapse into one bin.
LATENCY_BUCKETS = (0.005, 0.025, 0.1, 0.5, 1.0, 2.5, 10.0, 30.0)


class JsonFormatter(logging.Formatter):
    """One JSON object per line, with the request id folded in when there is one."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        request_id = request_id_var.get()
        if request_id:
            payload["request_id"] = request_id
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        # Anything passed as extra={"astra_...": v} rides along, prefix stripped.
        for key, value in getattr(record, "__dict__", {}).items():
            if key.startswith("astra_"):
                payload[key[6:]] = value
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", json_logs: bool = True) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(
        JsonFormatter()
        if json_logs
        else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # uvicorn installs its own handlers; without this every line is emitted twice,
    # once structured and once not.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        logger = logging.getLogger(name)
        logger.handlers[:] = []
        logger.propagate = True


class Metrics:
    """Counters and latency histograms, in Prometheus text format."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: dict[tuple[str, tuple], float] = defaultdict(float)
        self._gauges: dict[tuple[str, tuple], float] = {}
        self._histograms: dict[tuple[str, tuple], list[float]] = defaultdict(
            lambda: [0.0] * (len(LATENCY_BUCKETS) + 2)  # buckets + count + sum
        )

    @staticmethod
    def _key(labels: dict[str, str] | None) -> tuple:
        return tuple(sorted((labels or {}).items()))

    def increment(self, name: str, labels: dict[str, str] | None = None, amount: float = 1.0):
        with self._lock:
            self._counters[(name, self._key(labels))] += amount

    def set_gauge(self, name: str, value: float, labels: dict[str, str] | None = None):
        with self._lock:
            self._gauges[(name, self._key(labels))] = value

    def observe(self, name: str, seconds: float, labels: dict[str, str] | None = None):
        with self._lock:
            slot = self._histograms[(name, self._key(labels))]
            for index, bound in enumerate(LATENCY_BUCKETS):
                if seconds <= bound:
                    slot[index] += 1
            slot[-2] += 1  # count
            slot[-1] += seconds  # sum

    def timer(self, name: str, labels: dict[str, str] | None = None):
        return _Timer(self, name, labels)

    def render(self) -> str:
        """Prometheus text exposition format."""
        lines: list[str] = []
        with self._lock:
            counters = dict(self._counters)
            gauges = dict(self._gauges)
            histograms = {key: list(value) for key, value in self._histograms.items()}

        for name in sorted({name for name, _ in counters}):
            lines.append(f"# TYPE {name} counter")
            for (metric, labels), value in sorted(counters.items(), key=lambda kv: str(kv[0])):
                if metric == name:
                    lines.append(f"{name}{_labels(labels)} {value:g}")

        for name in sorted({name for name, _ in gauges}):
            lines.append(f"# TYPE {name} gauge")
            for (metric, labels), value in sorted(gauges.items(), key=lambda kv: str(kv[0])):
                if metric == name:
                    lines.append(f"{name}{_labels(labels)} {value:g}")

        for name in sorted({name for name, _ in histograms}):
            lines.append(f"# TYPE {name} histogram")
            for (metric, labels), slot in sorted(histograms.items(), key=lambda kv: str(kv[0])):
                if metric != name:
                    continue
                cumulative = 0.0
                for index, bound in enumerate(LATENCY_BUCKETS):
                    cumulative = slot[index]
                    lines.append(f"{name}_bucket{_labels(labels, le=str(bound))} {cumulative:g}")
                lines.append(f"{name}_bucket{_labels(labels, le='+Inf')} {slot[-2]:g}")
                lines.append(f"{name}_count{_labels(labels)} {slot[-2]:g}")
                lines.append(f"{name}_sum{_labels(labels)} {slot[-1]:g}")

        return "\n".join(lines) + "\n"

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._gauges.clear()
            self._histograms.clear()


def _labels(labels: tuple, **extra: str) -> str:
    pairs = [f'{key}="{value}"' for key, value in labels]
    pairs += [f'{key}="{value}"' for key, value in extra.items()]
    return "{" + ",".join(pairs) + "}" if pairs else ""


class _Timer:
    __slots__ = ("labels", "metrics", "name", "started")

    def __init__(self, metrics: Metrics, name: str, labels: dict[str, str] | None) -> None:
        self.metrics = metrics
        self.name = name
        self.labels = labels
        self.started = 0.0

    def __enter__(self) -> _Timer:
        self.started = time.perf_counter()
        return self

    def __exit__(self, *_exc) -> None:
        self.metrics.observe(self.name, time.perf_counter() - self.started, self.labels)


METRICS = Metrics()


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Correlation id, access log, and request metrics."""

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        logger = logging.getLogger("astrasynth.http")

        # Reset in one place, after everything that logs - including the summary
        # line below, which is the line most worth correlating.
        try:
            try:
                response = await call_next(request)
            except Exception:
                duration = time.perf_counter() - started
                route = _route_template(request)
                METRICS.increment(
                    "astra_http_requests_total",
                    {"method": request.method, "route": route, "status": "500"},
                )
                METRICS.observe("astra_http_request_seconds", duration, {"route": route})
                logger.exception(
                    "request failed",
                    extra={"astra_route": route, "astra_method": request.method},
                )
                raise

            duration = time.perf_counter() - started
            route = _route_template(request)
            METRICS.increment(
                "astra_http_requests_total",
                {"method": request.method, "route": route, "status": str(response.status_code)},
            )
            METRICS.observe("astra_http_request_seconds", duration, {"route": route})
            response.headers["X-Request-ID"] = request_id
            logger.info(
                "request",
                extra={
                    "astra_method": request.method,
                    "astra_route": route,
                    "astra_status": response.status_code,
                    "astra_duration_ms": round(duration * 1000, 2),
                },
            )
            return response
        finally:
            request_id_var.reset(token)


def _route_template(request: Request) -> str:
    """The matched route's path template, for use as a metric label."""
    route = request.scope.get("route")
    path = getattr(route, "path", None)
    if path:
        return path

    partial: str | None = None
    for candidate in getattr(request.app, "routes", ()):
        template = getattr(candidate, "path", None)
        if not template:
            continue
        try:
            match, _ = candidate.matches(request.scope)
        except Exception:  # a route type that cannot be matched offline
            continue
        if match is Match.FULL:
            return template
        if match is Match.PARTIAL and partial is None:
            # The path exists but the method does not: a 405, and the template is
            # still the useful label.
            partial = template
    return partial or "unmatched"
