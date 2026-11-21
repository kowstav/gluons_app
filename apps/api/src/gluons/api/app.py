"""HTTP application. Route modules in `gluons.api.routes` are discovered automatically."""
from __future__ import annotations

import importlib
import logging
import pkgutil
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text

from gluons import __version__
from gluons.api import routes
from gluons.config import get_settings
from gluons.core import GluonsError
from gluons.db.session import public_tx
from gluons.observability import configure_logging, configure_tracing, request_scope
from gluons.skills.registry import get_registry

logger = logging.getLogger("gluons.api")

# Ceiling for one request body. Webhook and document routes apply their own smaller limits;
# this bounds everything else, including routes that forget to.
MAX_REQUEST_BYTES = 2 * 1024 * 1024


class BodyLimit:
    """Rejects oversized request bodies before a handler buffers them.

    Counts bytes as they arrive, so a chunked upload without Content-Length is bounded too.
    """

    def __init__(self, app, limit: int = MAX_REQUEST_BYTES) -> None:  # type: ignore[no-untyped-def]
        self.app, self.limit = app, limit

    async def __call__(self, scope, receive, send) -> None:  # type: ignore[no-untyped-def]
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        declared = dict(scope["headers"]).get(b"content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.limit:
            await self._reject(send)
            return
        seen, exceeded, answered = 0, False, False

        async def counted_receive():  # type: ignore[no-untyped-def]
            nonlocal seen, exceeded
            if exceeded:
                return {"type": "http.disconnect"}
            message = await receive()
            if message["type"] == "http.request":
                seen += len(message.get("body", b""))
                if seen > self.limit:
                    # Stop feeding the handler. Whatever it answers is replaced below.
                    exceeded = True
                    return {"type": "http.disconnect"}
            return message

        async def guarded_send(message) -> None:  # type: ignore[no-untyped-def]
            nonlocal answered
            if not exceeded:
                await send(message)
            elif not answered:
                answered = True
                await self._reject(send)

        try:
            await self.app(scope, counted_receive, guarded_send)
        finally:
            if exceeded and not answered:
                await self._reject(send)

    @staticmethod
    async def _reject(send) -> None:  # type: ignore[no-untyped-def]
        body = b'{"error":{"code":"PAYLOAD_TOO_LARGE","message":"That request is too large.","details":{}}}'
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging()
    from gluons.mcp.server import create_mcp_app, create_server
    from gluons.oauth.routes import router as oauth_router

    mcp_server = create_server()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with mcp_server.session_manager.run():
            yield

    app = FastAPI(
        title="Gluons API",
        version=__version__,
        docs_url=None if settings.env == "production" else "/docs",
        redoc_url=None,
        lifespan=lifespan,
    )
    # Registered first so it sits inside CORS: an unexpected failure still reaches the web app
    # as a readable JSON error instead of an opaque cross-origin failure.
    @app.middleware("http")
    async def unexpected_errors(request: Request, call_next):  # type: ignore[no-untyped-def]
        try:
            return await call_next(request)
        except Exception:
            # The traceback stays in the server log under the request id. The client gets the
            # usual error envelope and nothing about the failure itself.
            logger.exception("request failed", extra={"method": request.method, "path": request.url.path})
            return JSONResponse(
                {"error": {"code": "INTERNAL_ERROR", "message": "Something went wrong on our side. Try again shortly.", "details": {}}},
                status_code=500,
            )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.web_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type", "X-CSRF-Token", "X-Expected-Organization", "Idempotency-Key", "Authorization", "MCP-Protocol-Version", "Mcp-Session-Id", "Mcp-Client-Info"],
        expose_headers=["Mcp-Session-Id"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):  # type: ignore[no-untyped-def]
        started = time.monotonic()
        with request_scope(request.headers) as request_id:
            try:
                response = await call_next(request)
            except Exception:
                logger.exception("request failed", extra={"method": request.method, "path": request.url.path})
                raise
            response.headers["X-Request-Id"] = request_id
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            # API responses carry tenant data and tokens: never cached, framed or embedded elsewhere.
            response.headers.setdefault("Cache-Control", "no-store")
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Cross-Origin-Resource-Policy"] = "same-site"
            if not request.url.path.startswith("/docs"):
                response.headers["Content-Security-Policy"] = "default-src 'none'; frame-ancestors 'none'"
            if settings.hosted:
                response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
            logger.info(
                "request",
                extra={
                    "method": request.method,
                    "path": request.url.path,
                    "status": response.status_code,
                    "ms": int((time.monotonic() - started) * 1000),
                },
            )
            return response

    @app.exception_handler(GluonsError)
    async def on_gluons_error(request: Request, exc: GluonsError) -> JSONResponse:
        return JSONResponse({"error": exc.to_dict()}, status_code=exc.status)

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        fields = [{"field": ".".join(str(p) for p in e["loc"][1:]), "message": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            {"error": {"code": "VALIDATION_FAILED", "message": "Check the highlighted fields.", "details": {"fields": fields}}},
            status_code=422,
        )

    @app.get("/health", tags=["ops"])
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready", tags=["ops"])
    def ready() -> JSONResponse:
        checks: dict[str, str] = {}
        try:
            with public_tx() as session:
                session.execute(text("select 1"))
            checks["database"] = "ok"
        except Exception:  # noqa: BLE001 - readiness reports, never raises
            checks["database"] = "unavailable"
        try:
            checks["skills"] = str(len(get_registry()))
        except Exception:  # noqa: BLE001 - a bad contract must show up here
            checks["skills"] = "invalid"
        healthy = checks["database"] == "ok" and checks["skills"] != "invalid"
        return JSONResponse({"status": "ok" if healthy else "degraded", "checks": checks}, status_code=200 if healthy else 503)

    app.include_router(oauth_router)
    for module in pkgutil.iter_modules(routes.__path__):
        loaded = importlib.import_module(f"{routes.__name__}.{module.name}")
        router = getattr(loaded, "router", None)
        if router is not None:
            app.include_router(router)
    app.mount("/", create_mcp_app(mcp_server))
    app.add_middleware(BodyLimit)
    configure_tracing(app)
    return app


app = create_app()
