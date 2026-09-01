"""The fabric row, the id caches, and the GUID signedness conversion."""

from __future__ import annotations

from typing import Any

import psycopg

_U64 = 1 << 64
_I63 = 1 << 63


def signed(v: int | None) -> int | None:
    """Fold an unsigned 64-bit GUID into the signed BIGINT the schema stores.

    001 explains the consequences at length; the short form is that a GUID whose
    OUI has the high bit set stores negative, this round-trips exactly, and
    ordering comparisons on the stored value are meaningless.
    """
    if v is None:
        return None
    return v - _U64 if v >= _I63 else v


def unsigned(v: int) -> int:
    """The inverse, for GUIDs read back out."""
    return v + _U64 if v < 0 else v


def scalar(cur: psycopg.Cursor[Any]) -> int:
    """First column of the one row a RETURNING statement produces."""
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("INSERT ... RETURNING produced no row")
    return row[0]


class Identity:
    """Cached (guid -> node_id) and ((node_id, port_number) -> port_id) maps."""

    def __init__(self, conn: psycopg.Connection, fabric_name: str,
                 subnet_prefix: int | None):
        self.conn = conn
        self.fabric_name = fabric_name
        self.subnet_prefix = subnet_prefix
        self.fabric_id: int | None = None

        self._node_id: dict[int, int] = {}
        self._port_id: dict[tuple[int, int], int] = {}

    # -- setup --------------------------------------------------------------

    def open(self) -> None:
        with self.conn.transaction():
            self._ensure_fabric()
            self._seed_caches()

    @property
    def is_open(self) -> bool:
        return self.fabric_id is not None

    def _ensure_fabric(self) -> None:
        row = self.conn.execute(
            "SELECT fabric_id FROM fabric WHERE name = %s", (self.fabric_name,)
        ).fetchone()
        if row is not None:
            self.fabric_id = row[0]
            return
        self.fabric_id = scalar(self.conn.execute(
            "INSERT INTO fabric (name, subnet_prefix) VALUES (%s, %s) "
            "RETURNING fabric_id",
            (self.fabric_name, signed(self.subnet_prefix)),
        ))

    def _seed_caches(self) -> None:
        """Load every id this fabric already has."""
        for node_id, guid in self.conn.execute(
            "SELECT node_id, node_guid FROM node WHERE fabric_id = %s",
            (self.fabric_id,),
        ):
            self._node_id[unsigned(guid)] = node_id
        for port_id, node_id, pnum in self.conn.execute(
            "SELECT port_id, node_id, port_number FROM port WHERE fabric_id = %s",
            (self.fabric_id,),
        ):
            self._port_id[(node_id, pnum)] = port_id

    # -- lookup -------------------------------------------------------------

    def node_id(self, guid: int) -> int | None:
        return self._node_id.get(guid)

    def port_id(self, guid: int, port_number: int) -> int | None:
        """port_id for a (NodeGUID, port number) pair, or None if this fabric has
        no such port. None is an ordinary answer, not an error: ibqueryerrors
        discovers by direct route and sees hardware the Subnet Administrator does
        not publish."""
        node_id = self._node_id.get(guid)
        if node_id is None:
            return None
        return self._port_id.get((node_id, port_number))

    def has_port(self, node_id: int, port_number: int) -> bool:
        return (node_id, port_number) in self._port_id

    # -- growth -------------------------------------------------------------

    def remember_node(self, guid: int, node_id: int) -> None:
        self._node_id[guid] = node_id

    def remember_port(self, node_id: int, port_number: int,
                      port_id: int) -> None:
        self._port_id[(node_id, port_number)] = port_id

    def counts(self) -> tuple[int, int]:
        return (len(self._node_id), len(self._port_id))
