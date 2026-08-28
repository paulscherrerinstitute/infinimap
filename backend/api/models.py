"""The API contract.

Two conventions run throughout:

  * **GUIDs are strings.** A 64-bit GUID does not survive JSON's float-backed
    numbers, and every one of these is 2^53 or larger.
  * **Raw beside decoded.** Speed and width carry both the stored mask and the
    label. The db keeps the masks because decoding is presentation and may need
    correcting.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from ibcore.health import Health

NodeKind = Literal["ca", "switch", "router"]
Change = Literal["added", "removed", "modified", "unchanged"]
SmRole = Literal["discovering", "standby", "master"]


# ---- shared --------------------------------------------------------------

class Resolved(BaseModel):
    """Which sweep answered this request. Present on every time-parameterised
    response.

    Built from the `db.Resolved` dataclass the pool hands back.
    """

    model_config = ConfigDict(from_attributes=True)

    requested_at: datetime
    snapshot_id: int
    collected_at: datetime
    latest_sweep_at: datetime | None = None
    latest_sweep_complete: bool | None = None
    topology_hash: str


class Rate(BaseModel):
    """A speed or width, as stored and as read."""

    mask: int | None = None
    label: str | None = None


class Counts(BaseModel):
    nodes: int = 0
    switches: int = 0
    cas: int = 0
    routers: int = 0
    links: int = 0
    health: dict[Health, int] = Field(default_factory=dict)


# ---- lean graph ----------------------------------------------------------

class NodeElement(BaseModel):
    id: str
    label: str
    type: NodeKind
    health: Health
    system_image_guid: str | None = None
    sm_role: SmRole | None = None
    num_ports: int | None = None


class LinkElement(BaseModel):
    id: str
    source: str
    target: str
    source_port: int
    target_port: int
    health: Health
    speed: Rate = Field(default_factory=Rate)
    width: Rate = Field(default_factory=Rate)
    rate_gbps: float | None = None


class SystemGroup(BaseModel):
    label: str
    children: list[str]


class Topology(BaseModel):
    resolved: Resolved
    counts: Counts
    nodes: list[NodeElement]
    links: list[LinkElement]
    system_groups: dict[str, SystemGroup]


# ---- detail --------------------------------------------------------------

class Peer(BaseModel):
    """The far end of a port's link, resolved so a panel needs no second fetch."""

    guid: str
    name: str | None = None
    type: NodeKind | None = None
    port_num: int
    link_id: str
    health: Health | None = None      # health of the link to this peer


class PortRate(BaseModel):
    """One port's throughput over a trailing window, for a detail card.

    Separate from `PortTraffic`, the overlay's sparse wire shape. This one is
    already attached to the port it describes, so it carries no identity and
    elides nothing -- an idle port on a card reads "0.00 Gbps" rather than
    vanishing the way it does from the overlay payload.
    """

    tx: float
    """Transmit rate, Gbps."""
    rx: float
    """Receive rate, Gbps."""
    txp: float
    """Transmit packet rate, packets/s."""
    rxp: float
    """Receive packet rate, packets/s."""
    span_s: float
    """The interval the two readings actually bounded."""
    at: datetime
    """When the newer reading was taken."""


class PortDetail(BaseModel):
    port_num: int
    port_guid: str | None = None
    lid: int | None = None
    state: str | None = None          # port_state.log_state; null is not "down"
    phys_state: str | None = None
    mtu: int | None = None            # raw IBTA enum 1-5
    mtu_bytes: int | None = None
    speed_active: Rate = Field(default_factory=Rate)
    speed_enabled: Rate = Field(default_factory=Rate)
    speed_supported: Rate = Field(default_factory=Rate)
    width_active: Rate = Field(default_factory=Rate)
    width_enabled: Rate = Field(default_factory=Rate)
    width_supported: Rate = Field(default_factory=Rate)
    rate_gbps: float | None = None
    peer: Peer | None = None

    # From the counters sweep, not the SA sweep -- a different cadence and a
    # different table, joined in only for a selected node or link.
    counters: dict["CounterName", int] | None = None
    """The newest raw reading at or before `at`. Dense -- all fourteen columns."""
    counters_at: datetime | None = None
    """When that reading was taken."""
    counters_delta: dict["CounterName", int] | None = None
    """What accumulated over the requested window, sparse. **None** means no
    delta could be computed -- no baseline, or a counter went backwards."""
    counters_span_s: float | None = None
    """The interval `counters_delta` actually covers.

    Not the window that was requested. The baseline is the last reading at or
    before the window opens, so a fabric collecting more slowly than the request
    yields a longer span -- and `xmit_wait` in particular is uninterpretable
    until it is divided by this."""
    traffic: PortRate | None = None
    """Throughput over a trailing window, when the card asked for it.

    **None means no rate could be stated** -- unpolled, or a byte counter
    wrapped -- and never "idle".
    """

    cable: dict | None = None
    check_events: list[str] = Field(default_factory=list)


