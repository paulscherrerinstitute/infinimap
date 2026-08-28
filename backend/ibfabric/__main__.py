"""
Parses the snapshot and prints a short summary of the fabric graph.

usage: python -m ibfabric <ibdiagnet2.db_csv>
"""

from __future__ import annotations

import sys

from .model import NodeType, guid_str
from .parse import build_graph


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print("usage: python -m ibfabric <ibdiagnet2.db_csv>", file=sys.stderr)
        return 2

    g = build_graph(argv[1])
    c = g.counts()

    print(f"snapshot: {g.run_timestamp}   ({g.source_path})")
    print(f"ibdiagnet: {g.versions.get('ibdiagnet', '?')}")
    print()
    print(f"nodes: {c['nodes']}  (switches {c['switches']}, CAs {c['cas']})")
    print(f"links: {c['links']}")
    for health in ("ok", "degraded", "down", "unknown"):
        key = f"links_{health}"
        if key in c:
            print(f"    {health:9} {c[key]}")
    print(f"check events: {c['check_events']}")

    sm = ", ".join(f"{s.role} {guid_str(s.guid)} (prio {s.priority})" for s in g.sm)
    if sm:
        print(f"subnet manager: {sm}")

    unhealthy = g.unhealthy_links()
    if unhealthy:
        print(f"\ntop unhealthy links ({len(unhealthy)} total):")
        for link in unhealthy[:10]:
            na = g.nodes.get(link.a.node_guid)
            nb = g.nodes.get(link.b.node_guid)
            print(f"    [{link.health.value:8}] "
                  f"{_short(na)}/{link.a.port_num} <-> {_short(nb)}/{link.b.port_num}")
    return 0


def _short(node) -> str:
    if node is None:
        return "?"
    return node.desc or guid_str(node.guid)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
