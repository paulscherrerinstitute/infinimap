"""Fold a parsed ibqueryerrors dump into one CounterObservation.

This stage owns the shape of the fact row: it turns a sparse map of counter
names into a dense tuple of column values. Absence becomes zero here, once, in
the two places it means zero:

  * a counter missing from a named port's line reads zero, because plain
    ibqueryerrors prints only nonzero fields.

  * a port in the universe that the dump never named read zero on all fourteen
    counters.

The universe comes from the topology Observation of the same cycle, which is the
strongest single reason the two halves run together.
"""

from __future__ import annotations

from ...counters import (COUNTERS, IGNORED_COUNTERS, SOURCE_ERRORS,
                         SOURCE_TRAFFIC, CounterObservation, CounterRow,
                         PortKey)
from .parse import parse_counters


def build_error_observation(
    text: str,
    universe: set[PortKey],
    collected_at: str,
    started_at: str,
    finished_at: str,
    *,
    already_incomplete: bool = False,
    origin: str = "live",
) -> CounterObservation:
    """Assemble the dense error observation from one plain ibqueryerrors dump.

    ``universe`` is every port the fabric is known to have. Ports the dump did
    not name get an all-zero row; see the module docstring.
    """
    return _build(text, SOURCE_ERRORS, universe, collected_at, started_at,
                  finished_at, already_incomplete=already_incomplete,
                  origin=origin)


def build_traffic_observation(
    text: str,
    collected_at: str,
    started_at: str,
    finished_at: str,
    *,
    already_incomplete: bool = False,
    origin: str = "live",
) -> CounterObservation:
    """Assemble the traffic observation from one `--counters` dump.

    No universe: --counters reports every port it checks and prints zero-valued
    fields rather than omitting them, so the dump is already dense and a port
    absent from it genuinely had no reading.
    """
    return _build(text, SOURCE_TRAFFIC, None, collected_at, started_at,
                  finished_at, already_incomplete=already_incomplete,
                  origin=origin)


def _build(text: str, source: str, universe: set[PortKey] | None,
           collected_at: str, started_at: str, finished_at: str,
           *, already_incomplete: bool, origin: str) -> CounterObservation:
    counters = COUNTERS[source]
    columns = tuple(counters.values())
    order = list(counters)          # IBTA names, in column order

    obs = CounterObservation(
        source=source,
        columns=columns,
        collected_at=collected_at,
        started_at=started_at,
        finished_at=finished_at,
        origin=origin,
    )
    if already_incomplete:
        obs.mark_incomplete("ibqueryerrors output was empty or truncated")

    dump = parse_counters(text)

    for port in dump.ports:
        key = (port.node_guid, port.port_number)
        obs.ports_reported.add(key)
        for name in port.counters:
            if name not in counters and name not in IGNORED_COUNTERS:
                obs.unknown_counters[name] = obs.unknown_counters.get(name, 0) + 1
        # Absent means zero.
        obs.rows.append(CounterRow(
            port=key,
            values=tuple(port.counters.get(name, 0) for name in order),
        ))

    if universe is not None:
        zeros = tuple(0 for _ in order)
        for key in universe - obs.ports_reported:
            obs.rows.append(CounterRow(port=key, values=zeros))
            obs.ports_zero_filled += 1

    obs.nodes_checked = dump.nodes_checked
    obs.ports_checked = dump.ports_checked
    obs.aggregates_dropped = len(dump.aggregates)

    if dump.ports_checked is None:
        obs.mark_incomplete("no '## Summary: ... ports checked' line")
    if dump.nodes_checked is None:
        obs.mark_incomplete("no '## Summary: ... nodes checked' line")

    if dump.unparsed:
        obs.mark_incomplete(
            f"{len(dump.unparsed)} unparsed line(s), first: {dump.unparsed[0][:80]!r}"
        )
    if dump.duplicates:
        obs.mark_incomplete(
            f"{len(dump.duplicates)} port(s) reported more than once, "
            f"first: 0x{dump.duplicates[0][0]:016x} port {dump.duplicates[0][1]}"
        )
    if obs.unknown_counters:
        obs.mark_incomplete(
            f"counter name(s) with no column: {sorted(obs.unknown_counters)[:4]}"
        )

    # A sanity check the tool hands us for free. On the error path it reports
    # more ports than it names, always, because it names only the interesting
    # ones; on the traffic path the two should agree exactly.
    named = len(obs.ports_reported)
    if dump.ports_checked is not None:
        if named > dump.ports_checked:
            obs.mark_incomplete(
                f"{named} ports named but only {dump.ports_checked} reported checked"
            )
        elif source == SOURCE_TRAFFIC and named < dump.ports_checked:
            # Not fatal -- the sweep is still written -- but --counters is
            # supposed to print every port it checked, so a shortfall means
            # rows are missing rather than zero.
            obs.mark_incomplete(
                f"--counters named {named} of {dump.ports_checked} ports checked"
            )

    return obs
