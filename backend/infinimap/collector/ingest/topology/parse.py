"""Parse the three saquery text outputs into raw field maps.

saquery prints ``<Record> dump:`` blocks, each a run of ``name....value`` lines.
This module does only the mechanical text -> ``dict[str, str]`` split; decoding
those dicts into Observation entities is assemble.py's job, which keeps the
fiddly text handling testable in isolation.

Every parser returns ``(records, dropped)``, where ``dropped`` counts blocks
missing a required key -- the signal assemble.py turns into ``complete=False``.
"""

from __future__ import annotations

import re

# name, optional trailing colon, a run of >=2 dots, then the value (rest of line).
# The colon is optional because RID fields print `EndPortLid....` while PortInfo
# fields print `LinkState:....`. Lines without a dot run -- capability flags like
# `IsTrapSupported`, sub-headers like `RID` -- simply do not match and are skipped.
_FIELD = re.compile(r"^\s*([A-Za-z0-9_]+):?\.{2,}\s*(.*?)\s*$")


def _to_int(value: str | None) -> int | None:
    """Coerce a saquery cell to int; hex (0x..) or decimal. Junk -> None."""
    if value is None:
        return None
    v = value.strip().strip('"')
    if not v or v.upper() == "N/A":
        return None
    try:
        return int(v, 16) if v.lower().startswith("0x") else int(v)
    except ValueError:
        return None


def read_blocks(text: str, header: str) -> list[dict[str, str]]:
    """Split ``text`` into per-block field maps, one per ``<header>`` line."""
    blocks: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    for line in text.splitlines():
        if line.strip() == header:
            current = {}
            blocks.append(current)
            continue
        if current is None:
            continue
        m = _FIELD.match(line)
        if m:
            key, val = m.group(1), m.group(2)
            current.setdefault(key, val)
    return blocks


def _extract(
    text: str, header: str, required: tuple[str, ...]
) -> tuple[list[dict[str, str]], int]:
    """read_blocks, then drop any block missing a required key."""
    kept: list[dict[str, str]] = []
    dropped = 0
    for block in read_blocks(text, header):
        if all(k in block and block[k] != "" for k in required):
            kept.append(block)
        else:
            dropped += 1
    return kept, dropped


def parse_node_records(text: str) -> tuple[list[dict[str, str]], int]:
    """NodeRecord blocks. Require node_guid + lid (the LID->GUID map depends on both)."""
    return _extract(text, "NodeRecord dump:", ("node_guid", "lid"))


def parse_port_infos(text: str) -> tuple[list[dict[str, str]], int]:
    """PortInfoRecord blocks. Require the RID (EndPortLid + PortNum) that ties the
    port to a node and distinguishes switch ports."""
    return _extract(text, "PortInfoRecord dump:", ("EndPortLid", "PortNum"))


def parse_link_records(text: str) -> tuple[list[dict[str, str]], int]:
    """LinkRecord blocks. Require both endpoints."""
    return _extract(
        text, "LinkRecord dump:", ("FromLID", "FromPort", "ToLID", "ToPort")
    )


def parse_sm_infos(text: str) -> tuple[list[dict[str, str]], int]:
    """SMInfoRecord blocks. Require the RID LID -- the only field that ties an SM
    to a node -- and SMState, the only one we cannot sensibly default."""
    return _extract(text, "SMInfoRecord dump:", ("LID", "SMState"))
