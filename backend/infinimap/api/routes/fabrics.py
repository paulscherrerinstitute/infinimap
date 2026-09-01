"""Fabric list, the poll target, and the snapshot index."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request, Response

from ..db import Database
from ..models import FabricRow, Head, Snapshots, SnapshotRow
from ..queries import topology as topology_q
from .deps import fabric_id, get_db, not_modified, resolve

router = APIRouter()

_SNAPSHOTS = """
    SELECT snapshot_id, collected_at, source, origin, changed, complete,
           node_count, port_count, link_count, duration_ms
    FROM topology_snapshot
    WHERE fabric_id = %(fabric_id)s
      AND collected_at > %(since)s AND collected_at <= %(until)s
    ORDER BY collected_at DESC, snapshot_id DESC
    LIMIT %(limit)s
"""


@router.get("/fabrics", response_model=list[FabricRow])
def list_fabrics(db: Database = Depends(get_db)):
    return db.fabrics()


@router.get("/fabrics/{fabric}/head", response_model=Head)
def head(fabric: str, request: Request, response: Response,
         at: datetime | None = None, db: Database = Depends(get_db)):
    """Cheap enough to poll. Refetch topology only when topology_hash moves."""
    fid = fabric_id(db, fabric)
    resolved = resolve(db, fid, at)
    if not_modified(request, response, resolved, at):
        return Response(status_code=304, headers=dict(response.headers))

    _, _, _, counts = topology_q.fetch(db, fid, at)
    return Head(fabric=fabric, resolved=resolved, counts=counts)


@router.get("/fabrics/{fabric}/snapshots", response_model=Snapshots)
def snapshots(fabric: str,
              since: datetime | None = None,
              until: datetime | None = None,
              limit: int = Query(500, ge=1, le=5000),
              db: Database = Depends(get_db)):
    """The collection-run index, newest first.

    Every run writes a row whether or not anything changed, so this is what
    makes silence readable: a gap here is the collector being down, while a run
    of `changed = false` is a fabric that genuinely did not move. Those two must
    never render the same way, and only this endpoint can tell them apart.
    """
    fid = fabric_id(db, fabric)
    until = until or datetime.now(timezone.utc)
    since = since or (until - timedelta(days=7))

    with db.pool.connection() as conn:
        rows = conn.execute(
            _SNAPSHOTS,
            {"fabric_id": fid, "since": since, "until": until, "limit": limit},
        ).fetchall()

    return Snapshots(fabric=fabric, snapshots=[SnapshotRow(**r) for r in rows])
