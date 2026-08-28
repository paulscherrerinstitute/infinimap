"""CLI entry point.

    python -m collector --once --from-dir ../test_data/test_data/data
    python -m collector --loop --config /etc/ibmon/collector.toml

Flags override the config file (see config.from_file); the file overrides the
built-in defaults. Fixture mode (--from-dir) still needs a database to write to;
it only changes where the records are read.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time

from .config import Config, DEFAULT_CONFIG_PATH, defaults, from_file
from .daemon import run


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="collector")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--once", action="store_true",
                      help="run a single cycle and exit (systemd-timer friendly)")
    mode.add_argument("--loop", action="store_true",
                      help="run continuously at the configured interval (default)")
    ap.add_argument("--config", metavar="FILE",
                    help="TOML config file (default: /etc/ibmon/collector.toml if present)")
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
    base = _base_config(args.config)
    prefix = (int(args.subnet_prefix, 0) if args.subnet_prefix else base.subnet_prefix)
    return Config(
        dsn=args.dsn or base.dsn,
        fabric_name=args.fabric or base.fabric_name,
        subnet_prefix=prefix,
        interval_s=args.interval if args.interval is not None else base.interval_s,
        from_dir=args.from_dir or base.from_dir,
    )


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    # Log in UTC
    logging.Formatter.converter = time.gmtime
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    return run(_config_from(args), once=args.once and not args.loop)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
