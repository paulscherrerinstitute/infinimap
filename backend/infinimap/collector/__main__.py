"""CLI entry point.

    infinimap-collector --once --from-dir ../test_data/test_data/data
    infinimap-collector --loop --config /etc/infinimap/collector.toml
    INFINIMAP_DSN=postgresql://... infinimap-collector --loop

Flags override the config file (see config.from_file); the file overrides the
built-in defaults. Fixture mode (--from-dir) still needs a database to write to;
it only changes where the records are read.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from dataclasses import replace

from .config import (Config, DEFAULT_CONFIG_PATH, defaults, from_env,
                     from_file)
from ..db import startup
from .daemon import run


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="infinimap-collector")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="run a single cycle and exit (systemd-timer friendly)")
    mode.add_argument("--loop", action="store_true",
                      help="run continuously at the configured interval (default)")
    ap.add_argument("--config", metavar="FILE",
                    help="TOML config file (default: /etc/infinimap/collector.toml if present)")
    ap.add_argument("--from-dir", metavar="DIR",
                    help="read record dumps from a fixture directory instead of saquery")
    ap.add_argument("--dsn", help="PostgreSQL connection string")
    ap.add_argument("--fabric", help="fabric name (created if absent)")
    ap.add_argument("--interval", type=float, help="poll interval in seconds (loop mode)")
    ap.add_argument("--subnet-prefix",
                    help="IB subnet prefix, e.g. 0xfe80000000000000 (first creation only)")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    return ap.parse_args(argv)


def _base_config(config_path: str | None) -> Config:
    """The file layer of config, before CLI overrides.

    An explicit --config must exist. With no --config we load the default path if it happens
    to be there, otherwise built-in defaults, so CLI-only runs (e.g. --from-dir
    testing) work with no file on disk.
    """
    if config_path is not None:
        return from_file(config_path)
    if DEFAULT_CONFIG_PATH.exists():
        return from_file(DEFAULT_CONFIG_PATH)
    return defaults()


def _config_from(args: argparse.Namespace) -> Config:
    """File, then environment, then flags - each layer overriding the last."""
    base = from_env(_base_config(args.config))

    overrides: dict[str, object] = {}
    if args.dsn:
        overrides["dsn"] = args.dsn
    if args.fabric:
        overrides["fabric_name"] = args.fabric
    if args.subnet_prefix:
        overrides["subnet_prefix"] = int(args.subnet_prefix, 0)
    if args.interval is not None:
        overrides["interval_s"] = args.interval
    if args.from_dir:
        overrides["from_dir"] = args.from_dir
    return replace(base, **overrides) if overrides else base


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    # Log in UTC
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    try:
        return run(_config_from(args), once=args.once and not args.loop)
    except (startup.SchemaMismatch, startup.ClockSkew) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


def console() -> int:
    """Console-script entry point. Takes no arguments; argparse reads sys.argv."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
