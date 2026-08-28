"""Per-selection detail: one node, or one link, complete in a single response."""

from __future__ import annotations

from datetime import datetime, timedelta

from ibcore import decode
from ibcore.guid import guid_str, parse_guid
from ibcore.health import Health, PortView, link_health, node_health, reasons
from ibcore.ids import edge_id, parse_edge_id

from ..db import Database, as_of, sql
from . import counters as counters_q
from ..models import (LinkDetail, LinkEnd, NodeDetail, Peer, PortDetail,
                      PortRate, Rate, Resolved)

_NODE = """
    SELECT n.node_id, n.node_guid, n.node_type, n.sys_image_guid, n.vendor_id,
           n.device_id, n.num_ports, n.first_seen,
           ns.node_desc, ns.sm_state, ns.fw_version
    FROM node n
    JOIN node_state ns ON ns.node_id = n.node_id AND {ns_state}
    WHERE n.fabric_id = %(fabric_id)s AND n.node_guid = %(guid)s
"""

# Every port of one node, each with its own state, its peer, and the peer's state. 
_PORTS = """
    SELECT p.port_id, p.port_number, p.port_guid,
           ps.log_state, ps.phys_state, ps.lid, ps.mtu,
           ps.active_speed, ps.active_width,
           ps.enabled_speed, ps.enabled_width,
           ps.supported_speed, ps.supported_width,

           peer_p.port_number   AS peer_port,
           peer_n.node_guid     AS peer_guid,
           peer_n.node_type     AS peer_type,
           peer_ns.node_desc    AS peer_desc,
           peer_ps.log_state       AS peer_log,
           peer_ps.active_speed    AS peer_aspeed,
           peer_ps.active_width    AS peer_awidth,
           peer_ps.enabled_speed   AS peer_espeed,
           peer_ps.enabled_width   AS peer_ewidth,
           peer_ps.supported_speed AS peer_sspeed,
           peer_ps.supported_width AS peer_swidth
    FROM port p
    LEFT JOIN port_state ps
           ON ps.port_id = p.port_id AND {ps_state}
    LEFT JOIN link_state ls
           ON (ls.port_a_id = p.port_id OR ls.port_b_id = p.port_id) AND {ls_state}
    LEFT JOIN port peer_p
           ON peer_p.port_id = CASE WHEN ls.port_a_id = p.port_id
                                    THEN ls.port_b_id ELSE ls.port_a_id END
    LEFT JOIN node peer_n
           ON peer_n.node_id = peer_p.node_id
    LEFT JOIN node_state peer_ns
           ON peer_ns.node_id = peer_n.node_id AND {peer_ns_state}
    LEFT JOIN port_state peer_ps
           ON peer_ps.port_id = peer_p.port_id AND {peer_ps_state}
    WHERE p.node_id = %(node_id)s
    ORDER BY p.port_number
"""

# Both ends of one link, addressed by the (guid, port) pairs its id encodes.
_ENDS = """
    SELECT p.port_id, p.port_number, p.port_guid,
           n.node_guid, n.node_type, ns.node_desc,
           ps.log_state, ps.phys_state, ps.lid, ps.mtu,
           ps.active_speed, ps.active_width,
           ps.enabled_speed, ps.enabled_width,
           ps.supported_speed, ps.supported_width
    FROM port p
    JOIN node n ON n.node_id = p.node_id
    LEFT JOIN node_state ns ON ns.node_id = n.node_id AND {ns_state}
    LEFT JOIN port_state ps ON ps.port_id = p.port_id AND {ps_state}
    WHERE p.fabric_id = %(fabric_id)s
      AND (n.node_guid, p.port_number) IN ((%(g1)s, %(p1)s), (%(g2)s, %(p2)s))
"""


