"""The source-neutral counter observation the counter writer consumes.

The parallel to observation.py for the other half of the schema: these
dataclasses mirror the counter tables the way ObservedNode and ObservedPort
mirror identity and state.

No delta, no rate, no verdict. The schema stores the raw cumulative reading and
every subtraction happens on read -- an observation is exactly what the tool
said, plus provenance.

Both modes of ibqueryerrors produce one row per port carrying every column of
its table. They differ only in which columns, and in what absence means:

  errors   plain ibqueryerrors. Prints only NONZERO fields, and names only the
           ports that have one.

  traffic  ibqueryerrors --counters. Prints every field including zeros and
           reports every port it checks, so absence means no reading and no
           universe is needed.
"""

from __future__ import annotations

from dataclasses import dataclass, field

# (node_guid, port_number) -- the same key observation.py uses, so the writer's
# port cache resolves both halves of a cycle through one map.
PortKey = tuple[int, int]

SOURCE_ERRORS = "errors"
SOURCE_TRAFFIC = "traffic"


# ---------------------------------------------------------------------------
# Column maps
#
# IBTA field name exactly as ibqueryerrors prints it between brackets -> the
# column that holds it. The ONLY place this mapping exists: the writer builds
# its INSERT from these and the parser never sees a column name.
#
# Order matters and is the column order of the INSERT. Keep it identical to the
# table definition so that reading the two side by side is a straight check.
# ---------------------------------------------------------------------------

ERROR_COUNTERS: dict[str, str] = {
    "SymbolErrorCounter":           "symbol_error",
    "LinkErrorRecoveryCounter":     "link_error_recovery",
    "LinkDownedCounter":            "link_downed",
    "PortRcvErrors":                "rcv_errors",
    "PortRcvRemotePhysicalErrors":  "rcv_remote_phys_errors",
    "PortRcvSwitchRelayErrors":     "rcv_switch_relay_errors",
    "PortXmitDiscards":             "xmit_discards",
    "PortXmitConstraintErrors":     "xmit_constraint_errors",
    "PortRcvConstraintErrors":      "rcv_constraint_errors",
    "LocalLinkIntegrityErrors":     "local_link_integrity",
    "ExcessiveBufferOverrunErrors": "excessive_buffer_overrun",
    "VL15Dropped":                  "vl15_dropped",
    "PortXmitWait":                 "xmit_wait",
    "QP1Dropped":                   "qp1_dropped",
}

TRAFFIC_COUNTERS: dict[str, str] = {
    "PortXmitData": "xmit_data",
    "PortRcvData":  "rcv_data",
    "PortXmitPkts": "xmit_pkts",
    "PortRcvPkts":  "rcv_pkts",
}

# --counters also prints PortUnicastXmitPkts, PortUnicastRcvPkts,
# PortMulticastXmitPkts and PortMulticastRcvPkts. They are deliberately not stored.
IGNORED_COUNTERS: frozenset[str] = frozenset({
    "PortUnicastXmitPkts", "PortUnicastRcvPkts",
    "PortMulticastXmitPkts", "PortMulticastRcvPkts",
    "CounterSelect", "CounterSelect2", "PortSelect",
})

TABLE = {SOURCE_ERRORS: "port_errors", SOURCE_TRAFFIC: "port_traffic"}
COUNTERS = {SOURCE_ERRORS: ERROR_COUNTERS, SOURCE_TRAFFIC: TRAFFIC_COUNTERS}


@dataclass(frozen=True, slots=True)
class CounterRow:
    """One port's complete reading: every column of its table, in order."""

    port: PortKey
    values: tuple[int, ...]


@dataclass
class CounterObservation:
    """Everything one ibqueryerrors run reported, ready for the writer."""

    source: str                # SOURCE_ERRORS | SOURCE_TRAFFIC
    columns: tuple[str, ...]   # the table columns `values` lines up with
    collected_at: str          # ISO-8601, one instant for the whole sweep
    started_at: str
    finished_at: str

    rows: list[CounterRow] = field(default_factory=list)

    # The tool's own coverage figures, read from its "## Summary: N nodes
    # checked, M ports checked" line rather than tallied from the lines above.
    nodes_checked: int | None = None
    ports_checked: int | None = None

    # Ports the dump actually named. On the error path this is a strict subset
    # of `rows`, because rows also covers the universe ports that were silent.
    ports_reported: set[PortKey] = field(default_factory=set)

    # Ports named by the dump that were zero-filled into rows from the universe.
    ports_zero_filled: int = 0

    # "port ALL" lines -- the AllPortSelect per-switch aggregate. Counted and
    # dropped: they are sums across a switch, not readings of any port.
    aggregates_dropped: int = 0

    # Counter names with no column. Not the same as IGNORED_COUNTERS: this is
    # the set that would mean the tool's output has moved under the parser.
    unknown_counters: dict[str, int] = field(default_factory=dict)

    origin: str = "live"       # 'live' | 'from_file'
    duration_ms: int | None = None

    # True only if the dump was complete and wholly understood. False does not
    # void the sweep.
    complete: bool = True
    problems: list[str] = field(default_factory=list)

    def mark_incomplete(self, reason: str) -> None:
        self.complete = False
        self.problems.append(reason)

    @property
    def table(self) -> str:
        return TABLE[self.source]

    def counts(self) -> tuple[int, int]:
        """(rows, ports the dump named) -- for the cycle log line."""
        return (len(self.rows), len(self.ports_reported))
