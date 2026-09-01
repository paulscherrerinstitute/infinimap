"""The event feed: what happened, as opposed to what is different."""

from __future__ import annotations

from datetime import datetime

from infinimap.ibcore.guid import guid_str
from infinimap.ibcore.ids import edge_id

from ..db import Database
from ..models import Event

_EVENTS = """
    SELECT event_id, occurred_at, event_type, snapshot_id,
           node_id, port_id, peer_port_id, detail
    FROM topology_event
    WHERE fabric_id = %(fabric_id)s
      AND occurred_at > %(since)s AND occurred_at <= %(until)s
      {type_filter}
    ORDER BY occurred_at DESC, event_id DESC
    LIMIT %(limit)s
"""

_CHURN = """
    SELECT node_id, port_id, peer_port_id, count(*) AS n
    FROM topology_event
    WHERE fabric_id = %(fabric_id)s
      AND occurred_at > %(since)s AND occurred_at <= %(until)s
    GROUP BY node_id, port_id, peer_port_id
"""


def fetch(db: Database, fabric_id: int, since: datetime, until: datetime,
          limit: int, types: list[str] | None = None) -> tuple[list[Event], bool]:
    """-> (events newest-first, truncated).

    One row over the limit is requested so that `truncated` is a fact rather
    than the guess "we returned exactly the limit, so there may be more".
    """
    params: dict = {"fabric_id": fabric_id, "since": since, "until": until,
                    "limit": limit + 1}
    type_filter = ""
    if types:
        type_filter = "AND event_type = ANY(%(types)s)"
        params["types"] = types

    with db.pool.connection() as conn:
        rows = conn.execute(_EVENTS.format(type_filter=type_filter), params).fetchall()

    truncated = len(rows) > limit
    ports, nodes = _identity_for(db, fabric_id, rows)
    return [_event(r, ports, nodes) for r in rows[:limit]], truncated


def churn(db: Database, fabric_id: int, since: datetime,
          until: datetime) -> tuple[dict[str, int], dict[tuple[int, int], int]]:
    """Event counts per node guid and per (guid, port_number), for the diff.

    A net diff cannot see a link that flapped two hundred times and came back -
    it is byte-identical at both ends of the window. This is the fix.
    """
    params = {"fabric_id": fabric_id, "since": since, "until": until}
    with db.pool.connection() as conn:
        rows = conn.execute(_CHURN, params).fetchall()

    ports, nodes = _identity_for(db, fabric_id, rows)
    by_node: dict[str, int] = {}
    by_port: dict[tuple[int, int], int] = {}
    for r in rows:
        n = r["n"]
        if r["node_id"] is not None and (guid := nodes.get(r["node_id"])) is not None:
            key = guid_str(guid)
            by_node[key] = by_node.get(key, 0) + n
        for col in ("port_id", "peer_port_id"):
            if r[col] is not None and (pk := ports.get(r[col])) is not None:
                by_port[pk] = by_port.get(pk, 0) + n
    return by_node, by_port


# ---- resolution ----------------------------------------------------------

def _identity_for(db: Database, fabric_id: int, rows: list[dict]):
    """Identity maps covering every id in these rows, reloading once on a miss.

    A miss means hardware was added since the cache was populated - node and
    port are append-only by design, so a cached entry can never be wrong, only
    absent.
    """
    ports, nodes = db.identity(fabric_id)
    needed_ports = {r[c] for r in rows for c in ("port_id", "peer_port_id")
                    if r.get(c) is not None}
    needed_nodes = {r["node_id"] for r in rows if r.get("node_id") is not None}
    if needed_ports - ports.keys() or needed_nodes - nodes.keys():
        ports, nodes = db.refresh_identity(fabric_id)
    return ports, nodes


def _event(r: dict, ports: dict, nodes: dict) -> Event:
    a = ports.get(r["port_id"]) if r["port_id"] is not None else None
    b = ports.get(r["peer_port_id"]) if r["peer_port_id"] is not None else None

    node_guid = None
    if r["node_id"] is not None and (g := nodes.get(r["node_id"])) is not None:
        node_guid = guid_str(g)
    elif a is not None:
        node_guid = guid_str(a[0])

    return Event(
        event_id=r["event_id"],
        occurred_at=r["occurred_at"],
        event_type=r["event_type"],
        snapshot_id=r["snapshot_id"],
        node_guid=node_guid,
        port_num=a[1] if a else None,
        peer_guid=guid_str(b[0]) if b else None,
        peer_port_num=b[1] if b else None,
        link_id=edge_id(a, b) if a and b else None,
        detail=r["detail"],
    )
