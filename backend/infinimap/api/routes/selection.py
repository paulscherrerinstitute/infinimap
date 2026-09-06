"""Aggregate one selection of nodes and links.

The only POST in the API, and the only route with no ETag. Both follow from
what the resource is: a selection is up to thousands of ids, which does not fit
in a query string, and it is asked once per selection gesture with a different
body every time, so there is nothing a validator could match against.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from ..db import Database
from ..models import SelectionRequest, SelectionSummary
from ..queries import selection as q
from .deps import fabric_id, get_db, resolve

router = APIRouter()


@router.post("/fabrics/{fabric}/selection", response_model=SelectionSummary)
def selection(fabric: str, req: SelectionRequest,
              db: Database = Depends(get_db)):
    """What a set of selected elements adds up to, at one instant."""
    total = len(req.nodes) + len(req.links)
    if total == 0:
        raise HTTPException(422, "selection is empty")
    if total > q.MAX_SELECTION:
        raise HTTPException(
            422,
            f"selection of {total} exceeds the {q.MAX_SELECTION} id limit; "
            "a selection that large is a fabric-wide question -- use "
            "/topology and /counters directly",
        )

    fid = fabric_id(db, fabric)
    resolved = resolve(db, fid, req.at)
    try:
        return q.summarize(db, fid, req, resolved)
    except ValueError as exc:
        # A malformed GUID or link id in the body. The app-level handler turns
        # these into 422s too, but naming the parameter is worth the catch.
        raise HTTPException(422, f"unusable id in selection: {exc}") from None
