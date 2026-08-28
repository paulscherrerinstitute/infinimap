"""Derive link and node health from port state.

`002_state.sql` stores the enabled and supported masks alongside the active
values precisely so that degradation is detectable -- "store facts, derive
verdicts" -- and this module is the verdict half.

Three rules, in order:

  * state missing at either end is **unknown**, not healthy.
  * either end not Active is **down**.
  * running below what both ends could have negotiated is **degraded**.

The degraded rule belongs to the LINK, not to either port: the ceiling is what
the two ends *mutually* enable, so one side cannot compute it alone -- a 4X port
cabled to a 1X port is running at the only width available to it and is healthy.

Counter-derived degradation is deliberately absent. Error counters are a
separate channel with their own window and their own overlay.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Literal

from . import decode

# The verdict set, as a type. It lives here rather than in the API models
# because this module is what produces the values -- a second definition
# elsewhere could disagree with these constants without anything noticing.
Health = Literal["ok", "degraded", "down", "unknown"]

OK: Health = "ok"
DEGRADED: Health = "degraded"
DOWN: Health = "down"
UNKNOWN: Health = "unknown"

# Worst-wins ordering for the per-node rollup. Note DOWN outranks DEGRADED and
# UNKNOWN sits just above OK: an unobserved link should not mask a real fault
# elsewhere on the same node, but it should still be visible.
SEVERITY: dict[Health, int] = {OK: 0, UNKNOWN: 1, DEGRADED: 2, DOWN: 3}


@dataclass(frozen=True, slots=True)
class PortView:
    """Exactly the port_state columns the health rules read"""

    log_state: str | None
    active_speed: int | None
    active_width: int | None
    enabled_speed: int | None
    enabled_width: int | None
    supported_speed: int | None
    supported_width: int | None


def link_health(a: PortView | None, b: PortView | None) -> Health:
    """Health of the link joining two ports, from both ends' state."""
    if a is None or b is None:
        return UNKNOWN
    if a.log_state != "active" or b.log_state != "active":
        return DOWN
    if _degraded(a, b) or _degraded(b, a):
        return DEGRADED
    return OK


def node_health(link_healths: Iterable[Health]) -> Health:
    """Worst health among the links incident to a node.

    A node with no links reports ok, matching the offline exporter. Arguably a
    disconnected HCA is not healthy, but changing that verdict would repaint
    every unpatched port on the map and is a product decision, not a data one.
    """
    worst: Health = OK
    for h in link_healths:
        if SEVERITY.get(h, 0) > SEVERITY[worst]:
            worst = h
    return worst


def reasons(a: PortView | None, b: PortView | None) -> list[str]:
    """Why a link is not ok, in words a panel can show.

    This replaces the db_csv exporter's `check_events`, which carried
    ibdiagnet's own speed and width verdicts. We recompute the same judgement
    from the stored masks, so the explanation and the verdict cannot disagree --
    they come from one comparison.
    """
    if a is None or b is None:
        return ["no port state reported at one or both ends"]

    out: list[str] = []
    for side, p in (("A", a), ("B", b)):
        if p.log_state != "active":
            out.append(f"end {side} is {p.log_state or 'not reporting'}, not active")
    if out:
        return out

    for side, p, peer in (("A", a, b), ("B", b, a)):
        got = decode.best_speed(p.active_speed)
        want = decode.best_speed(_mutual(p.enabled_speed, peer.enabled_speed,
                                         p.supported_speed, peer.supported_speed))
        if got and want and decode.speed_rank(got) < decode.speed_rank(want):
            out.append(f"end {side} negotiated {got} but both ends support {want}")

        got = decode.best_width(p.active_width)
        want = decode.best_width(_mutual(p.enabled_width, peer.enabled_width,
                                         p.supported_width, peer.supported_width))
        if got and want and decode.width_rank(got) < decode.width_rank(want):
            out.append(f"end {side} negotiated {got} but both ends support {want}")
    return out


# ---- degradation ---------------------------------------------------------

def _degraded(p: PortView, peer: PortView) -> bool:
    """True when p runs below the best speed or width both ends allow."""
    speed_ceiling = _mutual(p.enabled_speed, peer.enabled_speed,
                            p.supported_speed, peer.supported_speed)
    if _below(p.active_speed, speed_ceiling, decode.best_speed, decode.speed_rank):
        return True

    width_ceiling = _mutual(p.enabled_width, peer.enabled_width,
                            p.supported_width, peer.supported_width)
    return _below(p.active_width, width_ceiling, decode.best_width, decode.width_rank)


def _mutual(a_enabled: int | None, b_enabled: int | None,
            a_supported: int | None, b_supported: int | None) -> int:
    """What both ends can do: their enabled masks intersected.

    Falls back to the supported masks where the intersection is empty -- some
    firmware leaves the enabled fields zero -- and returns 0 when neither pair
    is usable, which _below reads as "no opinion".
    """
    mutual = (a_enabled or 0) & (b_enabled or 0)
    if mutual:
        return mutual
    return (a_supported or 0) & (b_supported or 0)


def _below(active_mask: int | None, ceiling_mask: int,
           best, rank) -> bool:
    """True when the negotiated value ranks below the ceiling both ends allow.

    False whenever either side is unreadable. An unknown ceiling is not evidence
    of degradation, and guessing paints healthy links red -- which is the
    failure mode that gets a monitoring tool ignored.
    """
    if not active_mask or not ceiling_mask:
        return False
    got, want = best(active_mask), best(ceiling_mask)
    if got is None or want is None:
        return False
    return rank(got) < rank(want)
