"""Serialize a :class:`FabricGraph` to a renderer-neutral, JSON-able dict.

The shape has two layers:

* ``nodes`` / ``links`` — lean elements for the topology view (styling, layout,
  filtering). This is what a Cytoscape adapter maps to graph elements.
* ``detail`` — a lookup keyed by the same node/edge ids, holding the heavy
  per-port data (counters, cable, check events) for selection panels. Never
  touched during layout/render.

Conventions:

* GUIDs are always strings (``guid_str`` form) — 64-bit ints would lose
  precision as JSON numbers in the browser.
* Enums are lowercase strings for display (``node_type``, ``health``, port
  ``state``).
* Missing values are ``null`` (keys are always present).
* Node id = GUID string. Edge id = ``"{guidLo}:{portLo}-{guidHi}:{portHi}"``
  with endpoints sorted canonically, so a link's identity is stable across
  snapshots regardless of which endpoint the parser called ``a``.
"""

from __future__ import annotations

import dataclasses

from .model import (
    FabricGraph,
    Link,
    LinkHealth,
    Node,
    NodeType,
    GuidMappings,
    Port,
    PortCounters,
    SmInfo,
    guid_str,
)

# worst-wins ordering for the per-node health rollup
_HEALTH_SEVERITY = {
    LinkHealth.OK: 0,
    LinkHealth.UNKNOWN: 1,
    LinkHealth.DEGRADED: 2,
    LinkHealth.DOWN: 3,
}


# --------------------------------------------------------------------- ids / enums


def edge_id(link: Link) -> str:
    """Canonical, endpoint-order-independent id for a link."""
    lo, hi = sorted((
        (link.a.node_guid, link.a.port_num),
        (link.b.node_guid, link.b.port_num),
    ))
    return f"{guid_str(lo[0])}:{lo[1]}-{guid_str(hi[0])}:{hi[1]}"


def _node_type(t: NodeType) -> str:
    return t.name.lower()  # CA -> "ca", SWITCH -> "switch", ROUTER -> "router"


def _guid_or_none(guid: int | None) -> str | None:
    return guid_str(guid) if guid is not None else None


def _port_link_ids(g: FabricGraph) -> dict[tuple[int, int], str]:
    """Map each linked port ``(node_guid, port_num)`` to its link's ``edge_id``.
    A port participates in at most one link, so this is 1:1."""
    m: dict[tuple[int, int], str] = {}
    for link in g.links:
        eid = edge_id(link)
        m[link.a.key] = eid
        m[link.b.key] = eid
    return m


# ----------------------------------------------------------------- detail helpers


def _counters(c: PortCounters | None) -> dict | None:
    """Named fields plus the raw column bag (``asdict`` includes ``raw``)."""
    return None if c is None else dataclasses.asdict(c)


def _cable(cable) -> dict | None:
    if cable is None:
        return None
    d = dataclasses.asdict(cable)
    # node_guid/port_num are redundant here (the port already identifies them)
    d.pop("node_guid", None)
    d.pop("port_num", None)
    return d


def _port(p: Port, port_links: dict[tuple[int, int], str]) -> dict:
    return {
        "port_num": p.port_num,
        "port_guid": _guid_or_none(p.port_guid),
        "lid": p.lid,
        "state": p.state.name.lower(),
        "phys_state": p.phys_state,
        "width_active": p.width_active,
        "width_supported": p.width_supported,
        "width_enabled": p.width_enabled,
        "speed_active": p.speed_active,
        "speed_enabled": p.speed_enabled,
        "flow_rate": p.flow_rate,
        "fec_active": p.fec_active,
        "mtu": p.mtu,
        "check_events": list(p.check_events),
        "counters": _counters(p.counters),
        "counters_delta": _counters(p.counters_delta),
        "cable": _cable(p.cable),
        # id of the link on this port (null for down/unused ports); lets the
        # frontend traverse port -> link without rebuilding an adjacency index
        "link_id": port_links.get(p.key),
    }


