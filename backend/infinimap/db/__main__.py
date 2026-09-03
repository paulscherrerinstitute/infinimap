"""Database administration CLI.

    infinimap-db check                       # diagnose; safe, read-only
    infinimap-db migrate                     # apply pending migrations
    infinimap-db init --dsn postgresql://postgres@localhost/postgres

`check` is the one to reach for first: it names what is wrong and the command
that fixes it. It is also useful from the collector's machine, pointed at the
server's DSN, to prove reachability before enabling the timer.

Exit codes: 0 clean, 1 something needs doing, 2 the command itself failed.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

from . import init as init_mod
from . import schema
from .check import DEFAULT_DSN, Status, check, worst

log = logging.getLogger("infinimap.db")


def _parse_args(argv: list[str]) -> argparse.Namespace:
    ap = argparse.ArgumentParser(prog="infinimap-db", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    sub = ap.add_subparsers(dest="command", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--dsn", help=f"libpq connection string "
                                      f"(env INFINIMAP_DSN; default {DEFAULT_DSN})")

    sub.add_parser("check", parents=[common],
                   help="diagnose the server and schema; changes nothing")
    sub.add_parser("migrate", parents=[common],
                   help="apply pending migrations")

    p_init = sub.add_parser("init", parents=[common],
                            help="create database, roles, extension, then migrate")
    p_init.add_argument("--dbname", default=init_mod.DEFAULT_DB,
                        help=f"database to create (default {init_mod.DEFAULT_DB})")
    p_init.add_argument("--api-password", help="password for the read-only role")
    p_init.add_argument("--collector-password", help="password for the write role")
    p_init.add_argument("--no-migrate", action="store_true",
                        help="create the database but leave the schema empty")
    return ap.parse_args(argv)


def _dsn(args: argparse.Namespace) -> str:
    """Flag, then environment, then the built-in default."""
    return args.dsn or os.environ.get("INFINIMAP_DSN") or DEFAULT_DSN


def _cmd_check(args: argparse.Namespace) -> int:
    findings = check(_dsn(args))
    for f in findings:
        print(f.render())
    return 0 if worst(findings) in (Status.OK, Status.INFO) else 1


def _cmd_migrate(args: argparse.Namespace) -> int:
    import psycopg

    with psycopg.connect(_dsn(args), autocommit=True) as conn:
        schema.preflight(conn)
        schema.ensure_version_table(conn)
        todo = schema.pending(conn)
        if not todo:
            done = schema.applied(conn)
            print(f"up to date at version {max(done) if done else 'none'}")
            return 0
        for m in todo:
            n = schema.apply_one(conn, m)
            print(f"applied {m} ({n} statements)")
    print(f"migrated to version {todo[-1].version}")
    return 0


def _report(res: init_mod.InitResult) -> None:
    for line in res.log:
        print(line)

    if res.passwords:
        print("\nGenerated passwords -- shown once, store them now:")
        for role, pw in res.passwords.items():
            print(f"  {role}: {pw}")
        print("\nPut the collector's in its config on the fabric node, and the "
              "API's in api.toml.\nIf that node is remote, the server also needs "
              "listen_addresses and a pg_hba.conf entry.")


def _cmd_init(args: argparse.Namespace) -> int:
    try:
        res = init_mod.initialise(
            _dsn(args),
            dbname=args.dbname,
            api_password=args.api_password,
            collector_password=args.collector_password,
            migrate=not args.no_migrate,
        )
    except init_mod.PartialInit as exc:
        # The roles are already in the cluster; print their passwords before
        # the error or they are lost, since a re-run will not touch them.
        _report(exc.result)
        print(f"\nerror: {exc.cause}", file=sys.stderr)
        print("the database and roles above survive; fix the above and re-run "
              "init", file=sys.stderr)
        return 2

    _report(res)
    return 0


def main(argv: list[str]) -> int:
    args = _parse_args(argv)
    logging.Formatter.converter = time.gmtime            # log in UTC, as the rest does
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    handler = {"check": _cmd_check, "migrate": _cmd_migrate, "init": _cmd_init}
    try:
        return handler[args.command](args)
    except schema.MigrationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except Exception as exc:                              # noqa: BLE001
        print(f"error: {exc}", file=sys.stderr)
        return 2


def console() -> int:
    """Console-script entry point. Takes no arguments; argparse reads sys.argv."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
