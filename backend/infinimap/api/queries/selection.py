"""What a box-select is asking.

The governing rule: **answer only what the browser cannot.**

  * **ports** -- how many, how many active, how many actually cabled.
  * **firmware, vendor, model**.
  * **counters and traffic**, summed over the selection's ports.
  * a degraded link's **reason**, which needs both ends' PortViews.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timedelta

from infinimap.ibcore.guid import guid_str, parse_guid, unsigned
from infinimap.ibcore.health import PortView, reasons
from infinimap.ibcore.ids import parse_edge_id

from ..db import Database, as_of, sql
from ..models import (
    CounterWindow, LinkElement, LinkNote, NodeElement, PortNote, Resolved,
    SelectionCounters, SelectionCounts, SelectionCoverage, SelectionLinks,
    SelectionNodes, SelectionRequest, SelectionSummary, SelectionTraffic,
    Tallied,
)
from . import counters as counters_q
from . import detail as detail_q
from . import topology as topology_q

#: Ids in one request.
MAX_SELECTION = 5000

#: Named individually in the response. The counts are always exact; only the
#: lists of examples are capped.
MAX_MISSING_REPORTED = 20
MAX_DEGRADED = 12
MAX_TOP_PORTS = 12

#: Worst-wins ordering, matching ibcore.health's own.
_SEVERITY = {"down": 0, "degraded": 1, "unknown": 2, "ok": 3}


# Everything about a selected node that the lean graph does not carry.
_NODE_EXTRA = """
    SELECT n.node_guid, n.num_ports, n.vendor_id, n.device_id,
           ns.fw_version, ns.node_desc
    FROM node n
    JOIN node_state ns ON ns.node_id = n.node_id AND {ns_state}
    WHERE n.fabric_id = %(fabric_id)s AND n.node_guid = ANY(%(guids)s)
"""

_PORTS_FOR_NODES = """
    SELECT p.port_id, p.port_number, n.node_guid,
           ps.log_state,
           -- link_state has no surrogate key -- its primary key is
           -- (port_a_id, port_b_id, valid_from) -- so "is this port cabled"
           -- is asked of a column that is NOT NULL when a row matched.
           (ls.port_a_id IS NOT NULL) AS linked
    FROM port p
    JOIN node n ON n.node_id = p.node_id
    LEFT JOIN port_state ps ON ps.port_id = p.port_id AND {ps_state}
    LEFT JOIN link_state ls
           ON (ls.port_a_id = p.port_id OR ls.port_b_id = p.port_id)
          AND {ls_state}
    WHERE p.fabric_id = %(fabric_id)s AND n.node_guid = ANY(%(guids)s)
"""

_PORTS_FOR_ENDS = """
    SELECT p.port_id, p.port_number, n.node_guid,
           ps.log_state, ps.active_speed, ps.active_width,
           ps.enabled_speed, ps.enabled_width,
           ps.supported_speed, ps.supported_width
    FROM port p
    JOIN node n ON n.node_id = p.node_id
    LEFT JOIN port_state ps ON ps.port_id = p.port_id AND {ps_state}
    WHERE p.fabric_id = %(fabric_id)s
      AND n.node_guid = ANY(%(guids)s)
      AND p.port_number = ANY(%(ports)s)