class NodeDetail(BaseModel):
    resolved: Resolved
    guid: str
    label: str
    desc: str | None = None           # as the SA reported it, before overrides
    type: NodeKind
    health: Health
    num_ports: int | None = None
    sm_state: str | None = None
    fw_version: str | None = None
    vendor_id: int | None = None
    device_id: int | None = None
    vendor: str | None = None
    model: str | None = None
    system_image_guid: str | None = None
    first_seen: datetime | None = None
    ports: list[PortDetail] = Field(default_factory=list)


class LinkEnd(BaseModel):
    guid: str
    label: str | None = None
    type: NodeKind | None = None
    port: PortDetail


class LinkDetail(BaseModel):
    resolved: Resolved
    id: str
    health: Health
    a: LinkEnd
    b: LinkEnd
    reason: list[str] = Field(default_factory=list)


# ---- history -------------------------------------------------------------

class Head(BaseModel):
    """Cheap poll target. Refetch topology only when topology_hash moves."""

    fabric: str
    resolved: Resolved
    counts: Counts


class SnapshotRow(BaseModel):
    snapshot_id: int
    collected_at: datetime
    source: str
    origin: str
    changed: bool
    complete: bool
    node_count: int
    port_count: int
    link_count: int
    duration_ms: int | None = None


class Snapshots(BaseModel):
    """Written unconditionally by the collector, so a gap in this 
    list is a collector outage rather than a quiet fabric."""

    fabric: str
    snapshots: list[SnapshotRow]


class Event(BaseModel):
    event_id: int
    occurred_at: datetime
    event_type: str
    snapshot_id: int | None = None
    node_guid: str | None = None
    node_label: str | None = None
    port_num: int | None = None
    peer_guid: str | None = None
    peer_port_num: int | None = None
    link_id: str | None = None
    detail: dict | None = None


class Events(BaseModel):
    fabric: str
    since: datetime
    until: datetime
    truncated: bool = False
    events: list[Event]


class DiffNode(BaseModel):
    id: str
    change: Change
    churn: int = 0
    before: NodeElement | None = None
    after: NodeElement | None = None


class DiffLink(BaseModel):
    id: str
    change: Change
    churn: int = 0
    before: LinkElement | None = None
    after: LinkElement | None = None


class Diff(BaseModel):
    """The union of both endpoints' topologies, annotated.

    `churn` counts events regardless of net change, because a link that flapped
    repeatedly and came back is byte-identical in a net diff and is probably the
    most interesting thing in the range.

    `system_groups` is the union of both endpoints', so a chassis that lost its
    last node is still there for the removed children to sit inside.
    """

    fabric: str
    since: Resolved
    until: Resolved
    unchanged_guaranteed: bool = False
    nodes: list[DiffNode]
    links: list[DiffLink]
    system_groups: dict[str, SystemGroup] = Field(default_factory=dict)


# ---- counters ------------------------------------------------------------

CounterName = Literal[
    "symbol_error", "link_error_recovery", "link_downed", "rcv_errors",
    "rcv_remote_phys_errors", "rcv_switch_relay_errors", "xmit_discards",
    "xmit_constraint_errors", "rcv_constraint_errors", "local_link_integrity",
    "excessive_buffer_overrun", "vl15_dropped", "xmit_wait", "qp1_dropped",
]
"""The columns of `port_errors`, which is the contract. IBTA standards."""


