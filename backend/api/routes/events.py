"""The event timeline: what happened in a window."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query

from ..db import Database
from ..models import Events
from ..queries import events as q
from .deps import fabric_id, get_db

router = APIRouter()


@router.get("/fabrics/{fabric}/events", response_model=Events)
def events(fabric: str,
           since: datetime | None = None,
           until: datetime | None = None,
           type: list[str] | None = Query(None),
           limit: int = Query(500, ge=1, le=5000),
           db: Database = Depends(get_db)):
    """Newest first. Defaults to the last 24 hours.

    Complements the diff rather than duplicating it: a diff says a link is
    unchanged, this says it went down and came back forty times while nobody
    was looking.
    """
    fid = fabric_id(db, fabric)
    until = until or datetime.now(timezone.utc)
    since = since or (until - timedelta(days=1))

    rows, truncated = q.fetch(db, fid, since, until, limit, type)
    return Events(fabric=fabric, since=since, until=until,
                  truncated=truncated, events=rows)
