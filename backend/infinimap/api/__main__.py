"""Run the read API.

    infinimap-api
    infinimap-api --config ./api.toml
    infinimap-api --openapi openapi.json    # write the schema and exit

Also runnable as `python -m infinimap.api`.

Every setting comes from the config file, falling back to a built-in default
when the file omits it. With no --config the default path is loaded if it
exists;
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path

import uvicorn

from .app import create_app
from .config import DEFAULT_CONFIG_PATH, Config, defaults, from_file


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="infinimap-api")
    ap.add_argument("--config", metavar="FILE", help="path to api.toml")
    ap.add_argument("--openapi", metavar="FILE", nargs="?", const="-",
                    help="write the OpenAPI schema and exit ('-' for stdout)")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return ap.parse_args(argv)


def _write_openapi(cfg: Config, dest: str) -> None:
    """Dump the schema. Needs no database.

    Keys are sorted because this file is committed: an unstable key order would
    make every regeneration a large diff and hide the one field that moved.
    """
    spec = json.dumps(create_app(cfg).openapi(), indent=2, sort_keys=True)
    if dest == "-":
        print(spec)
    else:
        Path(dest).write_text(spec + "\n", encoding="utf-8")
        print(f"wrote {dest}", file=sys.stderr)


def _load_config(config_path: str | None) -> Config:
    if config_path is not None:
        return from_file(config_path)
    if DEFAULT_CONFIG_PATH.exists():
        return from_file(DEFAULT_CONFIG_PATH)
    return defaults()


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    # Log in UTC
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = _load_config(args.config)

    if args.openapi:
        _write_openapi(cfg, args.openapi)
        return 0

    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port)
    return 0


def console() -> int:
    """Console-script entry point. Takes no arguments; argparse reads sys.argv."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
