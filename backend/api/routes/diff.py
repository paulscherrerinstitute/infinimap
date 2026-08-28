"""Topology and state differences between two instants."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from ..db import Database
from ..models import Diff
from ..queries import diff as q
from .deps import fabric_id, get_db, not_modified_range, resolve

router = APIRouter()


@router.get("/fabrics/{fabric}/diff", response_model=Diff)
def diff(fabric: str, request: Request, response: Response,
         since: datetime | None = None,
         until: datetime | None = None,
         db: Database = Depends(get_db)):
    """The union of both topologies, each element tagged with how it changed."""
    fid = fabric_id(db, fabric)
    until_t = until or datetime.now(timezone.utc)
    since_t = since or (until_t - timedelta(days=7))
    if since_t >= until_t:
        raise HTTPException(422, "`since` must be earlier than `until`")

    a = resolve(db, fid, since_t, floor=True)
    b = resolve(db, fid, until_t)
    
    if not_modified_range(request, response, a, b, until):
        return Response(status_code=304, headers=dict(response.headers))

    return q.fetch(db, fid, fabric, a, b)