"""


def summarize(db: Database, fabric_id: int, req: SelectionRequest,
              resolved: Resolved) -> SelectionSummary:
    """Aggregate one selection at one instant."""
    at = req.at
    # Deduplicated: a client is free to send an id twice.
    want_nodes = list(dict.fromkeys(req.nodes))
    want_links = list(dict.fromkeys(req.links))

    nodes, links, _groups, _counts = topology_q.fetch(db, fabric_id, at)
    by_guid = {n.id: n for n in nodes}
    by_link = {l.id: l for l in links}

    found_nodes = [by_guid[g] for g in want_nodes if g in by_guid]
    found_links = [by_link[i] for i in want_links if i in by_link]

    missing = [i for i in want_nodes if i not in by_guid]
    missing += [i for i in want_links if i not in by_link]

    node_block, node_port_ids = _nodes(db, fabric_id, at, found_nodes)
    link_block, end_port_ids = _links(db, fabric_id, at, found_links)

    # A port reachable both ways -- owned by a selected node AND an end of a
    # selected link -- must be counted once, or its errors are doubled.
    port_ids = list(dict.fromkeys([*node_port_ids, *end_port_ids]))

    # `until` is the RESOLVED instant, never the wall clock.
    until = at or resolved.collected_at

    return SelectionSummary(
        resolved=resolved,
        requested=SelectionCounts(
            nodes_requested=len(want_nodes),
            links_requested=len(want_links),
            nodes_found=len(found_nodes),
            links_found=len(found_links),
            missing=missing[:MAX_MISSING_REPORTED],
        ),
        nodes=node_block,
        links=link_block,
        counters=(_counters(db, fabric_id, port_ids, until,
                            req.counters_window, by_guid)
                  if req.counters_window else None),
        traffic=(_traffic(db, fabric_id, port_ids, until, by_guid,
                          found_links, link_block.capacity_gbps)
                 if req.with_traffic else None),
    )


# ---- nodes ----------------------------------------------------------------

def _nodes(db: Database, fabric_id: int, at: datetime | None,
           found: list[NodeElement]) -> tuple[SelectionNodes, list[int]]:
    """The node block, plus every port id the selected nodes own."""
    empty = SelectionNodes(total=0, ports_total=0, ports_active=0,
                           ports_inactive=0, ports_linked=0)
    if not found:
        return empty, []

    guids = [parse_guid(n.id) for n in found]
    params = {"fabric_id": fabric_id, "guids": guids, "at": at}

    with db.pool.connection() as conn:
        extra = conn.execute(sql(_NODE_EXTRA, ns_state=as_of("ns", at)),
                             params).fetchall()
        ports = conn.execute(
            sql(_PORTS_FOR_NODES, ps_state=as_of("ps", at),
                ls_state=as_of("ls", at)),
            params,
        ).fetchall()

    firmware: Counter[str] = Counter()
    vendors: Counter[str] = Counter()
    models: Counter[str] = Counter()
    for r in extra:
        firmware[r["fw_version"] or "unknown"] += 1
        vendor, model = detail_q.device_names(r["vendor_id"], r["device_id"])
        vendors[vendor or "unknown"] += 1
        models[model or "unknown"] += 1

    active = sum(1 for r in ports if r["log_state"] == "active")
    inactive = sum(1 for r in ports
                   if r["log_state"] is not None and r["log_state"] != "active")

    return SelectionNodes(
        total=len(found),
        ports_total=len(ports),
        ports_active=active,
        ports_inactive=inactive,
        ports_linked=sum(1 for r in ports if r["linked"]),
        health=_tally(n.health for n in found),
        types=_tally(n.type for n in found),
        firmware=_tallied(firmware),
        vendors=_tallied(vendors),
        models=_tallied(models),
    ), [r["port_id"] for r in ports]


# ---- links ----------------------------------------------------------------

def _links(db: Database, fabric_id: int, at: datetime | None,
           found: list[LinkElement]) -> tuple[SelectionLinks, list[int]]:
    """The link block, plus the port ids of both ends of every selected link."""
    if not found:
        return SelectionLinks(total=0), []

    # Every endpoint the ids name.
    ends: list[tuple[int, int]] = []
    for l in found:
        try:
            a, b = parse_edge_id(l.id)
        except ValueError:  # a link element topology.fetch produced must parse
            continue
        ends += [a, b]

    params = {
        "fabric_id": fabric_id, "at": at,
        "guids": sorted({g for g, _ in ends}),
        "ports": sorted({p for _, p in ends}),
    }
    with db.pool.connection() as conn:
        rows = conn.execute(sql(_PORTS_FOR_ENDS, ps_state=as_of("ps", at)),
                            params).fetchall()

    by_end = {(unsigned(r["node_guid"]), r["port_number"]): r for r in rows}
    wanted = {(unsigned(g), p) for g, p in ends}

    capacity = 0.0
    unrated = 0
    degraded: list[LinkNote] = []
    for l in found:
        if l.rate_gbps:
            capacity += l.rate_gbps
        else:
            unrated += 1
        if l.health in ("down", "degraded"):
            degraded.append(LinkNote(
                id=l.id, label=_link_label(db, l), health=l.health,
                reason=_reason_for(l, by_end),
            ))

    degraded.sort(key=lambda n: (_SEVERITY.get(n.health, 2), n.label))

    port_ids = [r["port_id"] for r in rows
                if (unsigned(r["node_guid"]), r["port_number"]) in wanted]

    return SelectionLinks(
        total=len(found),
        health=_tally(l.health for l in found),
        speeds=_tally(l.speed.label or "unknown" for l in found),
        widths=_tally(l.width.label or "unknown" for l in found),
        capacity_gbps=round(capacity, 2),
        unrated=unrated,
        degraded=degraded[:MAX_DEGRADED],
    ), port_ids


def _reason_for(link: LinkElement, by_end: dict) -> list[str]:
    """Why this link is not healthy, from both ends' state."""
    a = by_end.get((unsigned(parse_guid(link.source)), link.source_port))
    b = by_end.get((unsigned(parse_guid(link.target)), link.target_port))
    return reasons(_view(a), _view(b))


