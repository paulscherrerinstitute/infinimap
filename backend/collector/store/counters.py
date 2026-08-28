"""Write a CounterObservation into the timeseries schema (003).

Nothing here interprets anything. The observation already carries one dense row
per port, and this module resolves those ports to port_id and inserts them. That
is the whole job.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import psycopg
from psycopg import sql

from .identity import Identity, scalar as _scalar
from ..counters import CounterObservation

log = logging.getLogger("ibmon.collector.counters")


@dataclass
class CounterWriteResult:
    sweep_id: int | None = None
    source: str = ""
    complete: bool = True
    rows: int = 0                # rows the observation carried
    written: int = 0             # rows that resolved and were inserted
    ports_unresolved: int = 0    # named by the tool, no port row
    zero_filled: int = 0         # dense rows asserted from the universe

    @property
    def lost(self) -> int:
        return self.rows - self.written


class CounterWriter:
    """Writes counter_sweep and one fact table.

    One instance per source: the INSERT is built once from the observation's
    column list and cached. Resolves identity but never creates it -- the
    topology Writer owns that, and must have run first in the cycle.
    """

    def __init__(self, conn: psycopg.Connection, identity: Identity):
        self.conn = conn
        self.ident = identity
        self._insert: dict[str, sql.Composed] = {}

    # -- setup --------------------------------------------------------------

    def open(self) -> None:
        if not self.ident.is_open:
            self.ident.open()

    # -- main entry ---------------------------------------------------------

    def write(self, obs: CounterObservation) -> CounterWriteResult:
        """Write one counter sweep. One transaction, two statements plus the
        row insert -- the sweep row has to exist before the facts can reference
        it, and its ports_written is only known once resolution has run."""
        res = CounterWriteResult(source=obs.source, complete=obs.complete,
                                 rows=len(obs.rows),
                                 zero_filled=obs.ports_zero_filled)

        with self.conn.transaction():
            res.sweep_id = self._insert_sweep(obs)

            params: list[tuple] = []
            for row in obs.rows:
                port_id = self.ident.port_id(row.port[0], row.port[1])
                if port_id is None:
                    res.ports_unresolved += 1
                    log.debug("counter reading for unknown port 0x%016x/%d dropped",
                              row.port[0], row.port[1])
                    continue
                params.append((obs.collected_at, port_id, res.sweep_id,
                               *row.values))

            if params:
                self.conn.cursor().executemany(self._statement(obs), params)
            res.written = len(params)

            self.conn.execute(
                "UPDATE counter_sweep SET ports_written = %s WHERE sweep_id = %s",
                (res.written, res.sweep_id),
            )

        return res

    # -- statements ---------------------------------------------------------

    def _statement(self, obs: CounterObservation) -> sql.Composed:
        """The INSERT for this source, built from the observation's own column
        list so that adding a counter to counters.py is the only edit needed."""
        cached = self._insert.get(obs.source)
        if cached is not None:
            return cached

        cols = ("time", "port_id", "sweep_id", *obs.columns)
        stmt = sql.SQL(
            "INSERT INTO {table} ({cols}) VALUES ({vals}) "
            "ON CONFLICT (port_id, time) DO NOTHING"
        ).format(
            table=sql.Identifier(obs.table),
            cols=sql.SQL(", ").join(sql.Identifier(c) for c in cols),
            # time is the only one needing a cast: it arrives as an ISO string.
            vals=sql.SQL(", ").join(
                [sql.SQL("%s::timestamptz")]
                + [sql.SQL("%s")] * (len(cols) - 1)
            ),
        )
        self._insert[obs.source] = stmt
        return stmt

    def _insert_sweep(self, obs: CounterObservation) -> int:
        """Written unconditionally, before anything else.
        
        ports_polled prefers the tool's own count over a tally of what we hold:
        the tool knows what it asked, a tally only knows what answered.
        """
        # Reported verbatim, never clamped. ports_written may exceed it -- the
        # tool sees hardware the SA does not publish -- and 003 has no
        # constraint between the two precisely so this number can stay honest.
        polled = obs.ports_checked
        if polled is None:
            polled = len(obs.rows)

        return _scalar(self.conn.execute(
            "INSERT INTO counter_sweep (fabric_id, source, started_at, "
            "finished_at, ports_polled, ports_written, origin, collector_id) "
            "VALUES (%s, %s, %s::timestamptz, %s::timestamptz, %s, 0, %s, %s) "
            "RETURNING sweep_id",
            (self.ident.fabric_id, obs.source, obs.started_at, obs.finished_at,
             polled, obs.origin, None),
        ))
