"""Error deltas over an arbitrary window.

The question is always "what accumulated between these two instants", never
"what does this counter read": the schema stores raw cumulative values so that
every subtraction happens here, where it can be corrected without a backfill.

Three statements, because the baseline is asof and so reads a different range
from the other two -- it looks back from before the window opens:

    _BASELINE   the reading at or before `since`    DISTINCT ON, one row/port
    _LATEST     the last reading inside the window  DISTINCT ON, one row/port
    _EXTREMA    min/max per counter, plus coverage  GROUP BY, one row/port

The baseline is asof, not the first reading inside. A window shorter than the
collection interval holds one reading, and one reading is an endpoint rather
than an interval -- so `last - first_inside` yields nothing at all for the
shortest window anybody will ask for. The lookback is bounded, so a port that
stopped answering a week ago comes back as `no_data` instead of triggering a
week-long backwards scan.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, NamedTuple, get_args

from ibcore.guid import guid_str

from ..db import Database, sql
from ..models import (
    CounterCoverage, CounterName, CounterWindow, ErrorDeltas, PortErrorDelta,
    PortTraffic, TrafficCoverage, TrafficRates,
)

COLUMNS: tuple[CounterName, ...] = get_args(CounterName)

# A database row. `dict_row` hands back str-keyed rows of mixed type.
Row = dict[str, Any]

# How far back to look for the asof baseline. Three times the default 300 s 
# error cadence, so a single missed sweep still finds one and two do not.
DEFAULT_BASELINE_LOOKBACK = timedelta(seconds=900)

# Counters whose stored zero is fabricated when the port cannot report them,
# with the port_pm_caps column that says so.
_CAP_GATED: dict[CounterName, str] = {
    "xmit_wait": "xmit_wait_sup",
    "qp1_dropped": "qp1_drop_sup",
}

_COLS = ", ".join(f"s.{c}" for c in COLUMNS)
_EXTREMA_COLS = ", ".join(
    f"min(s.{c}) AS {c}_min, max(s.{c}) AS {c}_max" for c in COLUMNS
)

_TRAFFIC_COLS = ("xmit_data", "rcv_data", "xmit_pkts", "rcv_pkts")
_T_COLS = ", ".join(f"s.{c}" for c in _TRAFFIC_COLS)


# ---- the asof reads --------------------------------------------------------

_ASOF = """
    SELECT DISTINCT ON (s.port_id) s.port_id, s.time, {cols}
    FROM {table} s
    {scope}
      AND {window}
    ORDER BY s.port_id, s.time DESC
"""

_IN_FABRIC = """JOIN port p ON p.port_id = s.port_id
    WHERE p.fabric_id = %(fabric_id)s"""
_THESE_PORTS = "WHERE s.port_id = ANY(%(port_ids)s)"

# The four boundaries.
_BEFORE_SINCE = "s.time <= %(since)s AND s.time > %(floor)s"
_IN_WINDOW = "s.time > %(since)s AND s.time <= %(until)s"
_BEFORE_UNTIL = "s.time <= %(until)s AND s.time > %(floor)s"
_UP_TO_UNTIL = "s.time <= %(until)s"


def _asof(table, cols, scope, window):
    """One asof read, finished. `cols` is baked in, so callers execute these
    directly rather than formatting them a second time."""
    return sql(_ASOF, table=table, cols=cols, scope=scope, window=window)

#                      table           columns  scope         boundary
_BASELINE      = _asof("port_errors",  _COLS,   _IN_FABRIC,   _BEFORE_SINCE)
_LATEST        = _asof("port_errors",  _COLS,   _IN_FABRIC,   _IN_WINDOW)
_T_BASELINE    = _asof("port_traffic", _T_COLS, _IN_FABRIC,   _BEFORE_SINCE)
_T_LATEST      = _asof("port_traffic", _T_COLS, _IN_FABRIC,   _BEFORE_UNTIL)
_PORT_BASE     = _asof("port_errors",  _COLS,   _THESE_PORTS, _BEFORE_SINCE)
_PORT_LATEST   = _asof("port_errors",  _COLS,   _THESE_PORTS, _UP_TO_UNTIL)
_PORT_T_BASE   = _asof("port_traffic", _T_COLS, _THESE_PORTS, _BEFORE_SINCE)
_PORT_T_LATEST = _asof("port_traffic", _T_COLS, _THESE_PORTS, _BEFORE_UNTIL)


# Errors only, and not an asof read: min/max across the window, which is what
# makes the reset check possible. Same range as `_IN_WINDOW`, deliberately --
# `assemble` compares its sample count against `_LATEST`, so they must agree.
_EXTREMA = sql("""
    SELECT s.port_id, count(*) AS samples,
           min(s.time) AS first_at, max(s.time) AS last_at,
           {extrema}
    FROM port_errors s
    JOIN port p ON p.port_id = s.port_id
    WHERE p.fabric_id = %(fabric_id)s
      AND s.time > %(since)s AND s.time <= %(until)s
    GROUP BY s.port_id
