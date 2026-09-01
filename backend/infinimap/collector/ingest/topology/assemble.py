"""Fold the three parsed block-lists into one Observation.

This is where LID -- the only key the three saquery outputs share -- is turned
into the GUID-based identity the schema stores. NodeRecord provides the
LID -> node_guid map; PortInfoRecord and LinkRecord are keyed by LID and resolved
through it. An endpoint that fails to resolve does not corrupt the result: it is
dropped and the sweep is marked incomplete, so the writer refuses to diff it.
"""

from __future__ import annotations

import dataclasses

from . import encode
from ...observation import (
    Observation,
    ObservedLink,
    ObservedNode,
    ObservedPort,
    PortKey,
)
from .parse import (
    _to_int,
    parse_link_records,
    parse_node_records,
    parse_port_infos,
    parse_sm_infos,
)


def build_observation(
    node_text: str,
    port_text: str,
    link_text: str,
    collected_at: str,
    *,
    already_incomplete: bool = False,
    sm_text: str | None = None,
) -> Observation:
    """Assemble an Observation from the raw saquery texts.

    ``already_incomplete`` lets the caller pre-flag a sweep whose queries failed
    at the source (nonzero exit, stderr output) before parsing even begins.

    ``sm_text`` is optional and on its own failure domain: None means the SM was
    not read, which sets ``sm_seen`` and leaves ``complete`` alone.
    """
    obs = Observation(collected_at=collected_at)
    if already_incomplete:
        obs.mark_incomplete("one or more saquery invocations reported an error")

    node_blocks, n_drop = parse_node_records(node_text)
    port_blocks, p_drop = parse_port_infos(port_text)
    link_blocks, l_drop = parse_link_records(link_text)
    for label, n in (("NodeRecord", n_drop), ("PortInfoRecord", p_drop),
                     ("LinkRecord", l_drop)):
        if n:
            obs.mark_incomplete(f"{n} malformed {label} block(s) dropped")

    lid_to_guid: dict[int, int] = {}
    # (node_guid, port_number) -> port_guid, populated only for CA ports, which
    # are the only ports NodeRecord reports a PortGUID for. Switch data ports have
    # none; the schema stores NULL there.
    ca_port_guid: dict[PortKey, int] = {}

    # -- nodes + the LID map ------------------------------------------------
    for b in node_blocks:
        guid = _to_int(b.get("node_guid"))
        lid = _to_int(b.get("lid"))
        if guid is None or lid is None:
            obs.mark_incomplete("NodeRecord with unparseable guid/lid")
            continue
        lid_to_guid[lid] = guid
        ntype = encode.node_type(b.get("node_type"))
        obs.nodes[guid] = ObservedNode(
            guid=guid,
            node_type=ntype,
            sys_image_guid=_to_int(b.get("sys_guid")),
            vendor_id=_to_int(b.get("vendor_id")),
            device_id=_to_int(b.get("device_id")),
            num_ports=_to_int(b.get("num_ports")),
            desc=(b.get("NodeDescription") or "").strip() or None,
        )
        port_guid = _to_int(b.get("port_guid"))
        port_num = _to_int(b.get("port_num"))
        if ntype == "ca" and port_guid is not None and port_num is not None:
            ca_port_guid[(guid, port_num)] = port_guid

    # -- ports --------------------------------------------------------------
    for b in port_blocks:
        end_lid = _to_int(b.get("EndPortLid"))
        pnum = _to_int(b.get("PortNum"))
        if end_lid is None or pnum is None:
            obs.mark_incomplete("PortInfoRecord with unparseable RID")
            continue
        guid = lid_to_guid.get(end_lid)
        if guid is None:
            obs.mark_incomplete(f"PortInfoRecord EndPortLid {end_lid} resolves to no node")
            continue
        key = (guid, pnum)
        obs.ports[key] = ObservedPort(
            node_guid=guid,
            port_number=pnum,
            port_guid=ca_port_guid.get(key),
            log_state=encode.log_state(b.get("LinkState")),
            phys_state=encode.phys_state(b.get("PhysLinkState")),
            active_speed=encode.speed_mask(b.get("LinkSpeedActive"), b.get("LinkSpeedExtActive")),
            active_width=encode.width_mask(b.get("LinkWidthActive")),
            enabled_speed=encode.speed_mask(b.get("LinkSpeedEnabled"), b.get("LinkSpeedExtEnabled")),
            enabled_width=encode.width_mask(b.get("LinkWidthEnabled")),
            supported_speed=encode.speed_mask(b.get("LinkSpeedSupported"), b.get("LinkSpeedExtSupported")),
            supported_width=encode.width_mask(b.get("LinkWidthSupported")),
            lid=_to_int(b.get("Lid")) if b.get("Lid") else end_lid,
            mtu=encode.mtu_enum(b.get("NeighborMTU")),
        )

    # -- links --------------------------------------------------------------
    for b in link_blocks:
        from_lid = _to_int(b.get("FromLID"))
        from_port = _to_int(b.get("FromPort"))
        to_lid = _to_int(b.get("ToLID"))
        to_port = _to_int(b.get("ToPort"))
        if from_lid is None or from_port is None or to_lid is None or to_port is None:
            obs.mark_incomplete("LinkRecord with unparseable endpoint")
            continue
        a_guid = lid_to_guid.get(from_lid)
        b_guid = lid_to_guid.get(to_lid)
        if a_guid is None or b_guid is None:
            obs.mark_incomplete(f"LinkRecord LID {from_lid}<->{to_lid} resolves to no node")
            continue
        a: PortKey = (a_guid, from_port)
        bk: PortKey = (b_guid, to_port)
        # A link whose endpoint has no port row cannot be written: the writer has
        # no port_id to reference. Skip it and flag the sweep.
        if a not in obs.ports or bk not in obs.ports:
            obs.mark_incomplete(f"LinkRecord endpoint {a} or {bk} has no PortInfo")
            continue
        obs.links.add(ObservedLink.canonical(a, bk))

    # -- subnet managers ----------------------------------------------------
    _apply_sm_info(obs, sm_text, lid_to_guid)

    return obs


def _apply_sm_info(obs: Observation, sm_text: str | None,
                   lid_to_guid: dict[int, int]) -> None:
    """Fold SMInfoRecord into the nodes already assembled."""
    if sm_text is None:
        obs.sm_seen = False
        return

    blocks, dropped = parse_sm_infos(sm_text)
    if dropped:
        obs.sm_seen = False
        obs.problems.append(f"{dropped} malformed SMInfoRecord block(s) dropped")

    for b in blocks:
        lid = _to_int(b.get("LID"))
        if lid is None:
            obs.sm_seen = False
            obs.problems.append("SMInfoRecord with unparseable LID")
            continue
        guid = lid_to_guid.get(lid)
        if guid is None or guid not in obs.nodes:
            obs.sm_seen = False
            obs.problems.append(f"SMInfoRecord LID {lid} resolves to no node")
            continue
        obs.nodes[guid] = dataclasses.replace(
            obs.nodes[guid], sm_state=encode.sm_state(b.get("SMState"))
        )
