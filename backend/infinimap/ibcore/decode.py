"""Decode the raw speed/width masks and MTU enum that the schema stores.

The schema stores speed and width as the masks the hardware reports and MTU as
the raw IBTA enum, because decoding is presentation and belongs where it can be
corrected without a migration. This is that decoder.

Two properties of the encoding matter the moment a mask with more than one bit
set is ranked -- which is exactly what the degraded check in `health.py` does:

  * **Width bits are not ordered by width.** 2X is bit 16, the highest bit but
    the second-narrowest width, so "take the highest set bit" picks 2X out of a
    4X|2X mask. Ranking here is by lane count.
  * **Speed masks are (ext << 8) | base and the extended half wins outright.**
    A port negotiated to HDR reports base QDR for backward compatibility, and
    may report base 0.
"""

from __future__ import annotations

# ---- widths --------------------------------------------------------------

_WIDTH_BIT_TO_LABEL: dict[int, str] = {1: "1X", 2: "4X", 4: "8X", 8: "12X", 16: "2X"}

# Lane count, which is both the rank and the multiplier in link_rate()
_WIDTH_LANES: dict[str, int] = {"1X": 1, "2X": 2, "4X": 4, "8X": 8, "12X": 12}

# ---- speeds --------------------------------------------------------------

_BASE_SPEED_BIT_TO_LABEL: dict[int, str] = {1: "SDR", 2: "DDR", 4: "QDR"}
_EXT_SPEED_BIT_TO_LABEL: dict[int, str] = {
    1: "FDR", 2: "EDR", 4: "HDR", 8: "NDR", 16: "XDR",
}

_SPEED_RANK: dict[str, int] = {
    "SDR": 1, "DDR": 2, "QDR": 3, "FDR": 4,
    "EDR": 5, "HDR": 6, "NDR": 7, "XDR": 8,
}

_GB_PER_LANE: dict[str, float] = {
    "SDR": 2.5, "DDR": 5.0, "QDR": 10.0, "FDR": 14.0625,
    "EDR": 25.78125, "HDR": 53.125, "NDR": 106.25, "XDR": 212.5,
}

# ---- mtu -----------------------------------------------------------------

_MTU_ENUM_TO_BYTES: dict[int, int] = {1: 256, 2: 512, 3: 1024, 4: 2048, 5: 4096}


# ---- decoding ------------------------------------------------------------

def width_labels(mask: int | None) -> list[str]:
    """Every width in the mask, narrowest first. Empty for 0/None."""
    if not mask:
        return []
    found = [lbl for bit, lbl in _WIDTH_BIT_TO_LABEL.items() if mask & bit]
    return sorted(found, key=lambda w: _WIDTH_LANES[w])


def speed_labels(mask: int | None) -> list[str]:
    """Every speed in the mask, slowest first. Extended speeds shadow base ones.

    The shadowing is not a tie-break but the encoding: a port running an
    extended speed still advertises a base speed for backward compatibility, so
    reporting both would claim the link supports a rate it is not offering.
    """
    if not mask:
        return []
    ext = [lbl for bit, lbl in _EXT_SPEED_BIT_TO_LABEL.items() if (mask >> 8) & bit]
    found = ext or [lbl for bit, lbl in _BASE_SPEED_BIT_TO_LABEL.items() if mask & bit]
    return sorted(found, key=lambda s: _SPEED_RANK[s])


def best_width(mask: int | None) -> str | None:
    """The widest width in the mask, by lane count. None for 0/None."""
    labels = width_labels(mask)
    return labels[-1] if labels else None


def best_speed(mask: int | None) -> str | None:
    """The fastest speed in the mask. None for 0/None."""
    labels = speed_labels(mask)
    return labels[-1] if labels else None


def width_rank(label: str | None) -> int:
    """Lane count, or 0 for an unknown label -- never raises, so an unexpected
    value from a future generation degrades to "cannot judge" rather than 500."""
    return _WIDTH_LANES.get(label or "", 0)


def speed_rank(label: str | None) -> int:
    """Position in the SDR..XDR ordering, or 0 for an unknown label"""
    return _SPEED_RANK.get(label or "", 0)


def mtu_bytes(enum: int | None) -> int | None:
    """Raw IBTA MTU enum 1-5 -> bytes. Anything else is None.

    002 requires 0 to be normalised to NULL at ingest, so a 0 arriving here
    means the constraint was bypassed; it decodes to None either way.
    """
    if enum is None:
        return None
    return _MTU_ENUM_TO_BYTES.get(enum)


def link_rate(width: str | None, speed: str | None) -> float | None:
    """Effective Gb/s: lanes x per-lane rate x line-encoding efficiency.

    SDR/DDR/QDR use 8b/10b, everything from FDR up uses 64b/66b. None if either
    half is unknown, since a rate computed from a guess is worse than no rate.
    """
    lanes = _WIDTH_LANES.get(width or "")
    per_lane = _GB_PER_LANE.get(speed or "")
    if lanes is None or per_lane is None:
        return None
    efficiency = 0.8 if speed in ("SDR", "DDR", "QDR") else 64 / 66
    return lanes * per_lane * efficiency