""", extrema=_EXTREMA_COLS)

# The port universe and its capabilities in one. LEFT JOIN and selected from
# `port`, so a fabric with no caps rows still enumerates every port -- this is
# what `ports_expected` counts, and a port absent from the fact table has to be
# known here or its silence is invisible.
_CAPS = """
    SELECT p.port_id,
           COALESCE(c.xmit_wait_sup, true)  AS xmit_wait_sup,
           COALESCE(c.qp1_drop_sup,  false) AS qp1_drop_sup
    FROM port p
    LEFT JOIN port_pm_caps c ON c.port_id = p.port_id
    WHERE p.fabric_id = %(fabric_id)s
"""


def error_deltas(db: Database, fabric_id: int, fabric: str,
                 since: datetime, until: datetime,
                 lookback: timedelta = DEFAULT_BASELINE_LOOKBACK) -> ErrorDeltas:
    """Per-port error accumulation over (since, until]."""
    params = {
        "fabric_id": fabric_id,
        "since": since,
        "until": until,
        "floor": since - lookback,
    }

    with db.pool.connection() as conn:
        caps = {r["port_id"]: r for r in conn.execute(_CAPS, params).fetchall()}
        baseline = {r["port_id"]: r for r in
                    conn.execute(_BASELINE, params).fetchall()}
        latest = {r["port_id"]: r for r in
                  conn.execute(_LATEST, params).fetchall()}
        extrema = {r["port_id"]: r for r in
                   conn.execute(_EXTREMA, params).fetchall()}

    return assemble(fabric, since, until, caps=caps, baseline=baseline,
                    latest=latest, extrema=extrema,
                    ports=_identity_for(db, fabric_id, caps.keys()))


def assemble(fabric: str, since: datetime, until: datetime, *,
             caps: dict[int, Row], baseline: dict[int, Row],
             latest: dict[int, Row], extrema: dict[int, Row],
             ports: dict[int, tuple[int, int]]) -> ErrorDeltas:
    """The four result sets to the wire model. No database."""
    
    # Which counters each port cannot report, resolved up front because the
    # per-port answer depends on the fabric-wide one.
    gated_by_port: dict[int, list[CounterName]] = {
        port_id: [c for c, col in _CAP_GATED.items() if not cap[col]]
        for port_id, cap in caps.items()
    }
    unsupported_counts: dict[CounterName, int] = {}
    for g in gated_by_port.values():
        for c in g:
            unsupported_counts[c] = unsupported_counts.get(c, 0) + 1

    everywhere = {c for c, n in unsupported_counts.items() if n == len(caps)}

    rows: list[PortErrorDelta] = []
    silent: list[PortErrorDelta] = []
    measured = no_data = n_reset = n_partial = sweeps = 0
    first_at: datetime | None = None
    last_at: datetime | None = None

    for port_id, cap in caps.items():
        gated = gated_by_port[port_id]

        identity = ports.get(port_id)
        if identity is None:
            continue
        node_guid, port_num = identity
        wire: dict[str, Any] = {
            "node": guid_str(node_guid), "port": port_num,
            "unsupported": [c for c in gated if c not in everywhere],
        }

        base = baseline.get(port_id)
        last = latest.get(port_id)
        ext = extrema.get(port_id)

        # Two ways to have nothing measurable: no reading landed in the window
        # at all, or exactly one did and there is no baseline behind it to
        # subtract from. Both are "unpolled", and neither of them is zero.
        if last is None or ext is None or (base is None and ext["samples"] < 2):
            no_data += 1
            silent.append(PortErrorDelta(no_data=True, **wire))
            continue

        measured += 1
        sweeps = max(sweeps, ext["samples"])

        started = base["time"] if base is not None else ext["first_at"]
        first_at = started if first_at is None else min(first_at, started)
        last_at = ext["last_at"] if last_at is None else max(last_at, ext["last_at"])

        deltas: dict[CounterName, int] = {}
        reset = False
        for col in COLUMNS:
            if col in gated:
                continue
            lo = base[col] if base is not None else ext[f"{col}_min"]
            hi = last[col]

            # Backwards inside the window, then backwards across its leading
            # edge. For a monotonic series max == last and min == the first
            # reading, so either comparison failing is a counter that was
            # cleared.
            if ext[f"{col}_max"] > hi:
                reset = True
            if base is not None and ext[f"{col}_min"] < base[col]:
                reset = True

            # Floored rather than dropped, matching error_delta_1h: a reset
            # makes the figure a lower bound, and `reset` is the field saying so.
            if (d := hi - lo) > 0:
                deltas[col] = d

        partial = base is None
        if reset:
            n_reset += 1
        if partial:
            n_partial += 1

        # `reset` and `no_data` do earn a row, because those are per-port.
        if deltas or reset:
            rows.append(PortErrorDelta(d=deltas, reset=reset, partial=partial,
                                       **wire))

    # `no_data` is only meaningful relative to ports that did report.
    if measured:
        rows.extend(silent)

    return ErrorDeltas(
        fabric=fabric,
        window=CounterWindow(
            requested_since=since, requested_until=until,
            first_at=first_at, last_at=last_at,
            span_s=(last_at - first_at).total_seconds()
            if first_at and last_at else None,
            resolution="raw",
        ),
        coverage=CounterCoverage(
            ports_expected=len(caps), ports_measured=measured,
            ports_no_data=no_data, ports_reset=n_reset,
            ports_partial=n_partial, sweeps=sweeps,
            unsupported=unsupported_counts,
        ),
        counters=list(COLUMNS),
        ports=rows,
    )


def _identity_for(db: Database, fabric_id: int, port_ids) -> dict[int, tuple[int, int]]:
    """port_id -> (node_guid, port_number), reloading once on a miss."""
    ports, _ = db.identity(fabric_id)
    if set(port_ids) - ports.keys():
        ports, _ = db.refresh_identity(fabric_id)
    return ports


# ---- traffic --------------------------------------------------------------
#
# A rate, not a delta. Everything above answers "what accumulated between these
# two instants"; traffic answers "how fast is this going", so the arithmetic
# ends in a division and the answer is compared against the link's capacity
# rather than against zero.
#
# The cadence is observed, not assumed. The baseline is asof, as on the error 
# path: one reading is an endpoint, not an interval, and taking the last 
# reading at or before the window opens is what makes the failure `no_data`, 
# which is drawable, rather than an empty response, which is not.

# PortXmitData and PortRcvData are octets DIVIDED BY 4. Bytes are x4, bits 
# are x8, Gbps is /1e9 -- folded into one constant so the scaling appears 
# exactly once.
_OCTETS_TO_GBIT = 4 * 8 / 1e9

# Below this a port is omitted and reads as idle. Traffic is genuinely dense --
# unlike errors, most ports carry something -- so this floor is what keeps a
# poll to a few KB rather than ~60 KB.
IDLE_FLOOR_GBPS = 0.01

# Sizing the default window from the observed cadence. Three samples, so one
# missed sweep still leaves a rate, with a floor for a fabric whose cadence
# cannot be established yet.
WINDOW_CADENCES = 3.0
MIN_WINDOW = timedelta(seconds=5)
FALLBACK_CADENCE = timedelta(seconds=5)

# How far back to look for the asof baseline.
BASELINE_LOOKBACK_FACTOR = 4.0
MIN_BASELINE_LOOKBACK = timedelta(seconds=900)

# The spacing between recent traffic sweeps.
_CADENCE = """
    SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY gap) AS cadence
    FROM (
        SELECT EXTRACT(EPOCH FROM started_at
                       - lag(started_at) OVER (ORDER BY started_at)) AS gap
        FROM (
            SELECT started_at FROM counter_sweep
            WHERE fabric_id = %(fabric_id)s AND source = 'traffic'
              AND started_at <= %(until)s
            ORDER BY started_at DESC LIMIT 20
        ) recent
    ) gaps
    WHERE gap IS NOT NULL AND gap > 0
