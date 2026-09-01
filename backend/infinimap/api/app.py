"""The application: pool lifecycle, CORS, error translation, routing."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import Config
from .db import Database
from .routes import counters, detail, diff, events, fabrics, topology

log = logging.getLogger("infinimap.api")

API_PREFIX = "/api/v1"


def create_app(cfg: Config) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        db = Database(cfg)
        db.open()
        app.state.db = db
        log.info("api ready: dsn=%s fabric=%r", _safe_dsn(cfg.dsn), cfg.default_fabric)
        try:
            yield
        finally:
            db.close()

    app = FastAPI(
        title="infinimap read API",
        version="1.0.0",
        summary="Read-only view of the InfiniBand fabric, at any point in time.",
        lifespan=lifespan,
    )

    if cfg.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=list(cfg.cors_origins),
            allow_methods=["GET"],
            allow_headers=["*"],
            # So a browser can read the validator and make conditional requests
            expose_headers=["ETag"],
        )

    for module in (fabrics, topology, detail, events, diff, counters):
        app.include_router(module.router, prefix=API_PREFIX, tags=["fabric"])

    # Check if the database is reachable and the pool is working
    @app.get("/healthz", include_in_schema=False)
    def healthz():
        db: Database = app.state.db
        with db.pool.connection() as conn:
            conn.execute("SELECT 1")
        return {"status": "ok"}

    @app.exception_handler(ValueError)
    async def _bad_value(_request, exc: ValueError):
        """Malformed GUIDs and link ids raise ValueError from ibcore. They are
        client errors, and surfacing them as 500 would blame the server for a
        typo in a URL."""
        return JSONResponse(status_code=422, content={"detail": str(exc)})

    return app


def _safe_dsn(dsn: str) -> str:
    """Never log a password, even at debug."""
    if "@" not in dsn:
        return dsn
    head, _, tail = dsn.rpartition("@")
    scheme, sep, _ = head.partition("://")
    return f"{scheme}{sep}***@{tail}"
