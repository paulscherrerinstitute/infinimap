"""Collector configuration: where to write, what to call the fabric, how often.

Settings resolve flag, then environment, then TOML file, then built-in default;
__main__ applies the layers.
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, replace
from pathlib import Path

# Where __main__ looks when no --config is given. A file here is optional: if it
# is absent the built-in defaults below apply and CLI flags can still supply
# everything (handy for --from-dir testing with no file on disk).
DEFAULT_CONFIG_PATH = Path("/etc/infinimap/collector.toml")

_DEFAULT_DSN = "postgresql:///infinimap"
_DEFAULT_FABRIC = "default"
_DEFAULT_INTERVAL_S = 300.0
# Traffic is a separate sweep on a separate thread; see daemon.traffic_loop.
# 5s is what the volume figures in sql/003_counters.sql assume.
_DEFAULT_TRAFFIC_INTERVAL_S = 30.0


@dataclass(frozen=True)
class Config:
    dsn: str                       # libpq connection string for PostgreSQL
    fabric_name: str
    subnet_prefix: int | None
    interval_s: float              # topology + error cadence (loop mode)
    traffic_interval_s: float = _DEFAULT_TRAFFIC_INTERVAL_S
    
    from_dir: str | None = None    # For testing, collect from a directory of saquery fixture files instead of live

    @property
    def live(self) -> bool:
        return self.from_dir is None


def _coerce_prefix(raw: object) -> int | None:
    """Accept a hex/decimal string ("0xfe80...") or a plain int; blank -> None.

    Kept as a string in the file because a link-local prefix (0xfe80...) has its
    high bit set and overflows TOML's signed 64-bit integer range - writing it
    as a bare TOML integer would fail to parse. int(_, 0) infers the base from
    the literal, so both "0xfe80..." and "255" work.
    """
    if raw is None or raw == "":
        return None
    if isinstance(raw, bool):  # bool is an int subclass; a prefix is never one
        raise TypeError("subnet_prefix must be a string or int, not a bool")
    if isinstance(raw, int):
        return raw
    if isinstance(raw, str):
        return int(raw, 0)
    raise TypeError(f"subnet_prefix must be a string or int, got {type(raw).__name__}")


def from_mapping(data: dict[str, object]) -> Config:
    """Build a Config from a parsed TOML mapping, defaulting any absent key."""
    return Config(
        dsn=str(data.get("dsn", _DEFAULT_DSN)),
        fabric_name=str(data.get("fabric", _DEFAULT_FABRIC)),
        subnet_prefix=_coerce_prefix(data.get("subnet_prefix")),
        interval_s=float(data.get("interval_s", _DEFAULT_INTERVAL_S)),  # type: ignore[arg-type]
        traffic_interval_s=float(data.get("traffic_interval_s", _DEFAULT_TRAFFIC_INTERVAL_S)),  # type: ignore[arg-type]
        from_dir=(str(data["from_dir"]) if data.get("from_dir") else None),
    )


#: Environment variable -> Config field.
ENV_VARS = {
    "INFINIMAP_DSN": "dsn",
    "INFINIMAP_FABRIC": "fabric_name",
    "INFINIMAP_INTERVAL": "interval_s",
    "INFINIMAP_TRAFFIC_INTERVAL": "traffic_interval_s",
}


def from_env(base: Config | None = None, env: dict[str, str] | None = None) -> Config:
    """`base` with any INFINIMAP_* variable applied over it."""
    env = os.environ if env is None else env
    base = base if base is not None else defaults()

    overrides: dict[str, object] = {}
    for var, field in ENV_VARS.items():
        raw = env.get(var)
        if raw is None or raw == "":
            continue
        if field.endswith("_s"):
            try:
                overrides[field] = float(raw)
            except ValueError:
                raise ValueError(f"{var} must be a number, got {raw!r}") from None
        else:
            overrides[field] = raw
    return replace(base, **overrides) if overrides else base


def from_file(path: str | Path) -> Config:
    """Load Config from a TOML file. Raises FileNotFoundError if it is absent."""
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return from_mapping(data)


def defaults() -> Config:
    """The all-defaults Config, used when no file is present and flags fill the rest."""
    return from_mapping({})