"""

# The port universe, so a port that reported nothing is still counted.
_T_PORTS = "SELECT p.port_id FROM port p WHERE p.fabric_id = %(fabric_id)s"



class Rates(NamedTuple):
    """One port's rate over an interval. Gbps and packets/s."""

    tx: float
    rx: float
    txp: float
    rxp: float
    span_s: float
    at: datetime
    """The newer of the two readings."""


def rate_between(base: Row | None, last: Row | None) -> Rates | None:
    """Two readings to a rate, or None when no rate can be stated."""
    if base is None or last is None:
        return None
    span = (last["time"] - base["time"]).total_seconds()
    
    if span <= 0:
        return None

    d = {c: last[c] - base[c] for c in _TRAFFIC_COLS}
    if any(v < 0 for v in d.values()):
        return None

    return Rates(
        tx=d["xmit_data"] * _OCTETS_TO_GBIT / span,
        rx=d["rcv_data"] * _OCTETS_TO_GBIT / span,
        txp=d["xmit_pkts"] / span,
        rxp=d["rcv_pkts"] / span,
        span_s=span,
        at=last["time"],
    )

def traffic_cadence(db: Database, fabric_id: int, until: datetime) -> timedelta | None:
    """Observed spacing between recent traffic sweeps, or None if unknowable."""
    with db.pool.connection() as conn:
        row = conn.execute(_CADENCE, {"fabric_id": fabric_id,
                                      "until": until}).fetchone()
    cadence = (row or {}).get("cadence")
    return timedelta(seconds=float(cadence)) if cadence else None