def node(db: Database, fabric_id: int, guid_hex: str, at: datetime | None,
         resolved: Resolved,
         counters_window: timedelta | None = None,
         with_traffic: bool = False) -> NodeDetail | None:
    """One node with every port, each port's peer, and a health verdict."""
    guid = parse_guid(guid_hex)
    params = {"fabric_id": fabric_id, "guid": guid, "at": at}

    with db.pool.connection() as conn:
        n = conn.execute(sql(_NODE, ns_state=as_of("ns", at)), params).fetchone()
        if n is None:
            return None
        rows = conn.execute(
            sql(_PORTS, ps_state=as_of("ps", at), ls_state=as_of("ls", at),
                peer_ns_state=as_of("peer_ns", at),
                peer_ps_state=as_of("peer_ps", at)),
            {**params, "node_id": n["node_id"]},
        ).fetchall()

    counters = _counters_for(db, rows, at, resolved, counters_window)
    traffic = _traffic_for(db, fabric_id, rows, at, resolved, with_traffic)
    ports = [_port_detail(db, r, n["node_guid"], counters, traffic) for r in rows]
    # Annotated because an unannotated comprehension widens the Health literals
    # back to `str`, which node_health no longer accepts.
    healths: list[Health] = [p.peer.health for p in ports if p.peer and p.peer.health]
    guid_s = guid_str(n["node_guid"])
    vendor, model = _device(n["vendor_id"], n["device_id"])

    return NodeDetail(
        vendor=vendor,
        model=model,
        resolved=resolved,
        guid=guid_s,
        label=db.node_name(guid_s, n["node_desc"]) or guid_s,
        desc=n["node_desc"],
        type=n["node_type"],
        health=node_health(healths),
        num_ports=n["num_ports"],
        sm_state=n["sm_state"],
        fw_version=n["fw_version"],
        vendor_id=n["vendor_id"],
        device_id=n["device_id"],
        system_image_guid=guid_str(n["sys_image_guid"]),
        first_seen=n["first_seen"],
        ports=ports,
    )


def link(db: Database, fabric_id: int, link_id: str, at: datetime | None,
         resolved: Resolved,
         counters_window: timedelta | None = None,
         with_traffic: bool = False) -> LinkDetail | None:
    """One link with both ends fully expanded - no follow-up fetch needed."""
    (g1, p1), (g2, p2) = parse_edge_id(link_id)

    with db.pool.connection() as conn:
        rows = conn.execute(
            sql(_ENDS, ns_state=as_of("ns", at), ps_state=as_of("ps", at)),
            {"fabric_id": fabric_id, "at": at,
             "g1": g1, "p1": p1, "g2": g2, "p2": p2},
        ).fetchall()

    if len(rows) != 2:
        return None

    # Order the ends to match the id, so `a` is always the id's first half.
    rows.sort(key=lambda r: (r["node_guid"], r["port_number"]) != (g1, p1))
    a_view, b_view = _view(rows[0]), _view(rows[1])
    health = link_health(a_view, b_view)
    # Both ends in one call. Selecting a link is meant to be one request, and
    # that includes the counters that made it worth selecting.
    counters = _counters_for(db, rows, at, resolved, counters_window)
    traffic = _traffic_for(db, fabric_id, rows, at, resolved, with_traffic)

    return LinkDetail(
        resolved=resolved,
        id=edge_id((rows[0]["node_guid"], rows[0]["port_number"]),
                   (rows[1]["node_guid"], rows[1]["port_number"])),
        health=health,
        a=_link_end(db, rows[0], counters, traffic),
        b=_link_end(db, rows[1], counters, traffic),
        reason=[] if health == "ok" else reasons(a_view, b_view),
    )


# ---- assembly ------------------------------------------------------------

def _device(vendor_id: int | None, device_id: int | None):
    """Vendor and model names for an IEEE OUI plus vendor device id.

    The loader prints to stdout on first use, and it prefers a system 
    /usr/share/hwdata/pci.ids over the vendored copy, so names can differ 
    between a Linux deployment and a developer's machine. Failure to resolve 
    is not an error - the ids themselves are already in the response.
    """
    if vendor_id is None or device_id is None:
        return None, None
    try:
        from ibcore.vendor_device_parse import resolve as resolve_device
        info = resolve_device(vendor_id, device_id)
    except Exception:  # a missing pci.ids must not fail a detail request
        return None, None
    return (info.vendor or None), (info.model or None)


def _view(r: dict) -> PortView | None:
    """PortView for the row's own port, or None where it reported no state."""
    if r["log_state"] is None and r["active_speed"] is None:
        return None
    return PortView(
        log_state=r["log_state"],
        active_speed=r["active_speed"], active_width=r["active_width"],
        enabled_speed=r["enabled_speed"], enabled_width=r["enabled_width"],
        supported_speed=r["supported_speed"],
        supported_width=r["supported_width"],
    )


def _peer_view(r: dict) -> PortView | None:
    if r["peer_guid"] is None:
        return None
    if r["peer_log"] is None and r["peer_aspeed"] is None:
        return None
    return PortView(
        log_state=r["peer_log"],
        active_speed=r["peer_aspeed"], active_width=r["peer_awidth"],
        enabled_speed=r["peer_espeed"], enabled_width=r["peer_ewidth"],
        supported_speed=r["peer_sspeed"], supported_width=r["peer_swidth"],
    )


