"""The source-neutral snapshot the writer consumes.

These dataclasses mirror the database structure. A collector's job is to produce 
an `Observation`; the writer's job is to write it to the database.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# (node_guid, port_number) - the writer resolves this to a port_id
PortKey = tuple[int, int]


@dataclass(frozen=True, slots=True)
class ObservedNode:
    """Identity + node-level state for one node. Maps to `node` + `node_state`."""

    # identity (immutable; written once)
    guid: int
    node_type: str | None            # 'ca' | 'switch' | 'router' | None
    sys_image_guid: int | None
    vendor_id: int | None
    device_id: int | None
    num_ports: int | None
    # state (versioned)
    desc: str | None
    # 'none' | 'discovering' | 'standby' | 'master', from SMInfoRecord. Carried on
    sm_state: str = "none"
    # fw_version deferred: it is not in any record the collector reads. It needs
    # ibdiagnet or a vendor MAD, NOT SMInfoRecord.


@dataclass(frozen=True, slots=True)
class ObservedPort:
    """Identity + port state for one port. Maps to `port` + `port_state`."""

    # identity
    node_guid: int
    port_number: int
    port_guid: int | None
    # state
    log_state: str | None
    phys_state: str | None
    active_speed: int
    active_width: int
    enabled_speed: int
    enabled_width: int
    supported_speed: int
    supported_width: int
    lid: int | None
    mtu: int | None

    @property
    def key(self) -> PortKey:
        return (self.node_guid, self.port_number)


@dataclass(frozen=True, slots=True)
class ObservedLink:
    """One physical link, as an unordered pair of port keys, canonicalised (a <= b)"""

    a: PortKey
    b: PortKey

    @staticmethod
    def canonical(x: PortKey, y: PortKey) -> "ObservedLink":
        return ObservedLink(x, y) if x <= y else ObservedLink(y, x)


@dataclass
class Observation:
    """Everything one sweep saw, ready for the writer"""

    collected_at: str                 # ISO-8601; one instant for the whole cycle
    nodes: dict[int, ObservedNode] = field(default_factory=dict)   # guid -> node
    ports: dict[PortKey, ObservedPort] = field(default_factory=dict)
    links: set[ObservedLink] = field(default_factory=set)

    # 'live' | 'from_file'
    origin: str = "live"

    # Wall time to acquire and assemble, in milliseconds
    duration_ms: int | None = None

    # True only if every query was clean and no block was dropped. When False the
    # writer records the snapshot but refuses to diff.
    complete: bool = True

    # Whether SMInfoRecord was read and understood this sweep. 
    sm_seen: bool = True

    # Human-readable reasons the sweep was marked incomplete.
    problems: list[str] = field(default_factory=list)

    def mark_incomplete(self, reason: str) -> None:
        self.complete = False
        self.problems.append(reason)

    def counts(self) -> tuple[int, int, int]:
        return (len(self.nodes), len(self.ports), len(self.links))
