"""One collection cycle, and the loop around it.

A cycle has two halves against one fabric, in this order and for this reason:

  topology   saquery -> Observation -> identity + state (001/002)
  errors     ibqueryerrors -> CounterObservation -> port_errors (003)

Topology first, because a fact row carries port_id and the topology half is what
creates port rows.

The error half also DEPENDS on the topology half for its port universe: plain
ibqueryerrors names only the ports with something nonzero, and dense storage
needs a row for every port polled. That universe is the topology Observation's
port set, which is why the two run together rather than on separate clocks.

Traffic is a third job and does NOT belong to the cycle: `--counters` reports
every port unconditionally, so it needs no universe, and it is cheap enough to
run far more often than the cycle. It runs on its own thread with its own
connection, sharing only the Identity cache; see traffic_loop.
"""

from __future__ import annotations

import logging
import signal
import threading
import time
from datetime import datetime, timezone

import psycopg

from .. import __version__
from ..db import startup
from .acquire import ibqueryerrors, saquery
from .config import Config
from .counters import CounterObservation
from .ingest.counters.assemble import (build_error_observation,
                                       build_traffic_observation)
from .ingest.topology.assemble import build_observation
from .observation import Observation
from .store.counters import CounterWriteResult, CounterWriter
from .store.identity import Identity
from .store.writer import WriteResult, Writer

log = logging.getLogger("infinimap.collector")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# -- topology half ----------------------------------------------------------

def topology_cycle(writer: Writer, cfg: Config) -> tuple[Observation, WriteResult]:
    started = time.perf_counter()

    results = saquery.collect(cfg.from_dir)
    unclean = [r for r in saquery.RECORDS if not saquery.is_clean(results[r])]
    for r in results.values():
        if not saquery.is_clean(r):
            log.warning("query %s not clean (rc=%s stderr=%r)",
                        r.name, r.rc, saquery.errors(r).strip()[:200])
        elif saquery.benign(r):
            log.debug("query %s: %d benign formatter diagnostic(s) ignored",
                      r.name, saquery.benign(r))

    sm = results["SMInfoRecord"]
    obs = build_observation(
        results["NodeRecord"].text,
        results["PortInfoRecord"].text,
        results["LinkRecord"].text,
        _now(),
        already_incomplete=bool(unclean),
        sm_text=sm.text if saquery.is_clean(sm) else None,
    )
    obs.origin = "live" if cfg.live else "from_file"
    obs.duration_ms = round((time.perf_counter() - started) * 1000)

    res = writer.write(obs)

    n, p, l = obs.counts()
    log.info(
        "topology: snapshot=%s complete=%s changed=%s origin=%s took=%dms "
        "| nodes=%d ports=%d links=%d "
        "| new(node=%d port=%d) opened=%s closed=%s events=%d",
        res.snapshot_id, res.complete, res.changed, obs.origin, obs.duration_ms,
        n, p, l,
        res.nodes_new, res.ports_new, dict(res.opened), dict(res.closed), res.events,
    )
    if not obs.complete:
        log.warning("sweep incomplete, state not diffed: %s",
                    "; ".join(obs.problems[:5]))
    if not obs.sm_seen:
        log.warning("SMInfoRecord not read; sm_state carried forward")
    return obs, res


# -- error half -------------------------------------------------------------

def error_cycle(writer: CounterWriter, cfg: Config,
                obs: Observation) -> CounterWriteResult | None:
    """Read plain ibqueryerrors and write port_errors. Returns None when there
    was nothing to read."""
    started_at = _now()
    t0 = time.perf_counter()
    result = ibqueryerrors.collect(cfg.from_dir)

    if result.empty and result.rc == 2:
        log.debug("no ibqueryerrors output available (%s); error half skipped",
                  result.stderr.strip() or "absent")
        return None

    clean = ibqueryerrors.is_clean(result)
    if not clean:
        # The return code is NOT the signal -- ibqueryerrors exits nonzero
        # whenever any node has errors, which is most sweeps worth having.
        log.warning("ibqueryerrors output unusable (rc=%s, %d bytes, stderr=%r)",
                    result.rc, len(result.text), result.stderr.strip()[:200])

    cobs = build_error_observation(
        result.text,
        universe=set(obs.ports),
        collected_at=obs.collected_at,   # one instant for the whole cycle
        started_at=started_at,
        finished_at=_now(),
        already_incomplete=not clean,
        origin="live" if cfg.live else "from_file",
    )
    cobs.duration_ms = round((time.perf_counter() - t0) * 1000)
    return _write_and_log(writer, cobs)


# -- traffic --------------------------------------------------------------

