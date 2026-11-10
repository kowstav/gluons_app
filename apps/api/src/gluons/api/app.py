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
    configure_tracing(app)
    return app


app = create_app()
