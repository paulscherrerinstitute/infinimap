"""Counter deltas: what accumulated between two instants.

Deliberately mode-agnostic. This takes `since` and `until` and knows nothing
about live, point or compare.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response

from ..db import Database, utc
from ..models import ErrorDeltas, TrafficRates
from ..queries import counters as q
from .deps import fabric_id, get_db, not_modified_tag

router = APIRouter()

DEFAULT_WINDOW = timedelta(hours=1)

# The raw path scans one row per port per sweep.
MAX_RAW_WINDOW = timedelta(hours=24)

# Cheap enough to run before the real query.
_MARK = """
    SELECT max(sweep_id) AS mark
    FROM counter_sweep
    WHERE fabric_id = %(fabric_id)s AND source = 'errors'
      AND started_at > %(since)s AND started_at <= %(until)s
"""


@router.get("/fabrics/{fabric}/counters/errors", response_model=ErrorDeltas,
            response_model_exclude_defaults=True)
def errors(fabric: str, request: Request, response: Response,
           since: datetime | None = None,
           until: datetime | None = None,
           lookback: int = Query(
               int(q.DEFAULT_BASELINE_LOOKBACK.total_seconds()),
               ge=0, le=86_400,
               description="Seconds to look back before `since` for the "
                           "baseline reading. 0 disables the asof baseline.",
           ),
           db: Database = Depends(get_db)):
    """Per-port error deltas over `(since, until]`, sparse.

    A port is present only if a counter moved or a flag is raised; everything
    absent was measured, is zero, and is trusted. `coverage` is what makes that
    safe to read -- check it before believing an empty `ports`.
    """
    fid = fabric_id(db, fabric)
    until = utc(until)
    since = utc(since) if since is not None else until - DEFAULT_WINDOW

    if since >= until:
        raise HTTPException(422, "since must be earlier than until")
    if until - since > MAX_RAW_WINDOW:
        raise HTTPException(
            422,
            f"window of {until - since} exceeds the raw path's {MAX_RAW_WINDOW}; "
            "the hourly rollup that would answer this is not built yet",
        )

    with db.pool.connection() as conn:
        row = conn.execute(_MARK, {"fabric_id": fid, "since": since,
                                   "until": until}).fetchone()
    mark = (row or {}).get("mark")

    # The window is part of the tag because it is part of the resource: the
    # same sweep answers a different question over a different span.
    etag = f'W/"err-{since.timestamp():.6f}-{until.timestamp():.6f}-{mark}-{lookback}"'
    closed = until < datetime.now(timezone.utc)
    if not_modified_tag(request, response, etag, until if closed else None):
        return Response(status_code=304, headers=dict(response.headers))

    return q.error_deltas(db, fid, fabric, since, until,
                          timedelta(seconds=lookback))


# The traffic sweep is a different source on the same table, so the mark is the
# same trick with a different filter.
_TRAFFIC_MARK = """
    SELECT max(sweep_id) AS mark
    FROM counter_sweep
    WHERE fabric_id = %(fabric_id)s AND source = 'traffic'
      AND started_at <= %(until)s
"""


@router.get("/fabrics/{fabric}/counters/traffic", response_model=TrafficRates,
            response_model_exclude_defaults=True)
def traffic(fabric: str, request: Request, response: Response,
            at: datetime | None = None,
            window: float | None = Query(
                None, gt=0, le=3600,
                description="Trailing window in seconds. Defaults to three "
                            "times the fabric's observed traffic cadence.",
            ),
            db: Database = Depends(get_db)):
    """Per-port transmit and receive rates over a trailing window, sparse.

    A rate, not a delta: `at` names the instant to read *at*, and `window` only
    says how long an interval to average over. Ports below the idle floor are
    omitted; ports that could not be measured are present and flagged.
    """
    fid = fabric_id(db, fabric)
    until = utc(at)

    # Sized from the cadence actually observed, not from a constant.
    cadence = q.traffic_cadence(db, fid, until)
    span = (timedelta(seconds=window) if window is not None
            else q.default_window(cadence))

    with db.pool.connection() as conn:
        row = conn.execute(_TRAFFIC_MARK, {"fabric_id": fid,
                                           "until": until}).fetchone()
    mark = (row or {}).get("mark")

    # `at` is in the tag but the wall clock is not: a live poll asks for "now",
    # which is a different instant every time and would defeat the tag outright.
    # The sweep mark is what actually moves the answer, so a poll landing
    # between two sweeps is a 304 that never touches port_traffic.
    pinned = at is not None
    etag = f'W/"traf-{mark}-{span.total_seconds():.3f}-{until.timestamp():.0f}"'         if pinned else f'W/"traf-{mark}-{span.total_seconds():.3f}"'
    if not_modified_tag(request, response, etag, until if pinned else None):
        return Response(status_code=304, headers=dict(response.headers))

    return q.traffic_rates(db, fid, fabric, until, span, cadence)
