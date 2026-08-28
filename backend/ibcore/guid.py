"""GUID conversion between the database's signed BIGINT and the wire's hex string.

PostgreSQL has no unsigned 64-bit integer, so the schema stores the raw 64-bit
GUID pattern in a signed BIGINT: any GUID whose OUI has the high bit set
(0x88..., 0xd8...) is held as a negative number. The fold round-trips exactly,
but every value crossing the database boundary has to go through it, and every
value crossing the HTTP boundary has to become the 16-digit hex string the
InfiniBand world actually reads.

The schema also defines a SQL `guid_hex()` for the same job. Formatting happens
here so there is one implementation in one language; the SQL function stays for
humans reading the tables directly.
"""

from __future__ import annotations

from typing import overload

_U64 = 1 << 64
_I63 = 1 << 63

_HEX = frozenset("0123456789abcdef")

# All three converters are None-transparent: None in, None out. The overloads
# say so, which keeps a caller holding a NOT NULL column from having to cast
# away an Optional that cannot occur.


@overload
def signed(v: int) -> int: ...
@overload
def signed(v: None) -> None: ...
def signed(v: int | None) -> int | None:
    """Fold an unsigned 64-bit GUID into the signed BIGINT the schema stores"""
    if v is None:
        return None
    return v - _U64 if v >= _I63 else v


@overload
def unsigned(v: int) -> int: ...
@overload
def unsigned(v: None) -> None: ...
def unsigned(v: int | None) -> int | None:
    """The inverse, for GUIDs read back out"""
    if v is None:
        return None
    return v + _U64 if v < 0 else v


@overload
def guid_str(v: int) -> str: ...
@overload
def guid_str(v: None) -> None: ...
def guid_str(v: int | None) -> str | None:
    """A GUID of either signedness -> '0x' + 16 hex digits. None passes through.

    Returns None rather than a string sentinel: a JSON null is the honest
    encoding of an absent GUID, and every nullable GUID field in the contract is
    already typed to accept it.
    """
    if v is None:
        return None
    return f"0x{unsigned(v):016x}"


def parse_guid(s: str) -> int:
    """'0x0011223344556677' or bare hex -> the signed BIGINT to query with.

    Raises ValueError on anything that is not a 64-bit hex GUID, so a malformed
    path parameter becomes a 4xx instead of reaching the database.
    """
    t = s.strip().lower()
    if t.startswith("0x"):
        t = t[2:]
    if not t or len(t) > 16 or not set(t) <= _HEX:
        raise ValueError(f"not a 64-bit hex GUID: {s!r}")
    return signed(int(t, 16))
