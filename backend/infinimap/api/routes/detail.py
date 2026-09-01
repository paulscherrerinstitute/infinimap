"""Per-selection detail. One request per selected element, complete."""

from __future__ import annotations

from datetime import datetime, timedelta

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..db import Database
from ..models import LinkDetail, NodeDetail
from ..queries import detail as q
from .deps import fabric_id, get_db, not_modified_tag, resolve

router = APIRouter()

# The counter window is opt-in, and absent by default. A card that nobody asked
# counters for should not pay for the join.
_COUNTER_WINDOW = Query(
    None, gt=0, le=86_400,
    description="Seconds of counter history to summarise on each port. "
                "Omit for no counters. Pass the same window the overlay is "
                "using, or the card and the graph will disagree.",
)


# A flag rather than a window, unlike the counters above.
_WITH_TRAFFIC = Query(
    False,
    description="Include per-port throughput. The window is server-sized from "
                "the fabric's observed traffic cadence, matching the overlay.",
)


def _detail_tag(resolved, window: float | None, traffic: bool) -> str:
    """The snapshot validator, widened to cover what rides along with it.

    The snapshot alone is not enough once counters are in the response: the
    window is a request parameter, so two windows over one snapshot are two
    different answers. Errors need nothing more -- the collector writes topology
    and errors in the same cycle, so a new error sweep is a new snapshot_id.

    Traffic breaks that: it runs on its own thread at its own cadence, so a new 
    traffic sweep does NOT move `snapshot_id`, and a card would keep revalidating 
    into a 304 while the rate it shows goes stale. When traffic is requested the 
    tag therefore carries the traffic sweep mark rather than a bare flag.
    """
    return (f'W/"{resolved.snapshot_id}-{resolved.topology_hash[:16]}'
            f'-{window or 0:g}-{traffic}"')



_TRAFFIC_SWEEP_MARK = """
    SELECT max(sweep_id) AS mark
    FROM counter_sweep
    WHERE fabric_id = %(fabric_id)s AND source = 'traffic'
      AND started_at <= %(until)s
"""


def _traffic_mark(db: Database, fid: int, at):
    """The newest traffic sweep, for the validator. Cheap: one row per sweep."""
    from datetime import datetime, timezone
    until = at or datetime.now(timezone.utc)
    with db.pool.connection() as conn:
        row = conn.execute(_TRAFFIC_SWEEP_MARK,
                           {"fabric_id": fid, "until": until}).fetchone()
    return (row or {}).get("mark")


@router.get("/fabrics/{fabric}/nodes/{guid}", response_model=NodeDetail)
def node(fabric: str, guid: str, request: Request, response: Response,
         at: datetime | None = None,
         counters_window: float | None = _COUNTER_WINDOW,
         with_traffic: bool = _WITH_TRAFFIC,
         db: Database = Depends(get_db)):
    fid = fabric_id(db, fabric)
    resolved = resolve(db, fid, at)
    mark = _traffic_mark(db, fid, at) if with_traffic else False
    if not_modified_tag(request, response,
                        _detail_tag(resolved, counters_window, mark), at):
        return Response(status_code=304, headers=dict(response.headers))

    window = timedelta(seconds=counters_window) if counters_window else None
    try:
        found = q.node(db, fid, guid, at, resolved, window, with_traffic)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if found is None:
        raise HTTPException(404, f"no node {guid} in {fabric!r} at that time")
    return found


@router.get("/fabrics/{fabric}/links/{link_id}", response_model=LinkDetail)
def link(fabric: str, link_id: str, request: Request, response: Response,
         at: datetime | None = None,
         counters_window: float | None = _COUNTER_WINDOW,
         with_traffic: bool = _WITH_TRAFFIC,
         db: Database = Depends(get_db)):
    """Both ends expanded in place, so selecting a link is one request.

    The id is the canonical GUID-ordered form; the database's own port_id
    ordering never appears on the wire, so this parses without consulting it.
    """
    fid = fabric_id(db, fabric)
    resolved = resolve(db, fid, at)
    mark = _traffic_mark(db, fid, at) if with_traffic else False
    if not_modified_tag(request, response,
                        _detail_tag(resolved, counters_window, mark), at):
        return Response(status_code=304, headers=dict(response.headers))

    window = timedelta(seconds=counters_window) if counters_window else None
    try:
        found = q.link(db, fid, link_id, at, resolved, window, with_traffic)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    if found is None:
        raise HTTPException(404, f"no link {link_id} in {fabric!r} at that time")
    return found
