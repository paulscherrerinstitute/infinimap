"""Parse ibqueryerrors text into raw per-port counter readings.

Three traps, all of which occur in ordinary output:

  Two header forms. Most nodes print `Errors for "name"`, switches print
  `Errors for 0xGUID "name"`.

  `port ALL` is not a port number. It is the AllPortSelect aggregate, a sum
  across the whole switch: `int()` raises on it, and a bare word pattern
  invents a port.

  NodeDescription is not unique. Identity comes from the GUID on the port line,
  never from the header name -- so the header is read only for its GUID, when it
  has one, and otherwise ignored.

Every value also carries a human-readable form in parentheses --
`[PortXmitWait == 127475528 (121.570M)]` -- which is discarded. It is the same
number rounded, and in other modes additionally scaled (x4, binary units).
"""

from __future__ import annotations

import re

# `GUID 0x<hex> port <n|ALL>: <rest>`. The port group is deliberately \w+ rather
# than \d+ so that ALL matches here and is rejected by the caller with a count,
# instead of failing the line match and vanishing into `unparsed`.
_PORT_LINE = re.compile(
    r"^\s*GUID\s+0x([0-9a-fA-F]+)\s+port\s+(\w+):\s*(.*)$"
)

# `[Name == 12345]` or `[Name == 12345 (12.345K)]`. Only the integer is taken.
_COUNTER = re.compile(r"\[([A-Za-z0-9_]+)\s*==\s*(-?\d+)")

# Either header form, in either mode. The GUID group is optional and unused --
# the port lines carry their own GUID -- but matching the line is what lets a
# header be distinguished from a malformed port line.
#
# `--counters` prints "Data Counters for 0xGUID "name"" above the SAME
# `GUID 0x... port N: [Name == value]` grammar the plain mode uses, so the port
# parser below is shared verbatim between the two. Only the lead-in differs.
_HEADER = re.compile(
    r'^(?:Errors|Data Counters) for (?:0x([0-9a-fA-F]+)\s+)?"(.*)"\s*$')

# `-r` appends a topology line under each port. This is noise here.
_LINK_INFO = re.compile(r"^\s*Link info:")

# `## Summary: X nodes checked, Y bad nodes found`
_SUMMARY_NODES = re.compile(r"##\s*Summary:\s*(\d+)\s+nodes checked")
# `##          X ports checked, Y ports have errors beyond threshold`
_SUMMARY_PORTS = re.compile(r"##\s*(\d+)\s+ports checked")

# The aggregate port selector, as the tool spells it.
ALL_PORTS = "ALL"


class ParsedPort:
    """One `GUID ... port N:` line: a GUID, a port number, and its counters."""

    __slots__ = ("node_guid", "port_number", "counters")

    def __init__(self, node_guid: int, port_number: int,
                 counters: dict[str, int]):
        self.node_guid = node_guid
        self.port_number = port_number
        self.counters = counters

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (f"ParsedPort(0x{self.node_guid:016x}, {self.port_number}, "
                f"{self.counters})")


class ParsedDump:
    """Everything one dump said, before any of it is interpreted."""

    __slots__ = ("ports", "aggregates", "nodes_checked", "ports_checked",
                 "unparsed", "duplicates")

    def __init__(self) -> None:
        self.ports: list[ParsedPort] = []
        # `port ALL` lines, kept as (node_guid, counters) so a caller that ever
        # wants per-switch aggregates has them, and counted so that dropping
        # them is visible rather than silent.
        self.aggregates: list[tuple[int, dict[str, int]]] = []
        self.nodes_checked: int | None = None
        self.ports_checked: int | None = None
        # Lines that looked like content and matched nothing. Empty on every
        # fixture sweep; a nonempty list means the tool's output changed.
        self.unparsed: list[str] = []
        # (guid, port) seen more than once. Never happens in the fixture; if it
        # did, one of the two readings would be silently overwriting the other.
        self.duplicates: list[tuple[int, int]] = []


def parse_counters(text: str) -> ParsedDump:
    """Split an ibqueryerrors dump into per-port counter maps."""
    dump = ParsedDump()
    seen: set[tuple[int, int]] = set()

    for raw in text.splitlines():
        line = raw.rstrip()
        if not line.strip():
            continue

        if line.startswith("##"):
            _read_summary(line, dump)
            continue

        m = _PORT_LINE.match(line)
        if m is not None:
            _read_port(m, dump, seen)
            continue

        if _HEADER.match(line) is not None:
            continue  # nothing in a header is needed; see the module docstring

        if _LINK_INFO.match(line) is not None:
            continue  # `-r` topology noise

        dump.unparsed.append(line.strip())

    return dump


def _read_summary(line: str, dump: ParsedDump) -> None:
    """Pull the tool's own coverage figures off a `##` line."""
    m = _SUMMARY_NODES.search(line)
    if m is not None:
        dump.nodes_checked = int(m.group(1))
    m = _SUMMARY_PORTS.search(line)
    if m is not None:
        dump.ports_checked = int(m.group(1))


def _read_port(m: re.Match[str], dump: ParsedDump,
               seen: set[tuple[int, int]]) -> None:
    guid = int(m.group(1), 16)
    port_field = m.group(2)
    counters = {name: int(value) for name, value in _COUNTER.findall(m.group(3))}

    if port_field == ALL_PORTS:
        dump.aggregates.append((guid, counters))
        return

    try:
        port_number = int(port_field)
    except ValueError:
        # A port selector that is neither a number nor ALL. Has never appeared;
        # recorded rather than dropped so that it would be noticed.
        dump.unparsed.append(m.string.strip())
        return

    key = (guid, port_number)
    if key in seen:
        dump.duplicates.append(key)
        return
    seen.add(key)
    dump.ports.append(ParsedPort(guid, port_number, counters))