def _node_detail(n: Node, port_links: dict[tuple[int, int], str]) -> dict:
    return {
        "guid": guid_str(n.guid),
        "desc": n.desc,
        "type": _node_type(n.node_type),
        "num_ports": n.num_ports,
        "fw_version": n.fw_version,
        "device_id": n.device_id,
        "vendor_id": n.vendor_id,
        "model": n.model,
        "vendor": n.vendor,
        "system_image_guid": _guid_or_none(n.system_image_guid),
        "ports": {str(pn): _port(p, port_links) for pn, p in sorted(n.ports.items())},
    }


def _link_reason(link: Link) -> dict:
    """Why a link isn't ``ok``: which checks fired and which delta error counters
    are nonzero. Link-level and awkward to reconstruct client-side."""
    events: list[str] = []
    error_counters: list[str] = []
    for p in (link.a, link.b):
        events.extend(p.check_events)
        if p.counters_delta and p.counters_delta.has_errors():
            error_counters.extend(
                f for f in PortCounters.ERROR_FIELDS
                if (getattr(p.counters_delta, f) or 0) > 0
            )
    return {
        "check_events": sorted(set(events)),
        "error_counters": sorted(set(error_counters)),
    }


def _link_detail(link: Link) -> dict:
    return {
        "id": edge_id(link),
        "health": link.health.value,
        "a": {"guid": guid_str(link.a.node_guid), "port_num": link.a.port_num},
        "b": {"guid": guid_str(link.b.node_guid), "port_num": link.b.port_num},
        "reason": _link_reason(link),
    }


# ---------------------------------------------------------------- element helpers


def _node_health(g: FabricGraph, guid: int) -> str:
    worst = LinkHealth.OK
    for link in g.adjacency.get(guid, []):
        if _HEALTH_SEVERITY[link.health] > _HEALTH_SEVERITY[worst]:
            worst = link.health
    return worst.value


def _node_element(g: FabricGraph, n: Node, sm_by_guid: dict[int, SmInfo]) -> dict:
    s = sm_by_guid.get(n.guid)
    return {
        "id": guid_str(n.guid),
        "system_image_guid": _guid_or_none(n.system_image_guid),
        "label": n.desc or guid_str(n.guid),
        "type": _node_type(n.node_type),
        "sm_role": s.role if s else None,
        "num_ports": n.num_ports,
        "health": _node_health(g, n.guid),
    }


def _link_element(link: Link) -> dict:
    return {
        "id": edge_id(link),
        "source": guid_str(link.a.node_guid),
        "target": guid_str(link.b.node_guid),
        "source_port": link.a.port_num,
        "target_port": link.b.port_num,
        "health": link.health.value,
        "speed": link.speed_active,
        "width": link.width_active,
        "flow_rate": link.flow_rate,
    }


# ------------------------------------------------------------------------- top level


def to_dict(g: FabricGraph, mappings: GuidMappings | None = None) -> dict:
    """Assemble the full contract dict"""
    sm_by_guid = {s.guid: s for s in g.sm}
    port_links = _port_link_ids(g)
    return {
        "meta": {
            "run_timestamp": g.run_timestamp,
            "source_path": g.source_path,
            "versions": g.versions,
            "args": g.args,
            "counts": g.counts(),
            "sm": [
                {
                    "guid": guid_str(s.guid),
                    "role": s.role,
                    "priority": s.priority,
                    "port": s.port,
                }
                for s in g.sm
            ],
        },
        "system_groups": {
            guid_str(sgid): {
                "label": mappings.system_image_guid.get(sgid, "") if mappings else "", 
                "children": [guid_str(guid) for guid in children] 
                }
                for sgid, children in g.system_groups.items()
        },
        "nodes": [_node_element(g, n, sm_by_guid) for n in g.nodes.values()],
        "links": [_link_element(l) for l in g.links],
        "detail": {
            "nodes": {guid_str(n.guid): _node_detail(n, port_links) for n in g.nodes.values()},
            "links": {edge_id(l): _link_detail(l) for l in g.links},
        },
    }
