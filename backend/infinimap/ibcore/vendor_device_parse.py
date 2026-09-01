"""Vendor and device name resolution from IB NodeInfo identifiers.

NodeInfo carries an IEEE OUI (VendorID) and a vendor-defined DeviceID.
Mellanox/NVIDIA populate DeviceID with the PCI device ID, so device names
can be resolved from pci.ids -- but that file is keyed on PCI vendor ID,
so a hand-maintained OUI -> PCI vendor bridge is required.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
import sys

_HERE = Path(__file__).parent

BRIDGE_PATH = _HERE / "oui_to_pci.json"
VENDORED_PCI_IDS = _HERE / "data" / "pci.ids"
SYSTEM_PCI_IDS = (
    Path("/usr/share/hwdata/pci.ids"),
    Path("/usr/share/misc/pci.ids"),
)

_VENDOR_LINE = re.compile(r"^([0-9a-fA-F]{4})\s\s+(.+?)\s*$")
_DEVICE_LINE = re.compile(r"^\t([0-9a-fA-F]{4})\s\s+(.+?)\s*$")
_BRACKETED = re.compile(r"\[([^\]]+)\]")


@dataclass(frozen=True)
class VendorTables:
    """OUI -> PCI vendor bridge plus OUI display names."""

    oui_to_pci: dict[int, int]
    oui_names: dict[int, str]


@dataclass(frozen=True)
class PciIds:
    """Parsed pci.ids: vendor names and (vendor, device) -> device name."""

    vendors: dict[int, str]
    devices: dict[tuple[int, int], str]
    source: Path | None


def _pci_ids_path() -> Path | None:
    for candidate in SYSTEM_PCI_IDS:
        if candidate.is_file():
            return candidate
    return VENDORED_PCI_IDS if VENDORED_PCI_IDS.is_file() else None


@lru_cache(maxsize=1)
def load_bridge(path: Path = BRIDGE_PATH) -> VendorTables:
    if not path.is_file():
        return VendorTables(oui_to_pci={}, oui_names={})

    raw = json.loads(path.read_text(encoding="utf-8"))
    return VendorTables(
        oui_to_pci={int(k): int(v) for k, v in raw.get("oui_to_pci_vendor", {}).items()},
        oui_names={int(k): str(v) for k, v in raw.get("oui_names", {}).items()},
    )


@lru_cache(maxsize=1)
def load_pci_ids(path: Path | None = None) -> PciIds:
    if path is None:
        path = _pci_ids_path()
    if path is None:
        print("warning: no pci.ids found, device names will be missing", file=sys.stderr)
        return PciIds(vendors={}, devices={}, source=None)

    vendors: dict[int, str] = {}
    devices: dict[tuple[int, int], str] = {}
    current: int | None = None

    with path.open(encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            if line.startswith("\t\t"):      # subsystem line, ignore
                continue
            if m := _DEVICE_LINE.match(line):
                if current is not None:
                    devices[(current, int(m.group(1), 16))] = m.group(2)
                continue
            if m := _VENDOR_LINE.match(line):
                current = int(m.group(1), 16)
                vendors[current] = m.group(2)
                continue
            current = None                   # class/other section, reset
    
    print('loaded pci.ids from', path, f"({len(vendors):,} vendors, {len(devices):,} devices)")
    return PciIds(vendors=vendors, devices=devices, source=path)


def _strip_family(raw: str) -> str:
    """'MT27700 Family [ConnectX-4]' -> 'ConnectX-4'"""
    return m.group(1) if (m := _BRACKETED.search(raw)) else raw


def vendor_name(vendor_id: int | None) -> str | None:
    """Resolve an IB OUI to a display name."""
    if vendor_id is None:
        return None

    bridge = load_bridge()
    if name := bridge.oui_names.get(vendor_id):
        return name

    if pci_vendor := bridge.oui_to_pci.get(vendor_id):
        if name := load_pci_ids().vendors.get(pci_vendor):
            return name

    return f"0x{vendor_id:06X}"


def device_name(device_id: int | None, vendor_id: int | None) -> str | None:
    """Resolve an IB (OUI, DeviceID) pair to a model name."""
    
    if device_id is None or vendor_id is None:
        return None

    bridge = load_bridge()
    if pci_vendor := bridge.oui_to_pci.get(vendor_id):
        if raw := load_pci_ids().devices.get((pci_vendor, device_id)):
            return _strip_family(raw)

    if name := bridge.oui_names.get(vendor_id):
        return f"{name} (0x{device_id:04X})"

    return f"0x{vendor_id:06X} / 0x{device_id:04X}"


@dataclass(frozen=True)
class DeviceInfo:
    vendor_id: int
    device_id: int
    vendor: str
    model: str


def resolve(vendor_id: int, device_id: int) -> DeviceInfo:
    return DeviceInfo(
        vendor_id=vendor_id,
        device_id=device_id,
        vendor=vendor_name(vendor_id) or "",
        model=device_name(device_id, vendor_id) or "",
    )