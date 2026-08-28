"""Turn saquery's decoded text back into the raw codes the schema stores.

saquery prints human labels (4X, 25.78125 Gbps, 4096, Active, whereas the database 
stores raw codes: speed/width as bit masks, MTU as the IBTA enum 1-5, link/phys 
state as small enums.
"""

from __future__ import annotations

# --------------------------------------------------------------------- widths
#
# Bit values follow the ibdiagnet convention.
# Note 4X is bit 2 and 2X is bit 16.

_WIDTH_LABEL_TO_BIT: dict[str, int] = {
    "1X": 1,
    "4X": 2,
    "8X": 4,
    "12X": 8,
    "2X": 16,
}


# --------------------------------------------------------------------- speeds
#
# saquery reports speed as Gbps-per-lane strings, split across a base field and
# an extended field. We map each string to its bit.

_BASE_SPEED_LABEL_TO_BIT: dict[str, int] = {
    "2.5 Gbps": 1,   # SDR
    "5.0 Gbps": 2,   # DDR
    "10.0 Gbps": 4,  # QDR
}
_EXT_SPEED_LABEL_TO_BIT: dict[str, int] = {
    "14.0625 Gbps": 1,   # FDR
    "25.78125 Gbps": 2,  # EDR
    "53.125 Gbps": 4,    # HDR
    "106.25 Gbps": 8,    # NDR
    "212.5 Gbps": 16,    # XDR
}

# Sentinels saquery emits where a field has no value
_EMPTY_LABELS = frozenset({
    "",
    "no extended speed",
    "no extended speed 2",
    "extended speed",  # LinkSpeedActive says this when the real value is in the Ext field
})


def _mask_from_set(label: str | None, label_to_bit: dict[str, int]) -> int:
    """OR together the bits named in an ``A or B or C`` saquery set string."""
    if not label:
        return 0
    mask = 0
    for part in label.split(" or "):
        p = part.strip()
        if p.lower() in _EMPTY_LABELS or p.startswith("undefined"):
            continue
        mask |= label_to_bit.get(p, 0)
    return mask


def width_mask(label: str | None) -> int:
    """``'1X or 4X'`` -> 17. ``'undefined (0)'`` / unknown -> 0."""
    return _mask_from_set(label, _WIDTH_LABEL_TO_BIT)


def speed_mask(base_label: str | None, ext_label: str | None) -> int:
    """Assemble ``(ext << 8) | base`` from the two saquery speed fields.

    Handles both the single-value active fields and the ``A or B``-joined
    enabled/supported sets. An active port running an extended speed reports
    ``'Extended speed'`` in the base field (mask 0) and its real rate in the
    extended field; a base-only port reports its rate in the base field and
    ``'No Extended Speed'`` in the extended one.
    """
    base = _mask_from_set(base_label, _BASE_SPEED_LABEL_TO_BIT)
    ext = _mask_from_set(ext_label, _EXT_SPEED_LABEL_TO_BIT)
    return (ext << 8) | base


# ------------------------------------------------------------------------ mtu
#
# saquery prints MTU decoded to bytes; the schema stores the raw IBTA enum.

_MTU_BYTES_TO_ENUM: dict[int, int] = {
    256: 1,
    512: 2,
    1024: 3,
    2048: 4,
    4096: 5,
}


def mtu_enum(label: str | None) -> int | None:
    if not label:
        return None
    digits = "".join(c for c in label if c.isdigit())
    if not digits:
        return None
    return _MTU_BYTES_TO_ENUM.get(int(digits))


# --------------------------------------------------------------------- states
#
# standardized labels for the small enums the schema stores. The saquery output is inconsistent

_LOG_STATE: dict[str, str] = {
    "down": "down",
    "init": "init",
    "initialize": "init",
    "armed": "armed",
    "active": "active",
}
_PHYS_STATE: dict[str, str] = {
    "sleep": "sleep",
    "polling": "polling",
    "disabled": "disabled",
    "training": "training",
    "portconfigurationtraining": "training",
    "linkup": "linkup",
    "linkerrorrecovery": "link_error_recovery",
    "link_error_recovery": "link_error_recovery",
    "phytest": "phy_test",
    "phy_test": "phy_test",
}
_NODE_TYPE: dict[str, str] = {
    "channel adapter": "ca",
    "ca": "ca",
    "switch": "switch",
    "router": "router",
}

# IBTA SMInfo.SMState
_SM_STATE: dict[str, str] = {
    "0": "none",          # not active
    "1": "discovering",
    "2": "standby",
    "3": "master",
}


def _norm(label: str | None) -> str:
    return (label or "").strip().lower()


def log_state(label: str | None) -> str | None:
    return _LOG_STATE.get(_norm(label))


def phys_state(label: str | None) -> str | None:
    return _PHYS_STATE.get(_norm(label))


def node_type(label: str | None) -> str | None:
    return _NODE_TYPE.get(_norm(label))


def sm_state(label: str | None) -> str:
    """``'3'`` -> ``'master'``. Anything unrecognised -> ``'none'``."""
    return _SM_STATE.get(_norm(label), "none")
