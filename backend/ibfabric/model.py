"""Data model for a single ibdiagnet2 fabric snapshot.

The top-level object is :class:`FabricGraph`: nodes keyed by GUID, each holding its
ports, and a list of links joining port to port. Counters and cable info are attached
onto the ports they belong to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum, IntEnum


# --------------------------------------------------------------------------- enums


class NodeType(IntEnum):
    CA = 1  # HCA / channel adapter (compute node)
    SWITCH = 2
    ROUTER = 3

    @property
    def label(self) -> str:
        return self.name.title()


class PortState(IntEnum):
    NOP = 0
    DOWN = 1
    INIT = 2
    ARMED = 3
    ACTIVE = 4


class LinkHealth(str, Enum):
    OK = "ok"
    DEGRADED = "degraded"  # up, but wrong speed/width or nonzero error counters
    DOWN = "down"          # an endpoint is not Active
    UNKNOWN = "unknown"    # missing port data on an endpoint


def guid_str(guid: int | None) -> str:
    """Render a GUID int back to its canonical ``0x…16hex`` form."""
    return "N/A" if guid is None else f"0x{guid:016x}"


# ------------------------------------------------------------------------ entities


@dataclass
class PortCounters:
    """One row of PM data for a port"""

    symbol_error: int | None = None
    link_downed: int | None = None
    link_error_recovery: int | None = None
    port_rcv_errors: int | None = None
    port_xmit_discards: int | None = None
    port_rcv_remote_physical_errors: int | None = None
    local_link_integrity_errors: int | None = None
    excessive_buffer_overrun_errors: int | None = None
    vl15_dropped: int | None = None
    port_xmit_data: int | None = None
    port_rcv_data: int | None = None
    port_xmit_pkts: int | None = None
    port_rcv_pkts: int | None = None
    port_xmit_wait: int | None = None
    retransmission_per_sec: int | None = None
    raw: dict[str, int | None] = field(default_factory=dict) # counters not accounted for above

    # error counters that mark a link degraded when nonzero in the delta
    ERROR_FIELDS = (
        "symbol_error",
        "link_downed",
        "port_rcv_errors",
        "port_xmit_discards",
        "port_rcv_remote_physical_errors",
        "local_link_integrity_errors",
        "excessive_buffer_overrun_errors",
    )

    def has_errors(self) -> bool:
        return any((getattr(self, f) or 0) > 0 for f in self.ERROR_FIELDS)


@dataclass
class Cable:
    node_guid: int
    port_num: int
    vendor: str | None = None
    pn: str | None = None
    sn: str | None = None
    rev: str | None = None
    length_desc: str | None = None
    type_desc: str | None = None
    supported_speed: str | None = None
    nominal_bitrate: str | None = None
    temperature: str | None = None
    raw: dict[str, str] = field(default_factory=dict)


@dataclass
class Port:
    node_guid: int
    port_num: int
    port_guid: int | None = None
    lid: int | None = None
    state: PortState = PortState.NOP
    phys_state: int | None = None
    width_active: str | None = None
    width_supported: list[str] | None = None
    width_enabled: list[str] | None = None
    speed_active: str | None = None
    speed_enabled: list[str] | None = None
    flow_rate: float | None = None
    fec_active: int | None = None
    mtu: int | None = None
    counters: PortCounters | None = None       # raw / cumulative (PM_INFO)
    counters_delta: PortCounters | None = None  # since last reset (PM_DELTA)
    cable: Cable | None = None
    # names of link-check events (speed/width) that fired on this port
    check_events: list[str] = field(default_factory=list)

    @property
    def key(self) -> tuple[int, int]:
        return (self.node_guid, self.port_num)


@dataclass
class Node:
    guid: int
    desc: str
    node_type: NodeType
    num_ports: int | None = None
    system_image_guid: int | None = None
    device_id: int | None = None
    vendor_id: int | None = None
    model: str | None = None
    vendor: str | None = None
    fw_version: str | None = None
    ports: dict[int, Port] = field(default_factory=dict)  # port_num -> Port

@dataclass
class Switch:
    """Switch-specific config (from the SWITCHES section)."""

    guid: int
    raw: dict[str, int | None] = field(default_factory=dict)


@dataclass
class GuidMappings:
    system_image_guid: dict[int, str]
    node_guid: dict[int, str]


@dataclass
class SmInfo:
    guid: int
    port: int | None
    priority: int | None
    sm_state: int | None  # 2 = standby, 3 = master

    @property
    def role(self) -> str:
        return {3: "master", 2: "standby"}.get(self.sm_state or 0, "other")


@dataclass
class LinkCheckEvent:
    """A speed/width/fw check the diagnostic flagged (from ERRORS_*/WARNINGS_*)."""

    scope: str
    node_guid: int | None
    port_num: int | None
    event_name: str
    summary: str


@dataclass
class Link:
    """An edge between two ports. 'a' and 'b' are the Port objects."""

    a: Port
    b: Port

    @property
    def health(self) -> LinkHealth:
        if self.a is None or self.b is None:
            return LinkHealth.UNKNOWN
        if self.a.state != PortState.ACTIVE or self.b.state != PortState.ACTIVE:
            return LinkHealth.DOWN
        if self.a.check_events or self.b.check_events:
            # TODO: check against actual cable SupportedSpeed if LINK_UNEXPECTED_SPEED
            return LinkHealth.DEGRADED
        for p in (self.a, self.b):
            if p.counters_delta and p.counters_delta.has_errors():
                return LinkHealth.DEGRADED
        return LinkHealth.OK

    @property
    def speed_active(self) -> str | None:
        return self.a.speed_active if self.a else None

    @property
    def width_active(self) -> str | None: # No of physical lanes in the link
        return self.a.width_active if self.a else None
    
    @property
    def flow_rate(self) -> float | None: # Link rate in Gbps
        return self.a.flow_rate if self.a else None


@dataclass
class FabricGraph:
    """The assembled snapshot of the fabric, keyed by node GUID, with a list of links and other metadata."""

    # from RUN_INFO
    run_timestamp: str | None = None
    source_path: str | None = None
    versions: dict[str, str] = field(default_factory=dict)
    args: str | None = None

    nodes: dict[int, Node] = field(default_factory=dict)          # guid -> Node
    
    system_groups: dict[int, list[int]] = field(default_factory=dict)  # sgid -> list of children node guids
    
    links: list[Link] = field(default_factory=list)
    adjacency: dict[int, list[Link]] = field(default_factory=dict)  # guid -> links
    switches: dict[int, Switch] = field(default_factory=dict)      # guid -> Switch
    sm: list[SmInfo] = field(default_factory=list)
    check_events: list[LinkCheckEvent] = field(default_factory=list)

    # -- lookups -------------------------------------------------------------

    def port(self, node_guid: int | None, port_num: int | None) -> Port | None:
        if node_guid is None or port_num is None:
            return None
        node = self.nodes.get(node_guid)
        return node.ports.get(port_num) if node else None

    # Adjacency list stores links, here we get the actual nodes
    def neighbors(self, guid: int) -> list[Node]:
        out: dict[int, Node] = {}
        for link in self.adjacency.get(guid, []):
            other = link.b if link.a.node_guid == guid else link.a
            if other.node_guid in self.nodes:
                out[other.node_guid] = self.nodes[other.node_guid]
        return list(out.values())

    def unhealthy_links(self) -> list[Link]:
        return [l for l in self.links if l.health != LinkHealth.OK]

    # -- summary -------------------------------------------------------------

    def counts(self) -> dict[str, int | dict[str, int]]:
        switches = sum(1 for n in self.nodes.values() if n.node_type == NodeType.SWITCH)
        cas = sum(1 for n in self.nodes.values() if n.node_type == NodeType.CA)
        by_health: dict[str, int] = {}
        for link in self.links:
            by_health[link.health.value] = by_health.get(link.health.value, 0) + 1
        return {
            "nodes": len(self.nodes),
            "switches": switches,
            "cas": cas,
            "links": len(self.links),
            "check_events": len(self.check_events),
            "health": {
                "ok": by_health.get(LinkHealth.OK.value, 0),
                "degraded": by_health.get(LinkHealth.DEGRADED.value, 0),
                "down": by_health.get(LinkHealth.DOWN.value, 0),
            },
        }
