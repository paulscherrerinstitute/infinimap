"""Shared route dependencies: the database handle, fabric resolution, caching."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import HTTPException, Request, Response

from ..db import Database, FabricNotFound, NoSnapshot
from ..models import Resolved


def get_db(request: Request) -> Database:
    return request.app.state.db


def fabric_id(db: Database, name: str) -> int:
    try:
        return db.fabric_id(name)
    except FabricNotFound:
        raise HTTPException(404, f"no fabric named {name!r}") from None


def resolve(db: Database, fid: int, at: datetime | None, *,
            floor: bool = False) -> Resolved:
    """`floor=True` for the start of a range; see Database.resolve."""
    try:
        return Resolved.model_validate(db.resolve(fid, at, floor=floor))
    except NoSnapshot as exc:
        raise HTTPException(404, str(exc)) from None


def not_modified(request: Request, response: Response,
                 resolved: Resolved, at: datetime | None) -> bool:
    """Attach validators; True when the client's cached copy is still current.

    Weak validator because the bytes are not guaranteed identical across
    releases - a decode table can change a label without the fabric moving -
    while the semantics the tag stands for are exactly what snapshot_id and the
    topology hash pin down.
    """
    etag = f'W/"{resolved.snapshot_id}-{resolved.topology_hash[:16]}"'
    return _validate(request, response, etag, at)


def not_modified_range(request: Request, response: Response,
                       since: Resolved, until: Resolved,
                       until_requested: datetime | None) -> bool:
    """As `not_modified`, for a response spanning two sweeps rather than one."""
    etag = (f'W/"{since.snapshot_id}-{until.snapshot_id}'
            f'-{until.topology_hash[:16]}"')
    return _validate(request, response, etag, until_requested)


def not_modified_tag(request: Request, response: Response, etag: str,
                     at: datetime | None) -> bool:
    """As `not_modified`, for a resource that is not pinned to a snapshot.

    Counters answer from `counter_sweep`, not `topology_snapshot`, so they build
    their own validator and only need the freshness policy applied to it. `at`
    is the instant the answer is closed at, or None while it is still moving.
    """
    return _validate(request, response, etag, at)


def _validate(request: Request, response: Response, etag: str,
              at: datetime | None) -> bool:
    """Attach the validator and freshness policy; True on a cache hit."""
    response.headers["ETag"] = etag

    historical = at is not None and at < datetime.now(timezone.utc)
    response.headers["Cache-Control"] = (
        "public, max-age=31536000, immutable" if historical else "no-cache"
    )
    return request.headers.get("if-none-match") == etag
