"""Connection pooling and caching.

Three things every query needs and none of which belongs to a single route:

  * the pool. The collector holds one connection for its process lifetime; a
    server answering concurrent requests cannot.
  * `Resolve`, which answers "which sweep answers a request for this instant?"
  * the identity cache. Resolving a topology_event's port_id back to a
    (guid, port) pair is otherwise a join per event. Nodes and ports are
    append-only, so the cache never invalidates -- it only grows.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import LiteralString, cast

from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from infinimap.ibcore.guid import guid_str, unsigned

from .config import Config

log = logging.getLogger("infinimap.api")


class FabricNotFound(LookupError):
    """Named fabric has no row. Becomes a 404."""


class NoSnapshot(LookupError):
    """The fabric has no complete snapshot at or before the requested instant."""


@dataclass(frozen=True, slots=True)
class Resolved:
    """Response metadata.

    Sweeps are discrete: asking for 14:37 answers from the 14:35 sweep. Without
    this block on every response, "my diff is empty" is indistinguishable from
    "the collector was down all week".
    """

    requested_at: datetime
    snapshot_id: int
    collected_at: datetime
    latest_sweep_at: datetime | None    # The most recent sweep of any completeness, AKA "is the collector still running?"
    latest_sweep_complete: bool | None
    topology_hash: str


def utc(at: datetime | None) -> datetime:
    """Normalise to UTC datetime"""
    if at is None:
        return datetime.now(timezone.utc)
    return at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at


def as_of(alias: LiteralString, at: datetime | None) -> LiteralString:
    """Validity for a state table."""
    if at is None:
        return f"{alias}.valid_to IS NULL"
    return (f"{alias}.valid_from <= %(at)s "
            f"AND ({alias}.valid_to IS NULL OR {alias}.valid_to > %(at)s)")


def sql(template: LiteralString, **parts: LiteralString) -> LiteralString:
    """Compose a query from literal fragments."""
    return cast(LiteralString, template.format(**parts))


class Database:
    """Pool plus the per-fabric caches. One instance per process."""

    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.pool = ConnectionPool(
            cfg.dsn, min_size=cfg.pool_min, max_size=cfg.pool_max,
            kwargs={
                "row_factory": dict_row,
                "autocommit": True,
                "options": "-c timezone=UTC", # Ensure UTC across tables
            },
            open=False,
        )
        self._lock = threading.Lock()
        self._fabrics: dict[str, int] = {}
        self._ports: dict[int, tuple[int, int]] = {}   # port_id -> (guid, port_num)
        self._nodes: dict[int, int] = {}               # node_id -> node_guid
        self._identity_loaded: set[int] = set()
        self.names = _load_overrides(cfg.id_to_name_path)

    # -- lifecycle ----------------------------------------------------------

    def open(self) -> None:
        self.pool.open(wait=True, timeout=10.0)

    def close(self) -> None:
        self.pool.close()

    # -- fabric -------------------------------------------------------------

    def fabric_id(self, name: str) -> int:
        """Resolve a fabric name to its id, cached. Raises FabricNotFound."""
        with self._lock:
            hit = self._fabrics.get(name)
        if hit is not None:
            return hit

        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT fabric_id FROM fabric WHERE name = %s", (name,)
            ).fetchone()
        if row is None:
            raise FabricNotFound(name)

        with self._lock:
            self._fabrics[name] = row["fabric_id"]
        return row["fabric_id"]

    def fabrics(self) -> list[dict]:
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT fabric_id, name, subnet_prefix, description, created_at "
                "FROM fabric ORDER BY name"
            ).fetchall()
        for r in rows:
            r["subnet_prefix"] = guid_str(r.pop("subnet_prefix"))
        return rows

    # -- snapshots ----------------------------------------------------------

    def resolve(self, fabric_id: int, at: datetime | None, *,
                floor: bool = False) -> Resolved:
        """Which sweep answers a request for `at`. Raises NoSnapshot.

        `floor=True` is for the *start of a range*: a window reaching back
        before the first sweep clamps to it rather than failing, because "what
        changed in the last week" against four days of history has an obvious
        right answer. The clamp is visible -- `requested_at` and `collected_at`
        disagree, which is what that pair is for.
        """
        requested = utc(at)
        with self.pool.connection() as conn:
            state = conn.execute(   # Latest COMPLETE snapshot
                "SELECT snapshot_id, collected_at, topology_hash "
                "FROM topology_snapshot "
                "WHERE fabric_id = %s AND complete AND collected_at <= %s "
                "ORDER BY collected_at DESC, snapshot_id DESC LIMIT 1",
                (fabric_id, requested),
            ).fetchone()
            if state is None and floor:
                state = conn.execute(   # Earliest COMPLETE snapshot
                    "SELECT snapshot_id, collected_at, topology_hash "
                    "FROM topology_snapshot "
                    "WHERE fabric_id = %s AND complete "
                    "ORDER BY collected_at ASC, snapshot_id ASC LIMIT 1",
                    (fabric_id,),
                ).fetchone()
            if state is None:
                raise NoSnapshot(f"no complete snapshot at or before {requested.isoformat()}")

            horizon = max(requested, state["collected_at"])
            latest = conn.execute( # Latest snapshot of any completeness
                "SELECT collected_at, complete FROM topology_snapshot "
                "WHERE fabric_id = %s AND collected_at <= %s "
                "ORDER BY collected_at DESC, snapshot_id DESC LIMIT 1",
                (fabric_id, horizon),
            ).fetchone()

        return Resolved(
            requested_at=requested,
            snapshot_id=state["snapshot_id"],
            collected_at=state["collected_at"],
            latest_sweep_at=latest["collected_at"] if latest else None,
            latest_sweep_complete=latest["complete"] if latest else None,
            topology_hash=bytes(state["topology_hash"]).hex(),
        )

    def changed_between(self, fabric_id: int, lo: datetime, hi: datetime) -> bool:
        """Did any complete sweep in (lo, hi] record a change?"""
        with self.pool.connection() as conn:
            row = conn.execute(
                "SELECT bool_or(changed) AS any_change FROM topology_snapshot "
                "WHERE fabric_id = %s AND complete "
                "AND collected_at > %s AND collected_at <= %s",
                (fabric_id, lo, hi),
            ).fetchone()
        return bool(row and row["any_change"])

    # -- identity cache -----------------------------------------------------

    def identity(self, fabric_id: int) -> tuple[dict[int, tuple[int, int]], dict[int, int]]:
        """(port_id -> (node_guid, port_number), node_id -> node_guid)"""
        with self._lock:
            if fabric_id in self._identity_loaded:
                return self._ports, self._nodes
        return self.refresh_identity(fabric_id)

    def refresh_identity(self, fabric_id: int) -> tuple[dict[int, tuple[int, int]], dict[int, int]]:
        """Reload identity for a fabric. Called on a cache miss - which means
        hardware appeared since the last load, not that anything changed."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                "SELECT p.port_id, p.port_number, p.node_id, n.node_guid "
                "FROM port p JOIN node n USING (node_id) WHERE p.fabric_id = %s",
                (fabric_id,),
            ).fetchall()
        with self._lock:
            for r in rows:
                guid = unsigned(r["node_guid"])
                self._ports[r["port_id"]] = (guid, r["port_number"])
                self._nodes[r["node_id"]] = guid
            self._identity_loaded.add(fabric_id)
            return self._ports, self._nodes

    # -- display names ------------------------------------------------------

    def node_name(self, guid_hex: str | None, desc: str | None) -> str | None:
        """Operator override if there is one, else what the SA reported."""
        if guid_hex and (override := self.names["node"].get(guid_hex)):
            return override
        return desc

    def group_name(self, guid_hex: str | None) -> str | None:
        if not guid_hex:
            return None
        return self.names["group"].get(guid_hex, guid_hex)


def _load_overrides(path: Path | None) -> dict[str, dict[str, str]]:
    """Read id_to_name.json, defaulting to SA names on absence."""
    empty: dict[str, dict[str, str]] = {"node": {}, "group": {}}
    if path is None:
        return empty
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        log.warning("id_to_name overrides unusable (%s); serving SA names: %s", path, exc)
        return empty
    return {
        "node": dict(raw.get("nodeGuid", {})),
        "group": dict(raw.get("systemImageGuid", {})),
    }
