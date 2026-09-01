"""What changed between two instants."""

from __future__ import annotations

from datetime import datetime

from ..db import Database
from ..models import Change, Diff, DiffLink, DiffNode, LinkElement, NodeElement
from . import events as events_q
from . import topology as topology_q

ADDED: Change = "added"
REMOVED: Change = "removed"
MODIFIED: Change = "modified"
UNCHANGED: Change = "unchanged"


def fetch(db: Database, fabric_id: int, fabric: str, since, until) -> Diff:
    """`since` and `until` are Resolved - the sweeps, not the requested times."""
    lo, hi = since.collected_at, until.collected_at

    # If no complete sweep in the window recorded a change, the net diff is
    # provably empty and the second topology fetch is wasted work.
    quiet = not db.changed_between(fabric_id, lo, hi)

    a_nodes, a_links, a_groups, _ = topology_q.fetch(db, fabric_id, lo)
    if quiet:
        b_nodes, b_links, b_groups = a_nodes, a_links, a_groups
    else:
        b_nodes, b_links, b_groups, _ = topology_q.fetch(db, fabric_id, hi)

    node_churn, port_churn = events_q.churn(db, fabric_id, lo, hi)

    return Diff(
        fabric=fabric,
        since=since,
        until=until,
        unchanged_guaranteed=quiet,
        nodes=_diff_nodes(a_nodes, b_nodes, node_churn),
        links=_diff_links(a_links, b_links, port_churn),
        system_groups={**a_groups, **b_groups},
    )


# ---- comparison ----------------------------------------------------------

def _diff_nodes(before: list[NodeElement], after: list[NodeElement],
                churn: dict[str, int]) -> list[DiffNode]:
    a = {n.id: n for n in before}
    b = {n.id: n for n in after}
    out: list[DiffNode] = []
    for nid in sorted(a.keys() | b.keys()):
        old, new = a.get(nid), b.get(nid)
        change = _classify(old, new)
        out.append(DiffNode(
            id=nid,
            change=change,
            churn=churn.get(nid, 0),
            before=old if change in (REMOVED, MODIFIED) else None,
            after=new,
        ))
    return out


def _diff_links(before: list[LinkElement], after: list[LinkElement],
                churn: dict[tuple[int, int], int]) -> list[DiffLink]:
    a = {l.id: l for l in before}
    b = {l.id: l for l in after}
    out: list[DiffLink] = []
    for lid in sorted(a.keys() | b.keys()):
        old, new = a.get(lid), b.get(lid)
        change = _classify(old, new)
        out.append(DiffLink(
            id=lid,
            change=change,
            churn=_link_churn(old or new, churn),
            before=old if change in (REMOVED, MODIFIED) else None,
            after=new,
        ))
    return out


def _classify(old, new) -> Change:
    if old is None:
        return ADDED
    if new is None:
        return REMOVED
    return UNCHANGED if old == new else MODIFIED


def _link_churn(link: LinkElement | None, churn: dict[tuple[int, int], int]) -> int:
    """Events touching either end of this link."""
    if link is None:
        return 0
    a = churn.get((_guid_int(link.source), link.source_port), 0)
    b = churn.get((_guid_int(link.target), link.target_port), 0)
    return a + b


def _guid_int(guid_hex: str) -> int:
    """Wire hex back to the unsigned int the churn map is keyed by."""
    return int(guid_hex, 16)