def default_window(cadence: timedelta | None) -> timedelta:
    """Three sweeps' worth, so one missed sweep still leaves a rate."""
    return max((cadence or FALLBACK_CADENCE) * WINDOW_CADENCES, MIN_WINDOW)


def traffic_rates(db: Database, fabric_id: int, fabric: str, until: datetime,
                  window: timedelta,
                  cadence: timedelta | None = None) -> TrafficRates:
    """Per-port transmit and receive rates over the trailing `window`."""
    lookback = max(window * BASELINE_LOOKBACK_FACTOR, MIN_BASELINE_LOOKBACK)
    since = until - window
    params = {
        "fabric_id": fabric_id,
        "since": since,
        "until": until,
        "floor": since - lookback,
    }

    with db.pool.connection() as conn:
        port_ids = [r["port_id"] for r in
                    conn.execute(_T_PORTS, params).fetchall()]
        baseline = {r["port_id"]: r for r in
                    conn.execute(_T_BASELINE, params).fetchall()}
        latest = {r["port_id"]: r for r in
                  conn.execute(_T_LATEST, params).fetchall()}

    return assemble_traffic(
        fabric, until, window, cadence, port_ids=port_ids, baseline=baseline,
        latest=latest, ports=_identity_for(db, fabric_id, port_ids),
    )