def _rates(r: dict) -> dict[str, Rate]:
    """The six speed/width fields, each as stored mask plus decoded label."""
    return {
        "speed_active": Rate(mask=r["active_speed"],
                             label=decode.best_speed(r["active_speed"])),
        "speed_enabled": Rate(mask=r["enabled_speed"],
                              label=decode.best_speed(r["enabled_speed"])),
        "speed_supported": Rate(mask=r["supported_speed"],
                                label=decode.best_speed(r["supported_speed"])),
        "width_active": Rate(mask=r["active_width"],
                             label=decode.best_width(r["active_width"])),
        "width_enabled": Rate(mask=r["enabled_width"],
                              label=decode.best_width(r["enabled_width"])),
        "width_supported": Rate(mask=r["supported_width"],
                                label=decode.best_width(r["supported_width"])),
    }


def _base_port(r: dict) -> dict:
    """The fields common to both detail shapes."""
    rates = _rates(r)
    return dict(
        port_num=r["port_number"],
        port_guid=guid_str(r["port_guid"]),
        lid=r["lid"],
        state=r["log_state"],
        phys_state=r["phys_state"],
        mtu=r["mtu"],
        mtu_bytes=decode.mtu_bytes(r["mtu"]),
        rate_gbps=decode.link_rate(rates["width_active"].label,
                                   rates["speed_active"].label),
        **rates,
    )


def _counters_for(db: Database, rows: list[dict], at: datetime | None,
                  resolved: Resolved, window: timedelta | None,
                  ) -> dict[int, counters_q.PortCounters]:
    """Counters for the ports in `rows`, or nothing when none were asked for.

    `until` is the RESOLVED instant, not the requested one. A card reading
    counters up to "now" beside a topology resolved to a sweep ten minutes ago
    would be bad.
    """
    
    if window is None:
        return {}
    until = at or resolved.collected_at
    port_ids = [r["port_id"] for r in rows if r.get("port_id") is not None]
    return counters_q.port_counters(db, port_ids, until - window, until)


def _traffic_for(db: Database, fabric_id: int, rows: list[dict],
                 at: datetime | None, resolved: Resolved,
                 wanted: bool) -> dict[int, counters_q.Rates]:
    """Throughput for the ports in `rows`, when the card asked for it."""
    
    if not wanted:
        return {}
    until = at or resolved.collected_at
    port_ids = [r["port_id"] for r in rows if r.get("port_id") is not None]
    cadence = counters_q.traffic_cadence(db, fabric_id, until)
    return counters_q.port_traffic(db, port_ids, until,
                                   counters_q.default_window(cadence))


def _with_counters(base: dict, port_id: int | None,
                   counters: dict[int, counters_q.PortCounters],
                   traffic: dict[int, counters_q.Rates] | None = None) -> dict:
    """Fold one port's counters and rate into the field dict, if either was read."""
    
    out = dict(base)
    got = counters.get(port_id) if port_id is not None else None
    if got is not None:
        out |= {"counters": got.raw, "counters_at": got.at,
                "counters_delta": got.delta, "counters_span_s": got.span_s}
    rate = (traffic or {}).get(port_id) if port_id is not None else None
    if rate is not None:
        out["traffic"] = PortRate(
            tx=round(rate.tx, 4), rx=round(rate.rx, 4),
            txp=round(rate.txp, 1), rxp=round(rate.rxp, 1),
            span_s=rate.span_s, at=rate.at,
        )
    return out


def _port_detail(db: Database, r: dict, owner_guid: int,
                 counters: dict[int, counters_q.PortCounters] | None = None,
                 traffic: dict[int, counters_q.Rates] | None = None,
                 ) -> PortDetail:
    peer = None
    if r["peer_guid"] is not None:
        peer_guid = guid_str(r["peer_guid"])
        peer = Peer(
            guid=peer_guid,
            name=db.node_name(peer_guid, r["peer_desc"]) or peer_guid,
            type=r["peer_type"],
            port_num=r["peer_port"],
            link_id=edge_id((owner_guid, r["port_number"]),
                            (r["peer_guid"], r["peer_port"])),
            health=link_health(_view(r), _peer_view(r)),
        )
    return PortDetail(peer=peer,
                      **_with_counters(_base_port(r), r.get("port_id"),
                                       counters or {}, traffic))


def _link_end(db: Database, r: dict,
              counters: dict[int, counters_q.PortCounters] | None = None,
              traffic: dict[int, counters_q.Rates] | None = None,
              ) -> LinkEnd:
    guid = guid_str(r["node_guid"])
    return LinkEnd(
        guid=guid,
        label=db.node_name(guid, r["node_desc"]) or guid,
        type=r["node_type"],
        port=PortDetail(**_with_counters(_base_port(r), r.get("port_id"),
                                         counters or {}, traffic)),
    )
