"""Fold an Observation into the identity + state schema.

  * an incomplete sweep is recorded but never diffed: a wrong diff corrupts
    history permanently, a missing one is a small gap.
  * an unchanged topology_hash skips the diff entirely.

Everything happens in one transaction per cycle, so a crash mid-write leaves the
database on the previous consistent snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import psycopg
from psycopg import sql
from psycopg.types.json import Json

from .hashing import observation_hash
from .identity import Identity, scalar as _scalar, signed as _signed
from ..observation import Observation


@dataclass
class WriteResult:
    snapshot_id: int | None = None
    complete: bool = True
    changed: bool = False
    nodes_new: int = 0
    ports_new: int = 0
    opened: dict[str, int] = field(default_factory=dict)
    closed: dict[str, int] = field(default_factory=dict)
    events: int = 0


class Writer:
    """Writes identity and state. Owns no id cache of its own: it inserts into
    the shared Identity, which the counter writer then resolves through. This is
    the writer that CREATES ports, so it must run first in a cycle."""

    def __init__(self, conn: psycopg.Connection, identity: Identity):
        self.conn = conn
        self.ident = identity

    # -- setup --------------------------------------------------------------

    def open(self) -> None:
        self.ident.open()

    @property
    def fabric_id(self) -> int | None:
        return self.ident.fabric_id

    # -- main entry ---------------------------------------------------------

    def write(self, obs: Observation) -> WriteResult:
        if not self.ident.is_open:
            self.open()

        res = WriteResult(complete=obs.complete)
        new_hash = observation_hash(obs)
        ts = obs.collected_at
        n, p, l = obs.counts()

        with self.conn.transaction():
            changed = self._is_changed(new_hash) if obs.complete else False
            res.changed = changed
            res.snapshot_id = self._insert_snapshot(
                ts, new_hash, changed, obs.complete, n, p, l,
                obs.origin, obs.duration_ms,
            )
            self._snapshot_id = res.snapshot_id

            # An incomplete sweep is recorded but never diffed. An 
            # unchanged sweep needs nothing beyond the snapshot row
            if not obs.complete:
                self._record_incomplete(obs, res)
                return res
            if not changed:
                return res

            # Identity before state: state and event rows reference these ids.
            # Inserts are no-ops for hardware already cached.
            self._ensure_identity(obs, ts, res)
            self._diff_nodes(obs, ts, res)
            self._diff_ports(obs, ts, res)
            self._diff_links(obs, ts, res)

        return res

    # -- identity -----------------------------------------------------------

    def _ensure_identity(self, obs: Observation, ts: str, res: WriteResult) -> None:
        for node in obs.nodes.values():
            if self.ident.node_id(node.guid) is not None:
                continue
            node_id = _scalar(self.conn.execute(
                "INSERT INTO node (fabric_id, node_guid, node_type, sys_image_guid, "
                "vendor_id, device_id, num_ports, first_seen) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s::timestamptz) "
                "RETURNING node_id",
                (self.fabric_id, _signed(node.guid), node.node_type,
                 _signed(node.sys_image_guid), node.vendor_id, node.device_id,
                 node.num_ports, ts),
            ))
            self.ident.remember_node(node.guid, node_id)
            res.nodes_new += 1

        for port in obs.ports.values():
            node_id = self.ident.node_id(port.node_guid)
            assert node_id is not None, "node inserted above must be cached"
            if self.ident.has_port(node_id, port.port_number):
                continue
            port_id = _scalar(self.conn.execute(
                "INSERT INTO port (node_id, fabric_id, port_number, port_guid, "
                "first_seen) VALUES (%s, %s, %s, %s, %s::timestamptz) "
                "RETURNING port_id",
                (node_id, self.fabric_id, port.port_number,
                 _signed(port.port_guid), ts),
            ))
            self.ident.remember_port(node_id, port.port_number, port_id)
            res.ports_new += 1

    # -- snapshot -----------------------------------------------------------

    def _is_changed(self, new_hash: bytes) -> bool:
        row = self.conn.execute(
            "SELECT topology_hash FROM topology_snapshot "
            "WHERE fabric_id = %s AND complete "
            "ORDER BY collected_at DESC, snapshot_id DESC LIMIT 1",
            (self.fabric_id,),
        ).fetchone()
        if row is None:
            return True
        return bytes(row[0]) != new_hash

    def _insert_snapshot(self, ts: str, h: bytes, changed: bool, complete: bool,
                         n: int, p: int, l: int, origin: str,
                         duration_ms: int | None) -> int:
        return _scalar(self.conn.execute(
            "INSERT INTO topology_snapshot (fabric_id, collected_at, source, "
            "topology_hash, changed, complete, node_count, port_count, link_count, "
            "origin, duration_ms) "
            "VALUES (%s, %s::timestamptz, 'sa_query', %s, %s, %s, %s, %s, %s, %s, %s) "
            "RETURNING snapshot_id",
            (self.fabric_id, ts, h, changed, complete, n, p, l, origin, duration_ms),
        ))

    def _record_incomplete(self, obs: Observation, res: WriteResult) -> None:
        self.conn.execute(
            "INSERT INTO topology_event (fabric_id, occurred_at, snapshot_id, "
            "event_type, detail) VALUES (%s, %s::timestamptz, %s, "
            "'sweep_incomplete', %s)",
            (self.fabric_id, obs.collected_at, res.snapshot_id,
             Json({"problems": obs.problems})),
        )
        res.events += 1

    # -- diffs --------------------------------------------------------------

    def _diff_nodes(self, obs: Observation, ts: str, res: WriteResult) -> None:
        cur: dict[int, tuple] = {}
        for node_id, desc, fw, sm in self.conn.execute(
            "SELECT ns.node_id, ns.node_desc, ns.fw_version, ns.sm_state "
            "FROM node_state ns JOIN node n USING (node_id) "
            "WHERE n.fabric_id = %s AND ns.valid_to IS NULL",
            (self.fabric_id,),
        ):
            cur[node_id] = (desc, fw, sm)

        seen: set[int] = set()
        for node in obs.nodes.values():
            node_id = self.ident.node_id(node.guid)
            seen.add(node_id)
            # Carried forward, not defaulted, when the SM was not read this sweep.
            sm = node.sm_state
            if not obs.sm_seen and node_id in cur:
                sm = cur[node_id][2]
            desired = (node.desc, None, sm)   # fw_version deferred: not in any record we read
            if node_id not in cur:
                self._open_node(node_id, ts, desired)
                self._event(ts, res, "node_added", node_id=node_id)
                res.opened["node_state"] = res.opened.get("node_state", 0) + 1
            elif cur[node_id] != desired:
                self._close("node_state", "node_id", node_id, ts)
                self._open_node(node_id, ts, desired)
                res.closed["node_state"] = res.closed.get("node_state", 0) + 1
                res.opened["node_state"] = res.opened.get("node_state", 0) + 1
                if cur[node_id][0] != desired[0]:
                    self._event(ts, res, "node_renamed", node_id=node_id,
                                detail={"from": cur[node_id][0], "to": desired[0]})
                if cur[node_id][2] != desired[2]:
                    self._event(ts, res, "sm_failover", node_id=node_id,
                                detail={"from": cur[node_id][2], "to": desired[2]})

        for node_id in cur.keys() - seen:
            self._close("node_state", "node_id", node_id, ts)
            self._event(ts, res, "node_removed", node_id=node_id)
            res.closed["node_state"] = res.closed.get("node_state", 0) + 1

    def _diff_ports(self, obs: Observation, ts: str, res: WriteResult) -> None:
        cols = ("log_state", "phys_state", "active_speed", "active_width",
                "enabled_speed", "enabled_width", "supported_speed",
                "supported_width", "lid", "mtu")
        cur: dict[int, tuple] = {}
        for row in self.conn.execute(
            "SELECT ps.port_id, ps.log_state, ps.phys_state, ps.active_speed, "
            "ps.active_width, ps.enabled_speed, ps.enabled_width, "
            "ps.supported_speed, ps.supported_width, ps.lid, ps.mtu "
            "FROM port_state ps JOIN port p USING (port_id) "
            "WHERE p.fabric_id = %s AND ps.valid_to IS NULL",
            (self.fabric_id,),
        ):
            cur[row[0]] = tuple(row[1:])

        seen: set[int] = set()
        for port in obs.ports.values():
            port_id = self.ident.port_id(port.node_guid, port.port_number)
            seen.add(port_id)
            desired = (port.log_state, port.phys_state, port.active_speed,
                       port.active_width, port.enabled_speed, port.enabled_width,
                       port.supported_speed, port.supported_width, port.lid, port.mtu)
            if port_id not in cur:
                self._open_port(port_id, ts, desired)
                self._event(ts, res, "port_added", port_id=port_id,
                            node_id=self.ident.node_id(port.node_guid))
                res.opened["port_state"] = res.opened.get("port_state", 0) + 1
            elif cur[port_id] != desired:
                self._close("port_state", "port_id", port_id, ts)
                self._open_port(port_id, ts, desired)
                res.closed["port_state"] = res.closed.get("port_state", 0) + 1
                res.opened["port_state"] = res.opened.get("port_state", 0) + 1
                self._port_change_events(port_id, cur[port_id], desired, ts, res)

        # A port vanishing closes its state row;
        for port_id in cur.keys() - seen:
            self._close("port_state", "port_id", port_id, ts)
            res.closed["port_state"] = res.closed.get("port_state", 0) + 1

    def _port_change_events(self, port_id, old, new, ts, res) -> None:
        # old/new positional per the cols tuple: 0 log,1 phys,2 aspeed,3 awidth...
        if old[2] != new[2]:
            self._event(ts, res, "speed_changed", port_id=port_id,
                        detail={"from": old[2], "to": new[2]})
        if old[3] != new[3]:
            self._event(ts, res, "width_changed", port_id=port_id,
                        detail={"from": old[3], "to": new[3]})
        if old[0] != new[0]:
            self._event(ts, res, "port_state_changed", port_id=port_id,
                        detail={"from": old[0], "to": new[0]})

    def _diff_links(self, obs: Observation, ts: str, res: WriteResult) -> None:
        cur: set[tuple[int, int]] = set()
        for a, b in self.conn.execute(
            "SELECT ls.port_a_id, ls.port_b_id FROM link_state ls "
            "JOIN port p ON p.port_id = ls.port_a_id "
            "WHERE p.fabric_id = %s AND ls.valid_to IS NULL",
            (self.fabric_id,),
        ):
            cur.add((a, b))

        observed: set[tuple[int, int]] = set()
        for link in obs.links:
            a_id = self.ident.port_id(link.a[0], link.a[1])
            b_id = self.ident.port_id(link.b[0], link.b[1])
            # Re-canonicalise by port_id
            pair = (a_id, b_id) if a_id < b_id else (b_id, a_id)
            observed.add(pair)

        # Close before open
        for pair in cur - observed:
            self.conn.execute(
                "UPDATE link_state SET valid_to = %s::timestamptz "
                "WHERE port_a_id = %s AND port_b_id = %s AND valid_to IS NULL",
                (ts, pair[0], pair[1]),
            )
            self._event(ts, res, "link_removed", port_id=pair[0], peer_port_id=pair[1])
            res.closed["link_state"] = res.closed.get("link_state", 0) + 1

        for pair in observed - cur:
            self.conn.execute(
                "INSERT INTO link_state (port_a_id, port_b_id, valid_from, "
                "snapshot_id) VALUES (%s, %s, %s::timestamptz, %s)",
                (pair[0], pair[1], ts, res.snapshot_id),
            )
            self._event(ts, res, "link_added", port_id=pair[0], peer_port_id=pair[1])
            res.opened["link_state"] = res.opened.get("link_state", 0) + 1

    # -- row helpers --------------------------------------------------------

    def _close(self, table: str, key: str, key_val: int, ts: str) -> None:
        self.conn.execute(
            sql.SQL(
                "UPDATE {tbl} SET valid_to = %s::timestamptz "
                "WHERE {col} = %s AND valid_to IS NULL"
            ).format(tbl=sql.Identifier(table), col=sql.Identifier(key)),
            (ts, key_val),
        )

    def _open_node(self, node_id: int, ts: str, desired: tuple) -> None:
        self.conn.execute(
            "INSERT INTO node_state (node_id, valid_from, snapshot_id, node_desc, "
            "fw_version, sm_state) VALUES (%s, %s::timestamptz, %s, %s, %s, %s)",
            (node_id, ts, self._snapshot_id, desired[0], desired[1], desired[2]),
        )

    def _open_port(self, port_id: int, ts: str, d: tuple) -> None:
        self.conn.execute(
            "INSERT INTO port_state (port_id, valid_from, snapshot_id, log_state, "
            "phys_state, active_speed, active_width, enabled_speed, enabled_width, "
            "supported_speed, supported_width, lid, mtu) "
            "VALUES (%s, %s::timestamptz, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (port_id, ts, self._snapshot_id, *d),
        )

    def _event(self, ts: str, res: WriteResult, event_type: str, *,
               node_id: int | None = None, port_id: int | None = None,
               peer_port_id: int | None = None, detail: dict | None = None) -> None:
        self.conn.execute(
            "INSERT INTO topology_event (fabric_id, occurred_at, snapshot_id, "
            "event_type, node_id, port_id, peer_port_id, detail) "
            "VALUES (%s, %s::timestamptz, %s, %s, %s, %s, %s, %s)",
            (self.fabric_id, ts, self._snapshot_id, event_type, node_id, port_id,
             peer_port_id, Json(detail) if detail is not None else None),
        )
        res.events += 1

    # snapshot_id for the row helpers is stashed during write()
    _snapshot_id: int | None = None
