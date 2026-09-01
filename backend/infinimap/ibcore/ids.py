"""The canonical wire id for a link.

A link is an unordered pair of ports, so its id must be canonicalised or the
same cable acquires a different identity depending on which end a scan walked
first. The two halves of this system canonicalise *differently*: the database
orders by port_id, which is insertion order, while the wire orders by
(node_guid, port_number), which is a fact about the hardware.

They will disagree, and that is fine so long as exactly one function formats an
id for the wire and the raw (port_a_id, port_b_id) pair never crosses the
boundary. This is that function.

Wire ids are GUID-based rather than port_id-based so they survive a database
rebuild, stay identical across deployments, and can be produced by the offline
db_csv exporter, which has no port_ids at all.
"""

from __future__ import annotations

from .guid import guid_str, parse_guid, unsigned

# (node_guid, port_number). The GUID may arrive in either signedness; _norm
# settles it before anything compares two of them.
Endpoint = tuple[int, int]


def edge_id(a: Endpoint, b: Endpoint) -> str:
    """Two endpoints -> "{guidLo}:{portLo}-{guidHi}:{portHi}", order-independent."""
    lo, hi = sorted((_norm(a), _norm(b)))
    return f"{guid_str(lo[0])}:{lo[1]}-{guid_str(hi[0])}:{hi[1]}"


def parse_edge_id(s: str) -> tuple[Endpoint, Endpoint]:
    """The inverse, for routes that take a link id as a path parameter.

    Returns endpoints with *signed* GUIDs, ready to query with. Raises
    ValueError on anything malformed so a bad path becomes a 4xx.
    """
    halves = s.split("-")
    if len(halves) != 2:
        raise ValueError(f"not a link id: {s!r}")
    return _parse_half(halves[0]), _parse_half(halves[1])


def _parse_half(h: str) -> Endpoint:
    guid, _, port = h.partition(":")
    if not port:
        raise ValueError(f"link id half missing its port number: {h!r}")
    try:
        port_num = int(port)
    except ValueError:
        raise ValueError(f"link id half has a non-numeric port: {h!r}") from None
    return (parse_guid(guid), port_num)


def _norm(e: Endpoint) -> Endpoint:
    """Unsigned GUID, so ordering does not depend on which side of the database
    boundary the value came from. A signed BIGINT read straight out of Postgres
    sorts negative, which would flip the pair and mint a different id for the
    same cable."""
    return (unsigned(e[0]), e[1])  # type: ignore[return-value]