class CounterWindow(BaseModel):
    """Which readings actually bounded the delta.

    Requested and observed instants are both present and they differ, because
    counters are sampled discretely: a request for 09:00 is answered by whatever
    reading sat nearest it. A client drawing a per-hour figure from a span that
    is not quite an hour is wrong, and without this cannot know it.
    """

    requested_since: datetime
    requested_until: datetime
    first_at: datetime | None = None
    last_at: datetime | None = None
    span_s: float | None = None
    resolution: Literal["raw", "hourly"]


class CounterCoverage(BaseModel):
    """How much of the fabric the window actually saw.

    `unsupported` is fabric-level and per counter.
    """

    ports_expected: int
    ports_measured: int
    ports_no_data: int
    ports_reset: int
    ports_partial: int
    sweeps: int
    unsupported: dict[CounterName, int] = Field(default_factory=dict)


class PortErrorDelta(BaseModel):
    """One port's accumulation over the window.

    Sparse in two directions: `d` carries only counters that moved, so a port
    with nothing to say is absent entirely.

    Absence therefore means zero, which is only safe because every case where it
    would instead mean *unknown* raises a flag below.

    Every field below `port` keeps a default and the route serialises with
    `exclude_defaults`, so a clean row is `{"node", "port", "d"}`.
    """

    node: str
    port: int
    d: dict[CounterName, int] = Field(default_factory=dict)

    no_data: bool = False
    """No reading bounded the window. Not zero -- unpolled."""

    reset: bool = False
    """A counter went backwards. Every delta on this port is a lower bound."""

    partial: bool = False
    """No reading at or before `since`, so the baseline is the first reading
    *inside* the window and the delta covers less than was asked for."""

    unsupported: list[CounterName] = Field(default_factory=list)
    """Counters whose zero is fabricated by the tool."""


class ErrorDeltas(BaseModel):
    fabric: str
    window: CounterWindow
    coverage: CounterCoverage
    counters: list[CounterName]
    """Every column this response speaks, sent once rather than per port -- it
    is also what puts `CounterName` in the generated schema as a real union."""

    ports: list[PortErrorDelta]


# ---- traffic -------------------------------------------------------------
#
# A rate, not a delta, which is why it does not reuse the models above.
# `ErrorDeltas` answers "what accumulated between these two instants", a number
# the window defines; traffic answers "how fast is this going", which the window
# only samples.


class TrafficCoverage(BaseModel):
    """How much of the fabric the rate window saw.

    Deliberately thinner than `CounterCoverage`: there is no `unsupported`,
    because PortXmitData and PortRcvData are mandatory in IBTA and no port
    fabricates them, and no `sweeps`, because a rate is bounded by two readings
    however many sat between them.
    """

    ports_expected: int
    ports_measured: int
    ports_no_data: int
    ports_reset: int


class PortTraffic(BaseModel):
    """One port's rate over the window.

    Gbps on the wire rather than raw octets: the conversion needs `xmit_data`'s
    x4 scaling, the span, and the knowledge that these are wire bytes.

    Sparse on a floor rather than on a flag, because traffic is genuinely dense
    where errors are not. An omitted port is below `IDLE_FLOOR_GBPS` and reads
    as idle either way.
    """

    node: str
    port: int
    tx: float = 0.0
    """Transmit rate, Gbps."""
    rx: float = 0.0
    """Receive rate, Gbps."""
    txp: float = 0.0
    """Transmit packet rate, packets/s."""
    rxp: float = 0.0
    """Receive packet rate, packets/s."""

    no_data: bool = False
    """Fewer than two readings bounded the window. Not idle -- unpolled."""

    reset: bool = False
    """A byte counter went backwards, so no rate can be derived from it."""


class TrafficRates(BaseModel):
    fabric: str
    as_of: datetime
    """The newest reading that bounded the window."""
    first_at: datetime | None = None
    """The baseline reading. `as_of - first_at` is `span_s`."""
    span_s: float | None = None
    requested_window_s: float
    """What the window was, after the server sized it. `span_s` may exceed it --
    the baseline is the last reading at or before the window opens."""
    cadence_s: float | None = None
    """Observed spacing between recent traffic sweeps, which is what the default
    window is sized from. The collector's traffic interval is configurable, so 
    this cannot be assumed by either side."""
    coverage: TrafficCoverage
    ports: list[PortTraffic]


class FabricRow(BaseModel):
    fabric_id: int
    name: str
    subnet_prefix: str | None = None
    description: str | None = None
    created_at: datetime
