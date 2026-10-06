"""Optional shared-secret authentication for the whole HTTP surface."""

from __future__ import annotations

import logging
from secrets import compare_digest

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("astrasynth.security")

API_KEY_HEADER = "X-API-Key"

# A guessable key is false protection, not weak protection: it makes a
# deployment feel closed. Refusing to start is the safer failure.
MIN_KEY_LENGTH = 16

# Open even with a key set. The probes are read by a container runtime that
# cannot be given a credential, and failing them gets the process killed. /docs
# describes the API's shape, not its data, and Swagger UI cannot send a header to
# fetch its own schema.
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
    """The credential this request carries, from either accepted form."""
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

        # A preflight cannot carry the key - it exists to ask whether the header
        # may be sent - and rejecting it looks like a CORS bug.
        if request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
            return await call_next(request)

        candidate = presented_key(request)
        # compare_digest, not ==: do not leak the shared prefix length by timing.
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
    """Advertise the scheme on the OpenAPI document, so /docs offers Authorize."""
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
