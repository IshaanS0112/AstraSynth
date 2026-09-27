"""Optional shared-secret authentication for the whole HTTP surface.

Why a shared key and not OAuth
------------------------------
This is a single-tenant research platform. There are no user accounts, nothing
is scoped per-person, and every caller that can reach the API is entitled to
everything in it - so the only question worth answering at the edge is "is this
caller one of ours". A shared secret answers exactly that question and nothing
more, which is honest about the model rather than dressing it up. The moment
missions belong to *people* this becomes the wrong mechanism, and that is the
point at which to replace it rather than extend it.

Why it defaults to off
----------------------
``API_KEY`` unset means every endpoint is open, which is what the offline demo,
the Docker Compose quick start and CI all rely on. Turning authentication on by
default would mean shipping a default credential, and a default credential is
worse than none: it is a published one.

Why middleware and not a dependency
-----------------------------------
A ``Depends`` on each router is the idiomatic FastAPI answer and it would have
left a hole. ``/static`` is a mounted ``StaticFiles`` app, not a router - it
serves the uploaded terrain tiles and the rendered hazard overlays, which are
mission data, and a dependency cannot be attached to it. Middleware sees every
request including that mount. The security scheme is declared separately on the
OpenAPI document so that ``/docs`` still shows the padlock and lets a reader
authorise, which is the one thing the dependency approach would have given for
free.
"""

from __future__ import annotations

import logging
from secrets import compare_digest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("astrasynth.security")

API_KEY_HEADER = "X-API-Key"

# The minimum length a configured key is allowed to be. A four-character shared
# secret is not weak protection, it is *false* protection: it makes a deployment
# feel closed while being trivially guessable, and someone would reasonably stop
# worrying about the network in front of it. Refusing to start is the safer
# failure.
MIN_KEY_LENGTH = 16

# Probes and the API's own description stay open even with a key set.
#
# /health and /ready are read by a container runtime, which generally cannot be
# given a credential and whose failure to reach them gets the process killed -
# authentication on a liveness probe is a way to cause the outage it is meant to
# survive. /docs and /openapi.json describe the shape of the API without
# containing any mission data, and closing them breaks the docs page (the Swagger
# UI cannot send a header to fetch its own schema). Neither exposes anything a
# reader of this repository does not already have.
OPEN_PATHS = frozenset(
    {
        "/health",
        "/ready",
        "/docs",
        "/docs/oauth2-redirect",
        "/redoc",
        "/openapi.json",
    }
)


class MisconfiguredKeyError(RuntimeError):
    """Raised at startup for a key too short to be worth having."""


def validate_api_key(api_key: str) -> str:
    """Check a configured key at import time, not at first request."""
    if api_key and len(api_key) < MIN_KEY_LENGTH:
        raise MisconfiguredKeyError(
            f"API_KEY is {len(api_key)} characters; at least {MIN_KEY_LENGTH} are required. "
            "Generate one with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
        )
    return api_key


def presented_key(request: Request) -> str | None:
    """The credential this request carries, from either accepted form.

    ``X-API-Key`` is the documented header. ``Authorization: Bearer`` is accepted
    too because every HTTP client already knows how to send it, and refusing it
    would mean a caller has to special-case this API for no benefit.
    """
    direct = request.headers.get(API_KEY_HEADER)
    if direct:
        return direct
    authorization = request.headers.get("Authorization", "")
    scheme, _, credential = authorization.partition(" ")
    if scheme.lower() == "bearer" and credential:
        return credential.strip()
    return None


class ApiKeyMiddleware(BaseHTTPMiddleware):
    """Reject unauthenticated requests when a key is configured."""

    def __init__(self, app, api_key: str) -> None:
        super().__init__(app)
        self.api_key = api_key

    async def dispatch(self, request: Request, call_next):
        if not self.api_key:
            return await call_next(request)

        # A browser preflight cannot carry the key: the whole purpose of the
        # preflight is to ask whether the header may be sent. Rejecting it would
        # make the API unreachable from the dashboard while looking like a CORS
        # bug. The response to OPTIONS carries no mission data.
        if request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
            return await call_next(request)

        candidate = presented_key(request)
        # compare_digest rather than ==, so the comparison does not leak the
        # length of the shared prefix through its timing. The cost is nothing and
        # the alternative is a defect nobody notices in review.
        if candidate is None or not compare_digest(candidate, self.api_key):
            logger.warning(
                "rejected unauthenticated request",
                extra={"astra_route": request.url.path, "astra_method": request.method},
            )
            return JSONResponse(
                status_code=401,
                content={
                    "detail": (
                        f"Missing or invalid credential. Send the shared secret as the "
                        f"{API_KEY_HEADER} header, or as an Authorization: Bearer token."
                    )
                },
            )
        return await call_next(request)


def declare_security_scheme(app: FastAPI, api_key: str) -> None:
    """Advertise the scheme on the OpenAPI document, so /docs offers Authorize.

    Done by wrapping ``app.openapi`` rather than by declaring dependencies,
    because enforcement lives in middleware - see the module docstring. The
    scheme is only declared when a key is actually configured: a padlock on an
    open API would be a documented lie.
    """
    if not api_key:
        return

    def openapi_with_security() -> dict:
        if app.openapi_schema:
            return app.openapi_schema
        schema = _base_openapi(app)
        schema.setdefault("components", {}).setdefault("securitySchemes", {})["ApiKeyHeader"] = {
            "type": "apiKey",
            "in": "header",
            "name": API_KEY_HEADER,
        }
        schema["security"] = [{"ApiKeyHeader": []}]
        app.openapi_schema = schema
        return schema

    app.openapi = openapi_with_security  # type: ignore[method-assign]


def _base_openapi(app: FastAPI) -> dict:
    from fastapi.openapi.utils import get_openapi

    return get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
