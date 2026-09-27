"""Structured logging, request correlation, and metrics.

Three things, all in service of one question: when a study is slow or a job is
stuck, can you find out why from outside the process?

**Structured logs.** Human-readable lines are fine until you need to ask "which
requests for mission X took over a second", and then they are a regular
expression. Every log line here is one JSON object, so that question is a filter
rather than a parse.

**Request correlation.** A traverse simulation touches the API, the pipeline,
four planners and the database. Without an id threaded through, the log lines
from one slow request are interleaved with everyone else's and cannot be
separated afterwards. The id is generated per request, attached to every log
record emitted while handling it via a ``ContextVar``, and returned in the
``X-Request-ID`` header so a client can quote it in a bug report.

**Metrics.** Deliberately hand-rolled rather than pulling in a client library:
the whole surface needed here is counters and a latency histogram, the
Prometheus text format is a dozen lines to emit, and a research project should
not take an operational dependency to count four things. The histogram uses
explicit bucket bounds and cumulative counts, which is what the format requires -
getting that wrong is the usual reason hand-rolled metrics are a bad idea, so it
is tested.
"""

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

# Seconds. Chosen around what this system actually does: sub-millisecond reads,
# tens of milliseconds for a plan, seconds for a study. Uniform buckets would
# put every interesting value in one bin.
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
        # Anything passed as `extra=` rides along, so a call site can attach the
        # mission id or a planner's node count without a bespoke formatter.
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
    """Counters and latency histograms, in Prometheus text format.

    Thread-safe because the API serves sync endpoints on a threadpool and the
    workers run in threads beside it, so every counter here has concurrent
    writers.
    """

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
        """Prometheus text exposition format.

        Histogram buckets are **cumulative** and must include ``+Inf`` equal to
        the observation count - that is the part of this format people get wrong,
        and a scraper reading non-cumulative buckets reports nonsense rather than
        an error.
        """
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
    """Correlation id, access log, and request metrics.

    The route *template* is used as the metric label, not the path: labelling by
    path would mint a new time series per mission id and turn the metrics
    endpoint into an unbounded memory leak. That is the standard way to blow up
    a Prometheus deployment.
    """

    async def dispatch(self, request: Request, call_next):
        incoming = request.headers.get("x-request-id")
        request_id = incoming or uuid.uuid4().hex
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        logger = logging.getLogger("astrasynth.http")

        # The context variable is reset in one place, after *everything* that
        # logs. It used to be reset in a `finally` around `call_next`, which put
        # the reset before the summary line below - so the one line carrying the
        # route, status and duration was the only line in the request with no
        # correlation id on it.
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
    """The matched route's path template, for use as a metric label.

    ``scope["route"]`` is set by the router, so it is present for anything the
    router handled. It is *absent* for a response produced by an inner middleware
    that short-circuited - an authentication rejection, most obviously - and
    reporting those as ``unmatched`` was actively misleading: it labelled a 401
    on a real endpoint identically to a 404 on a URL that does not exist, and
    lost which endpoint was being called in the one case where an operator most
    wants to know.

    So when the router did not run, the request is matched against the route
    table here. The result is still a template, so label cardinality stays bound
    by the number of routes; the walk only happens on this fallback path.
    """
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
