"""Config loading for the API.

Settings resolve flag, then environment, then config file, then built-in
default; __main__ applies the layers. This module supplies the file and
environment halves.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
import tomllib

DEFAULT_CONFIG_PATH = Path("/etc/infinimap/api.toml")

# Built in defaults
@dataclass(frozen=True)
class Config:
    dsn: str = "postgresql:///infinimap"
    host: str = "127.0.0.1"
    port: int = 8000                     # HTTP listen port, not an IB port
    default_fabric: str = "default"

    # Vite's dev server
    cors_origins: tuple[str, ...] = ("http://localhost:5173",
                                     "http://127.0.0.1:5173")

    pool_min: int = 1
    pool_max: int = 8

    # Display-name overrides
    id_to_name_path: Path | None = None

    # Built frontend to serve at `/`. None means look for one: bundled in the
    # package first, then frontend/dist in a source checkout. See api/web.py.
    web_root: Path | None = None


def _coerce_origins(raw: object) -> tuple[str, ...]:
    """Accept a TOML array or a single string;"""
    if isinstance(raw, str):
        return (raw,)
    if isinstance(raw, (list, tuple)):
        if not all(isinstance(o, str) for o in raw):
            raise TypeError("cors_origins must contain only strings")
        return tuple(raw)
    raise TypeError(f"cors_origins must be a string or array, got {type(raw).__name__}")


def _coerce_path(raw: object) -> Path | None:
    if raw is None:
        return None
    if not isinstance(raw, str):
        raise TypeError(f"path must be a string, got {type(raw).__name__}")
    return Path(raw)


def from_mapping(data: dict[str, object]) -> Config:
    """Build a Config from a parsed TOML mapping, defaulting every key."""
    d = Config()  # built-in defaults, used for any key the file omits
    return Config(
        dsn=str(data.get("dsn", d.dsn)),
        host=str(data.get("host", d.host)),
        port=int(data.get("port", d.port)),  # type: ignore[arg-type]
        default_fabric=str(data.get("fabric", d.default_fabric)),
        cors_origins=_coerce_origins(data.get("cors_origins", d.cors_origins)),
        pool_min=int(data.get("pool_min", d.pool_min)),  # type: ignore[arg-type]
        pool_max=int(data.get("pool_max", d.pool_max)),  # type: ignore[arg-type]
        id_to_name_path=_coerce_path(data.get("id_to_name_path")),
        web_root=_coerce_path(data.get("web_root")),
    )


#: Environment variable -> Config field.
ENV_VARS = {
    "INFINIMAP_DSN": "dsn",
    "INFINIMAP_FABRIC": "default_fabric",
    "INFINIMAP_HOST": "host",
    "INFINIMAP_PORT": "port",
    "INFINIMAP_WEB_ROOT": "web_root",
}


def from_env(base: Config | None = None, env: dict[str, str] | None = None) -> Config:
    """`base` with any INFINIMAP_* variable applied over it.

    Overrides the config file rather than the reverse: the file is baked into an
    image or written by config management, while the environment is what the
    operator sets for this particular run.
    """
    env = os.environ if env is None else env
    base = base if base is not None else Config()

    overrides: dict[str, object] = {}
    for var, field in ENV_VARS.items():
        raw = env.get(var)
        if raw is None or raw == "":
            continue
        if field == "port":
            try:
                overrides[field] = int(raw)
            except ValueError:
                raise ValueError(f"{var} must be an integer, got {raw!r}") from None
        elif field == "web_root":
            overrides[field] = Path(raw)
        else:
            overrides[field] = raw
    return replace(base, **overrides) if overrides else base


def from_file(path: str | Path) -> Config:
    """Load a Config from a TOML file. Raises FileNotFoundError if absent."""
    with open(path, "rb") as fh:
        return from_mapping(tomllib.load(fh))


def defaults() -> Config:
    return Config()
