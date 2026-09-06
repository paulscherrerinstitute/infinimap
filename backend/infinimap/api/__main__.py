"""Run the read API.

    infinimap-api
    infinimap-api --config ./api.toml
    infinimap-api --dsn postgresql://infinimap_api:...@db/infinimap
    infinimap-api --openapi openapi.json    # write the schema and exit

Also runnable as `python -m infinimap.api`.

Settings resolve flag, then environment, then config file, then built-in
default. With no --config the default path is loaded if it exists. The
environment sits above the file because that is how a container is configured,
and it keeps a password out of a file on disk.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from dataclasses import replace
from pathlib import Path

from .config import DEFAULT_CONFIG_PATH, Config, defaults, from_env, from_file


def _server_bits():
    """Import what the `server` extra provides, or explain its absence.

    `pip install infinimap[collector]` still puts this module on disk, so
    `infinimap-api` exists as a command on a fabric node.
    """
    try:
        import uvicorn

        from .app import create_app
    except ImportError as exc:
        raise SystemExit(
            f"infinimap-api needs the server extra (missing: {exc.name}).\n"
            f"    pip install 'infinimap[server]'"
        ) from exc
    return uvicorn, create_app


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="infinimap-api")
    ap.add_argument("--config", metavar="FILE", help="path to api.toml")
    ap.add_argument("--dsn", help="libpq connection string (env INFINIMAP_DSN)")
    ap.add_argument("--openapi", metavar="FILE", nargs="?", const="-",
                    help="write the OpenAPI schema and exit ('-' for stdout)")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return ap.parse_args(argv)


def _write_openapi(cfg: Config, dest: str) -> None:
    """Dump the schema. Needs no database.

    Keys are sorted because this file is committed: an unstable key order would
    make every regeneration a large diff and hide the one field that moved.
    """
    _, create_app = _server_bits()
    spec = json.dumps(create_app(cfg).openapi(), indent=2, sort_keys=True)
    if dest == "-":
        print(spec)
    else:
        Path(dest).write_text(spec + "\n", encoding="utf-8")
        print(f"wrote {dest}", file=sys.stderr)


def _load_config(args: argparse.Namespace) -> Config:
    """File, then environment, then flags - each layer overriding the last."""
    if args.config is not None:
        cfg = from_file(args.config)
    elif DEFAULT_CONFIG_PATH.exists():
        cfg = from_file(DEFAULT_CONFIG_PATH)
    else:
        cfg = defaults()

    cfg = from_env(cfg)
    if args.dsn:
        cfg = replace(cfg, dsn=args.dsn)
    return cfg


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    # Log in UTC
    logging.Formatter.converter = time.gmtime
    # INFO until the config is read, so the config loader's own warnings (an
    # unknown key, say) are not swallowed by the level that config sets.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    cfg = _load_config(args)
    logging.getLogger().setLevel(_level(cfg.log_level, args.verbose))

    if args.openapi:
        _write_openapi(cfg, args.openapi)
        return 0

    uvicorn, create_app = _server_bits()
    uvicorn.run(create_app(cfg), host=cfg.host, port=cfg.port)
    return 0


def _level(name: str, verbose: bool) -> int:
    """The configured level, unless -v was passed.

    `-v` is the innermost layer of the precedence and so wins over both the
    file and the environment.
    """
    if verbose:
        return logging.DEBUG
    return getattr(logging, name.upper(), None) if isinstance(
        getattr(logging, name.upper(), None), int) else logging.INFO


def console() -> int:
    """Console-script entry point. Takes no arguments; argparse reads sys.argv."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