def assemble_traffic(fabric: str, until: datetime, window: timedelta,
                     cadence: timedelta | None, *, port_ids: list[int],
                     baseline: dict[int, Row], latest: dict[int, Row],
                     ports: dict[int, tuple[int, int]]) -> TrafficRates:
    """The two result sets to the wire model. No database."""
    rows: list[PortTraffic] = []
    silent: list[PortTraffic] = []
    measured = no_data = n_reset = 0
    first_at: datetime | None = None
    last_at: datetime | None = None

    for port_id in port_ids:
        identity = ports.get(port_id)
        if identity is None:
            continue
        node_guid, port_num = identity
        wire = {"node": guid_str(node_guid), "port": port_num}

        base = baseline.get(port_id)
        last = latest.get(port_id)
        r = rate_between(base, last)

        if r is None:
            # Two ways to have no rate, and they are different facts: no pair of
            # readings bounded the window, or a counter wrapped. `rate_between`
            # collapses both to None, so tell them apart here from the inputs.
            if base is not None and last is not None \
                    and last["time"] > base["time"]:
                n_reset += 1
                rows.append(PortTraffic(reset=True, **wire))
            else:
                no_data += 1
                silent.append(PortTraffic(no_data=True, **wire))
            continue

        assert base is not None and last is not None
        measured += 1
        first_at = base["time"] if first_at is None else min(first_at, base["time"])
        last_at = last["time"] if last_at is None else max(last_at, last["time"])

        # The floor is on the byte rates alone, not the packet rates.
        if r.tx < IDLE_FLOOR_GBPS and r.rx < IDLE_FLOOR_GBPS:
            continue

        rows.append(PortTraffic(
            tx=round(r.tx, 4), rx=round(r.rx, 4),
            txp=round(r.txp, 1), rxp=round(r.rxp, 1),
            **wire,
        ))

    if measured:
        rows.extend(silent)

    return TrafficRates(
        fabric=fabric,
        as_of=last_at or until,
        first_at=first_at,
        span_s=(last_at - first_at).total_seconds()
        if first_at and last_at else None,
        requested_window_s=window.total_seconds(),
        cadence_s=cadence.total_seconds() if cadence else None,
        coverage=TrafficCoverage(
            ports_expected=len(port_ids), ports_measured=measured,
            ports_no_data=no_data, ports_reset=n_reset,
        ),
        ports=rows,
    )


# ---- one port at a time ----------------------------------------------------

class PortCounters(NamedTuple):
    """One port's counters."""

    raw: dict[CounterName, int]
    at: datetime
    delta: dict[CounterName, int] | None
    span_s: float | None


def port_counters(db: Database, port_ids: list[int], since: datetime,
                  until: datetime,
                  lookback: timedelta = DEFAULT_BASELINE_LOOKBACK,
                  ) -> dict[int, PortCounters]:
    """Newest reading per port, plus what accumulated over (since, until]."""
    
    if not port_ids:
        return {}

    params = {"port_ids": port_ids, "since": since, "until": until,
              "floor": since - lookback}
    with db.pool.connection() as conn:
        latest = {r["port_id"]: r for r in
                  conn.execute(_PORT_LATEST, params).fetchall()}
        baseline = {r["port_id"]: r for r in
                    conn.execute(_PORT_BASE, params).fetchall()}

    out: dict[int, PortCounters] = {}
    for port_id, last in latest.items():
        raw: dict[CounterName, int] = {c: last[c] for c in COLUMNS}

        base = baseline.get(port_id)
        delta: dict[CounterName, int] | None = None
        if base is not None and base["time"] < last["time"]:
            moved = {c: last[c] - base[c] for c in COLUMNS}
            delta = ({c: d for c, d in moved.items() if d > 0}
                     if all(d >= 0 for d in moved.values()) else None)

        span = ((last["time"] - base["time"]).total_seconds()
                if base is not None and delta is not None else None)
        out[port_id] = PortCounters(raw=raw, at=last["time"], delta=delta,
                                    span_s=span)
    return out


# The detail card's traffic, scoped to the ports it is drawing rather 
# than to a fabric.

def port_traffic(db: Database, port_ids: list[int], until: datetime,
                 window: timedelta) -> dict[int, Rates]:
    """Per-port throughput over the trailing `window`.

    A port with no stateable rate is simply absent from the result, and the
    caller renders that as unknown rather than as idle. No flag is needed --
    `PortDetail.traffic` being None already carries it.
    """
    if not port_ids:
        return {}

    since = until - window
    lookback = max(window * BASELINE_LOOKBACK_FACTOR, MIN_BASELINE_LOOKBACK)
    params = {"port_ids": port_ids, "since": since, "until": until,
              "floor": since - lookback}

    with db.pool.connection() as conn:
        latest = {r["port_id"]: r for r in
                  conn.execute(_PORT_T_LATEST, params).fetchall()}
        baseline = {r["port_id"]: r for r in
                    conn.execute(_PORT_T_BASE, params).fetchall()}

    out: dict[int, Rates] = {}
    for port_id, last in latest.items():
        r = rate_between(baseline.get(port_id), last)
        if r is not None:
            out[port_id] = r
    return out
