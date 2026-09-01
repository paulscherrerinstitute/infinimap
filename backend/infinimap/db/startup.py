"""What both halves verify against the database before doing any work.

Neither check mattered while the collector, the API and PostgreSQL shared a
machine. Split across two hosts -- which is the deployment this is built for --
both become ways to be quietly wrong rather than loudly broken.

**Schema version.** One distribution does not stop two machines running
different builds against one database. Only reading `schema_version` and
comparing it against the migrations this build actually carries does.

**Clock skew.** The collector stamps `valid_from`, `collected_at` and the
`time` column of both hypertables from its *own* clock, so its skew is written
into the history: TimescaleDB's retention and compression policies compare those
values against the server's clock, and a collector running behind can have rows
dropped early. The API is milder but not exempt -- it calls
`datetime.now(timezone.utc)` in seven places to mean "now", including the
`at < now()` test in `routes/deps.py` that decides whether a response is
historical and therefore cacheable.

Hence the asymmetry below: the writer refuses, the reader warns.
"""

from __future__ import annotations

import logging
import time

import psycopg

from .schema import discover, has_version_table, scalar

log = logging.getLogger("infinimap.db")

#: The newest migration this build ships.
EXPECTED_VERSION: int = max((m.version for m in discover()), default=-1)

#: Skew worth mentioning.
WARN_SKEW_S = 0.1

#: Samples taken when measuring; the one with the fastest round trip wins,
#: because round-trip time is the whole uncertainty in the measurement.
_SAMPLES = 3


class SchemaMismatch(RuntimeError):
    """The database schema is not what this build expects."""


class ClockSkew(RuntimeError):
    """This host's clock disagrees with the database's by too much."""


# -- schema ------------------------------------------------------------------

def schema_version(conn) -> int | None:
    """The highest applied migration, or None if never initialised."""
    if not has_version_table(conn):
        return None
    try:
        found = scalar(conn, "SELECT max(version) FROM schema_version")
    except psycopg.errors.InsufficientPrivilege as exc:
        raise SchemaMismatch(
            f"cannot read schema_version: {str(exc).strip().splitlines()[0]}\n"
            f"    This role needs SELECT on the infinimap tables; "
            f"`infinimap-db init` grants it.\n"
            f"    Check the DSN names the right database, too."
        ) from exc
    return None if found is None else int(found)


def check_schema(conn, *, component: str) -> int | None:
    """Compare the database against the migrations this build carries.

    Older or absent is fatal: the queries reference columns that are not there
    yet, so continuing only turns a clear message into a confusing 500.

    Newer is a warning, not an error. Migrations are supposed to be additive.
    """
    found = schema_version(conn)

    if found is None:
        raise SchemaMismatch(
            f"{component}: database has no schema (expected version "
            f"{EXPECTED_VERSION}).\n    infinimap-db init"
        )
    if found < EXPECTED_VERSION:
        raise SchemaMismatch(
            f"{component}: database is at schema {found}, this build needs "
            f"{EXPECTED_VERSION}.\n    infinimap-db migrate"
        )
    if found > EXPECTED_VERSION:
        log.warning(
            "%s: database is at schema %d, newer than this build (%d); "
            "upgrade infinimap on this host", component, found, EXPECTED_VERSION)
    return found


# -- clock -------------------------------------------------------------------

def measure_skew(conn, samples: int = _SAMPLES) -> tuple[float, float]:
    """(skew, round_trip) in seconds. Positive skew means the server is ahead.

    The server's answer is only known to lie somewhere inside the round trip,
    so the local clock is read either side and compared against the midpoint.
    """
    best: tuple[float, float] | None = None
    for _ in range(max(1, samples)):
        before = time.time()
        # clock_timestamp(), not now(): now() is the transaction's start time,
        # which is not the wall clock at the moment we ask.
        server = float(scalar(conn, "SELECT extract(epoch FROM clock_timestamp())"))
        after = time.time()

        round_trip = after - before
        if best is None or round_trip < best[1]:
            best = (server - (before + after) / 2, round_trip)

    assert best is not None
    return best


def certain_skew(skew: float, round_trip: float) -> float:
    """How much skew the measurement actually proves, never below zero."""
    return max(0.0, abs(skew) - round_trip / 2)


def check_clock(conn, *, component: str, max_skew_s: float = 0.0) -> float:
    """Measure skew, complain proportionately, and return it.

    `max_skew_s` of 0 measures and reports but never raises.
    """
    skew, round_trip = measure_skew(conn)
    proven = certain_skew(skew, round_trip)
    detail = (f"clock is {skew:+.3f}s from the database "
              f"(round trip {round_trip * 1000:.1f}ms)")

    if max_skew_s and proven > max_skew_s:
        raise ClockSkew(
            f"{component}: {detail}, over the {max_skew_s:g}s limit.\n"
            f"    Timestamps written here are compared against the database's "
            f"own clock, so this would corrupt the history.\n"
            f"    Fix NTP on this host, or raise max_clock_skew_s to override."
        )
    if proven > WARN_SKEW_S:
        log.warning("%s: %s", component, detail)
    else:
        log.debug("%s: %s", component, detail)
    return skew
