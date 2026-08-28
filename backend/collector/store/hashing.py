"""Canonical hash of an Observation, for the snapshot change-gate.

The hash covers every field that can drive a state write, so an unchanged hash is a licence to skip diffing entirely.
"""

from __future__ import annotations

import hashlib

from ..observation import Observation


def _node_tuple(n) -> tuple:
    return (
        n.guid, n.node_type, n.sys_image_guid, n.vendor_id,
        n.device_id, n.num_ports, n.desc, n.sm_state,
    )


def _port_tuple(p) -> tuple:
    return (
        p.node_guid, p.port_number, p.port_guid,
        p.log_state, p.phys_state,
        p.active_speed, p.active_width,
        p.enabled_speed, p.enabled_width,
        p.supported_speed, p.supported_width,
        p.lid, p.mtu,
    )


def observation_hash(obs: Observation) -> bytes:
    """32-byte sha256 over the canonicalised topology + state."""
    h = hashlib.sha256()
    for guid in sorted(obs.nodes):
        h.update(repr(_node_tuple(obs.nodes[guid])).encode())
        h.update(b"\x00")
    for key in sorted(obs.ports):
        h.update(repr(_port_tuple(obs.ports[key])).encode())
        h.update(b"\x00")
    for link in sorted((l.a, l.b) for l in obs.links):
        h.update(repr(link).encode())
        h.update(b"\x00")
    return h.digest()