def _view(r: dict | None) -> PortView | None:
    if r is None:
        return None
    if r["log_state"] is None and r["active_speed"] is None:
        return None
    return PortView(
        log_state=r["log_state"],
        active_speed=r["active_speed"], active_width=r["active_width"],
        enabled_speed=r["enabled_speed"], enabled_width=r["enabled_width"],
        supported_speed=r["supported_speed"],
        supported_width=r["supported_width"],
    )


def _link_label(db: Database, link: LinkElement) -> str:
    """`nodeA:port <-> nodeB:port`, honouring display-name overrides."""
    a = db.node_name(link.source, None) or link.source
    b = db.node_name(link.target, None) or link.target
    return f"{a}:{link.source_port} ↔ {b}:{link.target_port}"


# ---- counters -------------------------------------------------------------

def _counters(db: Database, fabric_id: int, port_ids: list[int],
              until: datetime, window_s: float,
              by_guid: dict[str, NodeElement]) -> SelectionCounters:
    """Error deltas summed over the selection's ports.

    Goes through `counters.port_counters` -- the same function the detail cards
    use -- so a port's number here is the number its own card would show.
    """
    window = timedelta(seconds=window_s)
    since = until - window
    got = counters_q.port_counters(db, port_ids, since, until)
    ident = counters_q.identity_for(db, fabric_id, port_ids)

    totals: Counter[str] = Counter()
    notes: list[PortNote] = []
    measured = unusable = 0
    first_at: datetime | None = None
    last_at: datetime | None = None

    for port_id in port_ids:
        one = got.get(port_id)
        if one is None:
            continue  # counted as no_data below
        if one.delta is None:
            # A reading, but no delta: no baseline, or a counter went
            # backwards. Not zero, and not unpolled either.
            unusable += 1
            continue

        measured += 1
        last_at = one.at if last_at is None else max(last_at, one.at)
        if one.span_s is not None:
            started = one.at - timedelta(seconds=one.span_s)
            first_at = started if first_at is None else min(first_at, started)

        for name, value in one.delta.items():
            totals[name] += value

        total = sum(one.delta.values())
        if total <= 0:
            continue
        guid, port_num = ident.get(port_id, (None, None))
        if guid is None:
            continue
        guid_hex = guid_str(guid)
        notes.append(PortNote(
            node=guid_hex, label=_label_of(by_guid, guid_hex), port=port_num,
            value=float(total),
            detail={k: float(v) for k, v in one.delta.items()},
        ))

    notes.sort(key=lambda n: (-n.value, n.label, n.port))

    return SelectionCounters(
        window=CounterWindow(
            requested_since=since, requested_until=until,
            first_at=first_at, last_at=last_at,
            span_s=((last_at - first_at).total_seconds()
                    if first_at and last_at else None),
            resolution="raw",
        ),
        coverage=SelectionCoverage(
            ports_expected=len(port_ids),
            ports_measured=measured,
            ports_no_data=len(port_ids) - measured - unusable,
            ports_unusable=unusable,
        ),
        totals=dict(totals),  # type: ignore[arg-type]
        top_ports=notes[:MAX_TOP_PORTS],
    )


