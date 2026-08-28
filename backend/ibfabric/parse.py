"""Parse the 'ibdiagnet2.db_csv' file into a :class:`FabricGraph`.

The file is a set of 'START_<SECTION>' / 'END_<SECTION>' blocks, each with a
header row followed by comma-separated data rows.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

from .model import (
    Cable,
    FabricGraph,
    Link,
    GuidMappings,
    LinkCheckEvent,
    Node,
    NodeType,
    Port,
    PortCounters,
    PortState,
    SmInfo,
    Switch,
)

from .vendor_device_parse import resolve

Section = tuple[list[str], list[list[str]]]  # (header, data rows)


# -------------------------------------------------------------------


def _to_int(value: str | None) -> int | None:
    """Coerce a db_csv cell to int, or None for N/A-style values.

    Handles ``0x…`` hex and plain decimals. Empty / ``N/A`` become None.
    """
    if value is None:
        return None
    v = value.strip().strip('"')
    if v == "" or v.upper() == "N/A":
        return None
    try:
        return int(v, 16) if v.lower().startswith("0x") else int(v)
    except ValueError:
        return None


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    v = value.strip().strip('"').strip()
    return v or None


def read_sections(path: str | Path) -> dict[str, Section]:
    """Split the file into '{SECTION_NAME: (header, rows)}'.

    Sections whose header ends in 'Summary' (the ERRORS_*/WARNINGS_* blocks) can
    contain unescaped quotes inside that last field, which breaks a normal CSV
    reader - those are split by a fixed number of leading commas instead.
    """
    sections: dict[str, Section] = {}
    name: str | None = None
    header: list[str] | None = None
    rows: list[list[str]] = []

    for raw_line in Path(path).read_text(errors="replace").splitlines():
        line = raw_line.rstrip("\n")
        if line.startswith("START_"):
            name, header, rows = line[len("START_"):], None, []
        elif line.startswith("END_"):
            if name is not None and header is not None:
                sections[name] = (header, rows)
            name = header = None
            rows = []
        elif name is not None:
            if not line.strip():
                continue
            if header is None:
                header = _split(name, line, None)
            else:
                rows.append(_split(name, line, len(header)))
    return sections


def _split(section: str, line: str, ncols: int | None) -> list[str]:
    """Split one line into fields.

    'ncols is None' means this is a header line - always well-formed, parse with
    a real CSV reader. For data lines in 'Summary' sections, keep the last
    column verbatim (it may contain unescaped quotes/commas); everything else goes
    through the CSV reader.
    """
    if ncols is None:
        return [f.strip() for f in next(csv.reader(io.StringIO(line)))]
    if _is_summary_section(section):
        parts = line.split(",", ncols - 1)
        return [p.strip() for p in parts] + [""] * (ncols - len(parts))
    fields = next(csv.reader(io.StringIO(line)))
    if len(fields) < ncols:
        fields += [""] * (ncols - len(fields))
    return fields


_SUMMARY_SECTIONS = {
    "ERRORS_LINKS_SPEED_CHECK",
    "ERRORS_LINKS_WIDTH_CHECK",
    "WARNINGS_FW_CHECK",
}


LINK_WIDTHS: dict[int, str] = {
    1: "1X", 2: "4X", 4: "8X", 8: "12X", 16: "2X",
}

BASE_SPEEDS: dict[int, str] = {1: "SDR", 2: "DDR", 4: "QDR"}

EXT_SPEEDS: dict[int, str] = {
    1: "FDR", 2: "EDR", 4: "HDR", 8: "NDR", 16: "XDR",
}

GB_PER_LANE: dict[str, float] = {
    "SDR": 2.5, "DDR": 5.0, "QDR": 10.0,
    "FDR": 14.0625, "EDR": 25.78125, "HDR": 53.125,
    "NDR": 106.25, "XDR": 212.5,
}


def parse_width(mask: int) -> list[str]:
    return [name for bit, name in LINK_WIDTHS.items() if mask & bit]


def parse_speed(value: int) -> list[str]:
    """Decode ibdiagnet2 PORTS LinkSpeed* - layout is (ext_mask << 8) | base_mask.
    
    Extended speeds take precedence: a port negotiated to HDR reports base QDR
    for backward compatibility, and may report base 0. Returns [] for down or
    unpopulated ports (value 0).
    """
    if ext := [name for bit, name in EXT_SPEEDS.items() if (value >> 8) & bit]:
        return ext
    return [name for bit, name in BASE_SPEEDS.items() if value & bit]


def active_speed(value: int) -> str | None:
    """Single negotiated speed, or None if the port is down."""
    speeds = parse_speed(value)
    return speeds[-1] if speeds else None

def active_width(mask: int) -> str | None:
    """Single negotiated width, or None if the port is down."""
    widths = parse_width(mask)
    return widths[-1] if widths else None

def link_rate(width: str, speed: str) -> float:
    """Effective Gb/s: lanes x per-lane rate x line-encoding efficiency."""
    lanes = int(width.rstrip("Xx"))
    efficiency = 0.8 if speed in ("SDR", "DDR", "QDR") else 64 / 66 # ratio of payload bits to bits actually transmitted on the wire
    return lanes * GB_PER_LANE[speed] * efficiency



def _is_summary_section(section: str) -> bool:
    return section in _SUMMARY_SECTIONS or (
        section.startswith(("ERRORS_", "WARNINGS_")) and section.endswith("_CHECK")
    )


def _rows_as_dicts(section: Section) -> list[dict[str, str]]:
    header, rows = section
    return [dict(zip(header, r)) for r in rows]


def load_guid_mappings(filename: str = "id_to_name.json") -> GuidMappings | None:
    path = Path(__file__).parent / filename
    try:
        with open(path) as f:
            raw = json.load(f)
    except FileNotFoundError:
        return None
    
    return GuidMappings(
        system_image_guid={
            ik: v
            for k, v in raw["systemImageGuid"].items()
            if (ik := _to_int(k)) is not None
        },
        node_guid={
            ik: v
            for k, v in raw["nodeGuid"].items()
            if (ik := _to_int(k)) is not None
        },
    )

# --------------------------------------------------------------- build entities


def build_graph(path: str | Path) -> FabricGraph:
    sections = read_sections(path)
    g = FabricGraph(source_path=str(path))

    guid_mappings = load_guid_mappings()

    _fill_provenance(g, sections)
    _fill_nodes(g, sections, guid_mappings.node_guid if guid_mappings is not None else None)
    _fill_ports(g, sections)
    _fill_counters(g, sections)
    _fill_cables(g, sections)
    _fill_switches(g, sections)
    _fill_sm(g, sections)
    _fill_check_events(g, sections)
    _fill_links(g, sections)
    return g


def _fill_provenance(g: FabricGraph, s: dict[str, Section]) -> None:
    for row in _rows_as_dicts(s.get("RUN_INFO", ([], []))):
        g.run_timestamp = _clean(row.get("Date"))
        g.args = _clean(row.get("Args"))
        g.versions = {
            "ibdiagnet": _clean(row.get("IBDIAGNET_Version")) or "",
            "ibdiag": _clean(row.get("IBDIAG_Version")) or "",
            "ibdm": _clean(row.get("IBDM_Version")) or "",
            "ibis": _clean(row.get("IBIS_Version")) or "",
        }
        break


def _fill_nodes(g: FabricGraph, s: dict[str, Section], guid_to_name: dict[int, str] | None = None) -> None:
    for row in _rows_as_dicts(s.get("NODES", ([], []))):
        guid = _to_int(row.get("NodeGUID"))
        system_image_guid = _to_int(row.get("SystemImageGUID"))
        if guid is None:
            continue
        if system_image_guid is not None:
            g.system_groups.setdefault(system_image_guid, []).append(guid)
        
        desc = _clean(row.get("NodeDesc")) or ""
        if guid_to_name is not None:
            desc = guid_to_name.get(guid, desc)
            mapped_name = guid_to_name.get(guid)
            if mapped_name is not None:
                desc = mapped_name
        
        device_id = _to_int(row.get("DeviceID")) or 0
        vendor_id = _to_int(row.get("VendorID")) or 0
        
        g.nodes[guid] = Node(
            guid=guid,
            desc=desc,
            node_type=NodeType(_to_int(row.get("NodeType")) or 1),
            num_ports=_to_int(row.get("NumPorts")),
            system_image_guid=system_image_guid,
            device_id=device_id,
            vendor_id=vendor_id,
            model=resolve(vendor_id, device_id).model,
            vendor=resolve(vendor_id, device_id).vendor,
        )
    
    # Cleanup system groups to remove any singe-entry elements
    g.system_groups = {k: v for k, v in g.system_groups.items() if len(v) > 1}
    
    
    # firmware version comes from the FW-check warnings (best-effort)
    for row in _rows_as_dicts(s.get("WARNINGS_FW_CHECK", ([], []))):
        guid = _to_int(row.get("NodeGUID"))
        node = g.nodes.get(guid) if guid is not None else None
        if node and "FW version" in (row.get("Summary") or ""):
            node.fw_version = (row["Summary"].split("FW version", 1)[1]
                               .strip().split()[0])


def _fill_ports(g: FabricGraph, s: dict[str, Section]) -> None:
    for row in _rows_as_dicts(s.get("PORTS", ([], []))):
        guid = _to_int(row.get("NodeGuid"))
        pnum = _to_int(row.get("PortNum"))
        if guid is None or pnum is None:
            continue
        node = g.nodes.get(guid)
        if node is None:
            continue
        
        speed_active = active_speed(_to_int(row.get("LinkSpeedActv")) or 0)
        width_active = active_width(_to_int(row.get("LinkWidthActv")) or 0)
        
        node.ports[pnum] = Port(
            node_guid=guid,
            port_num=pnum,
            port_guid=_to_int(row.get("PortGuid")),
            lid=_to_int(row.get("LID")),
            state=PortState(_to_int(row.get("PortState")) or 0),
            phys_state=_to_int(row.get("PortPhyState")),
            width_active=width_active,
            width_supported=parse_width(_to_int(row.get("LinkWidthSup")) or 0),
            width_enabled=parse_width(_to_int(row.get("LinkWidthEn")) or 0),
            speed_active=speed_active,
            speed_enabled=parse_speed(_to_int(row.get("LinkSpeedEn")) or 0),
            flow_rate=link_rate(width_active, speed_active) if width_active and speed_active else None,
            fec_active=_to_int(row.get("FECActv")),
            mtu=_to_int(row.get("NMTU")),
        )


_COUNTER_MAP = {
    "symbol_error": "SymbolErrorCounter",
    "link_downed": "LinkDownedCounter",
    "link_error_recovery": "LinkErrorRecoveryCounter",
    "port_rcv_errors": "PortRcvErrors",
    "port_xmit_discards": "PortXmitDiscards",
    "port_rcv_remote_physical_errors": "PortRcvRemotePhysicalErrors",
    "local_link_integrity_errors": "LocalLinkIntegrityErrors",
    "excessive_buffer_overrun_errors": "ExcessiveBufferOverrunErrors",
    "vl15_dropped": "VL15Dropped",
    "port_xmit_data": "PortXmitData",
    "port_rcv_data": "PortRcvData",
    "port_xmit_pkts": "PortXmitPkts",
    "port_rcv_pkts": "PortRcvPkts",
    "port_xmit_wait": "PortXmitWait",
    "retransmission_per_sec": "retransmission_per_sec",
}


def _counters_from_row(row: dict[str, str]) -> PortCounters:
    named = {attr: _to_int(row.get(col)) for attr, col in _COUNTER_MAP.items()}
    raw = {k: _to_int(v) for k, v in row.items()}
    return PortCounters(raw=raw, **named)


def _fill_counters(g: FabricGraph, s: dict[str, Section]) -> None:
    for section, attr in (("PM_INFO", "counters"), ("PM_DELTA", "counters_delta")):
        for row in _rows_as_dicts(s.get(section, ([], []))):
            port = g.port(_to_int(row.get("NodeGUID")), _to_int(row.get("PortNumber")))
            if port is not None:
                setattr(port, attr, _counters_from_row(row))


def _fill_cables(g: FabricGraph, s: dict[str, Section]) -> None:
    for row in _rows_as_dicts(s.get("CABLE_INFO", ([], []))):
        guid = _to_int(row.get("NodeGuid"))
        pnum = _to_int(row.get("PortNum"))
        if guid is None or pnum is None:
            continue
        port = g.port(guid, pnum)
        if port is None:
            continue
        port.cable = Cable(
            node_guid=guid,
            port_num=pnum,
            vendor=_clean(row.get("Vendor")),
            pn=_clean(row.get("PN")),
            sn=_clean(row.get("SN")),
            rev=_clean(row.get("Rev")),
            length_desc=_clean(row.get("LengthDesc")),
            type_desc=_clean(row.get("TypeDesc")),
            supported_speed=_clean(row.get("SupportedSpeed")),
            nominal_bitrate=_clean(row.get("NominalBitrate")),
            temperature=_clean(row.get("Temperature")),
            raw={k: v for k, v in row.items()},
        )


def _fill_switches(g: FabricGraph, s: dict[str, Section]) -> None:
    for row in _rows_as_dicts(s.get("SWITCHES", ([], []))):
        guid = _to_int(row.get("NodeGUID"))
        if guid is not None:
            g.switches[guid] = Switch(guid=guid, raw={k: _to_int(v) for k, v in row.items()})


def _fill_sm(g: FabricGraph, s: dict[str, Section]) -> None:
    for row in _rows_as_dicts(s.get("SM_INFO", ([], []))):
        guid = _to_int(row.get("NodeGUID"))
        if guid is not None:
            g.sm.append(SmInfo(
                guid=guid,
                port=_to_int(row.get("PortNumber")),
                priority=_to_int(row.get("Priority")),
                sm_state=_to_int(row.get("SmState")),
            ))


def _fill_check_events(g: FabricGraph, s: dict[str, Section]) -> None:
    for section in ("ERRORS_LINKS_SPEED_CHECK", "ERRORS_LINKS_WIDTH_CHECK"):
        for row in _rows_as_dicts(s.get(section, ([], []))):
            guid = _to_int(row.get("NodeGUID"))
            pnum = _to_int(row.get("PortNumber"))
            event = LinkCheckEvent(
                scope=_clean(row.get("Scope")) or "",
                node_guid=guid,
                port_num=pnum,
                event_name=_clean(row.get("EventName")) or "",
                summary=_clean(row.get("Summary")) or "",
            )
            g.check_events.append(event)
            port = g.port(guid, pnum)
            if port is not None:
                port.check_events.append(event.event_name)


def _fill_links(g: FabricGraph, s: dict[str, Section]) -> None:
    for row in _rows_as_dicts(s.get("LINKS", ([], []))):
        a = g.port(_to_int(row.get("NodeGuid1")), _to_int(row.get("PortNum1")))
        b = g.port(_to_int(row.get("NodeGuid2")), _to_int(row.get("PortNum2")))
        if a is not None and b is not None:
            link = Link(a=a, b=b)
            g.links.append(link)
            g.adjacency.setdefault(a.node_guid, []).append(link)
            g.adjacency.setdefault(b.node_guid, []).append(link)
