"""The lean graph: every node and link as of an instant, with health derived.

One query for nodes, one for links. The link query drags both endpoints'
port_state along because health belongs to the pair -- the ceiling a link should
be running at is what the two ends *mutually* enable, so it cannot be judged one
port at a time.

Node health is the worst-wins rollup over incident links, computed here rather
than in SQL: it is dict lookups over rows already in memory, and one Python
function keeps the live map and the diff from drifting apart.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime

from ibcore import decode
from ibcore.guid import guid_str
from ibcore.health import Health, PortView, link_health, node_health
from ibcore.ids import edge_id

from ..db import Database, as_of, sql
from ..models import Counts, LinkElement, NodeElement, Rate, SystemGroup

_NODES = """
    SELECT n.node_id, n.node_guid, n.node_type, n.sys_image_guid,
           n.vendor_id, n.device_id, n.num_ports,
           ns.node_desc, ns.sm_state
    FROM node n
    JOIN node_state ns ON ns.node_id = n.node_id AND {state}
    WHERE n.fabric_id = %(fabric_id)s
"""

# Both endpoints' state in one row.
_LINKS = """
    SELECT na.node_guid AS a_guid, pa.port_number AS a_port,
           nb.node_guid AS b_guid, pb.port_number AS b_port,
           psa.log_state AS a_log, psa.active_speed AS a_aspeed,
           psa.active_width AS a_awidth, psa.enabled_speed AS a_espeed,
           psa.enabled_width AS a_ewidth, psa.supported_speed AS a_sspeed,
           psa.supported_width AS a_swidth,
           psb.log_state AS b_log, psb.active_speed AS b_aspeed,
           psb.active_width AS b_awidth, psb.enabled_speed AS b_espeed,
           psb.enabled_width AS b_ewidth, psb.supported_speed AS b_sspeed,
           psb.supported_width AS b_swidth
    FROM link_state ls
    JOIN port pa ON pa.port_id = ls.port_a_id
    JOIN port pb ON pb.port_id = ls.port_b_id
    JOIN node na ON na.node_id = pa.node_id
    JOIN node nb ON nb.node_id = pb.node_id
    LEFT JOIN port_state psa ON psa.port_id = ls.port_a_id AND {a_state}
    LEFT JOIN port_state psb ON psb.port_id = ls.port_b_id AND {b_state}
    WHERE pa.fabric_id = %(fabric_id)s AND {link_state}
"""


def fetch(db: Database, fabric_id: int, at: datetime | None):
    """-> (nodes, links, system_groups, counts), all wire-ready."""
    params = {"fabric_id": fabric_id, "at": at}

    with db.pool.connection() as conn:
        node_rows = conn.execute(
            sql(_NODES, state=as_of("ns", at)), params
        ).fetchall()
        link_rows = conn.execute(
            sql(_LINKS, a_state=as_of("psa", at), b_state=as_of("psb", at),
                link_state=as_of("ls", at)),
            params,
        ).fetchall()

    links = [_link_element(r) for r in link_rows]

    # Worst-wins rollup, gathered per endpoint before the nodes are built
    by_node: dict[str, list[Health]] = defaultdict(list)
    for link in links:
        by_node[link.source].append(link.health)
        by_node[link.target].append(link.health)

    nodes = [_node_element(db, r, by_node) for r in node_rows]
    groups = _system_groups(db, node_rows)
    return nodes, links, groups, _counts(nodes, links)


# ---- assembly ------------------------------------------------------------

def _port_view(r: dict, side: str) -> PortView | None:
    """PortView for one side of a link row, or None where state is missing."""
    if r[f"{side}_log"] is None and r[f"{side}_aspeed"] is None:
        return None
    return PortView(
        log_state=r[f"{side}_log"],
        active_speed=r[f"{side}_aspeed"], active_width=r[f"{side}_awidth"],
        enabled_speed=r[f"{side}_espeed"], enabled_width=r[f"{side}_ewidth"],
        supported_speed=r[f"{side}_sspeed"], supported_width=r[f"{side}_swidth"],
    )


def _link_element(r: dict) -> LinkElement:
    a, b = _port_view(r, "a"), _port_view(r, "b")
    health = link_health(a, b)

    # The reported rate is a's view. An asymmetric link is a fault, and the
    # health verdict above already catches it; picking a side here only decides
    # which number is shown next to the fault.
    speed_mask = r["a_aspeed"]
    width_mask = r["a_awidth"]
    speed_label = decode.best_speed(speed_mask)
    width_label = decode.best_width(width_mask)

    return LinkElement(
        id=edge_id((r["a_guid"], r["a_port"]), (r["b_guid"], r["b_port"])),
        source=guid_str(r["a_guid"]),
        target=guid_str(r["b_guid"]),
        source_port=r["a_port"],
        target_port=r["b_port"],
        health=health,
        speed=Rate(mask=speed_mask, label=speed_label),
        width=Rate(mask=width_mask, label=width_label),
        rate_gbps=decode.link_rate(width_label, speed_label),
    )


def _node_element(db: Database, r: dict, by_node: dict[str, list[Health]]) -> NodeElement:
    guid = guid_str(r["node_guid"])
    sys_img = guid_str(r["sys_image_guid"])
    label = db.node_name(guid, r["node_desc"]) or guid
    sm = r["sm_state"] if r["sm_state"] and r["sm_state"] != "none" else None
    return NodeElement(
        id=guid,
        label=label,
        type=r["node_type"],
        health=node_health(by_node.get(guid, [])),
        system_image_guid=sys_img,
        sm_role=sm,
        num_ports=r["num_ports"],
    )


def _system_groups(db: Database, node_rows: list[dict]) -> dict[str, SystemGroup]:
    """Chassis grouping, derived from sys_image_guid.

    Singletons are dropped: a group of one is not a chassis, and rendering a
    compound parent around every standalone HCA is just noise.
    """
    children: dict[str, list[str]] = defaultdict(list)
    for r in node_rows:
        if (sgid := guid_str(r["sys_image_guid"])) is not None:
            children[sgid].append(guid_str(r["node_guid"]))
    return {
        sgid: SystemGroup(label=db.group_name(sgid) or sgid, children=sorted(kids))
        for sgid, kids in children.items() if len(kids) > 1
    }


def _counts(nodes: list[NodeElement], links: list[LinkElement]) -> Counts:
    """Derived from what is actually being returned, so the legend can never
    disagree with the map."""
    by_type: dict[str, int] = defaultdict(int)
    for n in nodes:
        by_type[n.type] += 1
    by_health: dict[Health, int] = defaultdict(int)
    for l in links:
        by_health[l.health] += 1
    return Counts(
        nodes=len(nodes),
        switches=by_type.get("switch", 0),
        cas=by_type.get("ca", 0),
        routers=by_type.get("router", 0),
        links=len(links),
        health=dict(by_health),
    )