# ---- traffic --------------------------------------------------------------

def _traffic(db: Database, fabric_id: int, port_ids: list[int],
             until: datetime, by_guid: dict[str, NodeElement],
             links: list[LinkElement],
             capacity_gbps: float) -> SelectionTraffic:
    """Throughput summed over the selection's ports."""
    cadence = counters_q.traffic_cadence(db, fabric_id, until)
    window = counters_q.default_window(cadence)
    got = counters_q.port_traffic(db, port_ids, until, window)
    ident = counters_q.identity_for(db, fabric_id, port_ids)

    # Port -> its link's line rate, for the peak utilisation figure. 
    # Only links carry a rate.
    rate_of: dict[tuple[str, int], float] = {}
    for l in links:
        if l.rate_gbps:
            rate_of[(l.source, l.source_port)] = l.rate_gbps
            rate_of[(l.target, l.target_port)] = l.rate_gbps

    tx = rx = 0.0
    peak: float | None = None
    notes: list[PortNote] = []
    first_at: datetime | None = None
    last_at: datetime | None = None

    for port_id in port_ids:
        r = got.get(port_id)
        if r is None:
            continue
        guid, port_num = ident.get(port_id, (None, None))
        if guid is None:
            continue
        guid_hex = guid_str(guid)

        tx += r.tx
        rx += r.rx
        last_at = r.at if last_at is None else max(last_at, r.at)
        started = r.at - timedelta(seconds=r.span_s)
        first_at = started if first_at is None else min(first_at, started)

        # The busier direction, matching the overlay.
        busier = max(r.tx, r.rx)
        rate = rate_of.get((guid_hex, port_num))
        if rate:
            pct = busier / rate * 100
            peak = pct if peak is None else max(peak, pct)

        notes.append(PortNote(
            node=guid_hex, label=_label_of(by_guid, guid_hex), port=port_num,
            value=round(busier, 4),
            detail={"tx": round(r.tx, 4), "rx": round(r.rx, 4)},
        ))

    notes.sort(key=lambda n: (-n.value, n.label, n.port))

    return SelectionTraffic(
        as_of=last_at,
        span_s=((last_at - first_at).total_seconds()
                if first_at and last_at else None),
        cadence_s=cadence.total_seconds() if cadence else None,
        tx_gbps=round(tx, 3),
        rx_gbps=round(rx, 3),
        capacity_gbps=capacity_gbps,
        peak_utilisation_pct=round(peak, 1) if peak is not None else None,
        ports_measured=len(notes),
        ports_no_data=len(port_ids) - len(notes),
        top_ports=notes[:MAX_TOP_PORTS],
    )


# ---- shared ---------------------------------------------------------------

def _label_of(by_guid: dict[str, NodeElement], guid_hex: str) -> str:
    el = by_guid.get(guid_hex)
    return el.label if el is not None else guid_hex


def _tally(values) -> list[Tallied]:
    return _tallied(Counter(values))


def _tallied(c: Counter) -> list[Tallied]:
    """Count descending, then alphabetical, so the order is stable between two
    requests whose counts happen to tie."""
    return [Tallied(key=k, count=n)
            for k, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]
