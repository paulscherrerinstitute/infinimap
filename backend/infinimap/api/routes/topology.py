"""The lean graph - what the map draws."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response

from ..db import Database
from ..models import Topology
from ..queries import topology as q
from .deps import fabric_id, get_db, not_modified, resolve

router = APIRouter()


@router.get("/fabrics/{fabric}/topology", response_model=Topology)
def topology(fabric: str, request: Request, response: Response,
             at: datetime | None = None, db: Database = Depends(get_db)):
    """The graph as of `at`, or now."""
    fid = fabric_id(db, fabric)
    resolved = resolve(db, fid, at)
    if not_modified(request, response, resolved, at):
        return Response(status_code=304, headers=dict(response.headers))

    nodes, links, groups, counts = q.fetch(db, fid, at)
    return Topology(resolved=resolved, counts=counts, nodes=nodes,
                    links=links, system_groups=groups)