def traffic_sweep(writer: CounterWriter,
                  cfg: Config) -> CounterWriteResult | None:
    """One `--counters` sweep. Independent of the topology cycle in every way
    except the Identity cache: --counters names every port it checks, so there
    is no universe to supply and nothing to order against."""
    started_at = _now()
    t0 = time.perf_counter()
    result = ibqueryerrors.collect_counters(cfg.from_dir)

    if result.empty and result.rc == 2:
        log.debug("no --counters output available (%s); traffic sweep skipped",
                  result.stderr.strip() or "absent")
        return None

    clean = ibqueryerrors.is_clean(result)
    if not clean:
        log.warning("--counters output unusable (rc=%s, %d bytes, stderr=%r)",
                    result.rc, len(result.text), result.stderr.strip()[:200])

    tobs = build_traffic_observation(
        result.text,
        collected_at=_now(),
        started_at=started_at,
        finished_at=_now(),
        already_incomplete=not clean,
        origin="live" if cfg.live else "from_file",
    )
    tobs.duration_ms = round((time.perf_counter() - t0) * 1000)
    return _write_and_log(writer, tobs)


def traffic_loop(cfg: Config, identity: Identity, stop: threading.Event) -> None:
    """The traffic cadence, on its own thread and its own connection."""
    with psycopg.connect(cfg.dsn, autocommit=True,
                         options="-c timezone=UTC") as conn:
        writer = CounterWriter(conn, identity)
        log.info("traffic loop started at %.1fs cadence", cfg.traffic_interval_s)
        while not stop.is_set():
            t0 = time.perf_counter()
            try:
                traffic_sweep(writer, cfg)
            except Exception:
                log.exception("traffic sweep failed; continuing")
            # Sleep the REMAINDER, not the interval. A sweep that took 0.4s must
            # not push the cadence to 5.4s and drift the series against the wall
            # clock; the rollups bucket on time_bucket and expect a steady rate.
            stop.wait(max(0.0, cfg.traffic_interval_s - (time.perf_counter() - t0)))


# -- shared write + log ---------------------------------------------------

def _write_and_log(writer: CounterWriter,
                   obs: CounterObservation) -> CounterWriteResult:
    res = writer.write(obs)
    rows, reported = obs.counts()
    log.info(
        "%s: sweep=%s complete=%s origin=%s took=%dms "
        "| rows=%d written=%d (named=%d zero_filled=%d unresolved=%d)",
        obs.source, res.sweep_id, res.complete, obs.origin, obs.duration_ms,
        rows, res.written, reported, res.zero_filled, res.ports_unresolved,
    )
    if obs.aggregates_dropped:
        log.debug("%d 'port ALL' aggregate line(s) dropped",
                  obs.aggregates_dropped)
    if obs.unknown_counters:
        log.warning("counter name(s) with no column in %s: %s",
                    obs.table, dict(list(obs.unknown_counters.items())[:6]))
    if not obs.complete:
        log.warning("%s sweep incomplete: %s", obs.source,
                    "; ".join(obs.problems[:5]))
    return res


# -- the cycle --------------------------------------------------------------

def one_cycle(writer: Writer, errors: CounterWriter, cfg: Config) -> WriteResult:
    obs, res = topology_cycle(writer, cfg)
    try:
        error_cycle(errors, cfg, obs)
    except Exception:
        log.exception("error half failed; topology snapshot %s stands",
                      res.snapshot_id)
    return res


def run(cfg: Config, *, once: bool) -> int:
    stop = threading.Event()

    def _graceful(signum, _frame):
        log.info("signal %s received, stopping after current cycle "
                 "(signal again to quit now)", signum)
        stop.set()
        signal.signal(signum, signal.default_int_handler
                      if signum == signal.SIGINT else signal.SIG_DFL)

    signal.signal(signal.SIGTERM, _graceful)
    signal.signal(signal.SIGINT, _graceful)

    with psycopg.connect(cfg.dsn, autocommit=True, options="-c timezone=UTC") as conn:
        startup.check_schema(conn, component="collector")
        startup.check_clock(conn, component="collector",
                            max_skew_s=cfg.max_clock_skew_s)

        identity = Identity(conn, cfg.fabric_name, cfg.subnet_prefix)
        writer = Writer(conn, identity)
        errors = CounterWriter(conn, identity)
        writer.open()
        errors.open()
        log.info("collector started: version=%s fabric=%r source=%s",
                 __version__, cfg.fabric_name,
                 cfg.from_dir or "live saquery + ibqueryerrors")

        if once:
            # One of each, in cycle order: the traffic sweep is independent, but
            # running it second means --once against a fixture directory writes
            # every table the daemon would.
            one_cycle(writer, errors, cfg)
            traffic_sweep(CounterWriter(conn, identity), cfg)
            return 0

        # Started after the first cycle rather than before it, so that the
        # Identity cache is warm.
        one_cycle(writer, errors, cfg)
        traffic = threading.Thread(target=traffic_loop, name="traffic",
                                   args=(cfg, identity, stop), daemon=True)
        traffic.start()

        while not stop.is_set():
            stop.wait(cfg.interval_s)
            if stop.is_set():
                break
            try:
                one_cycle(writer, errors, cfg)
            except Exception:  # one bad cycle must not kill the daemon
                log.exception("cycle failed; continuing")

        traffic.join(timeout=cfg.traffic_interval_s + 5)
    return 0
