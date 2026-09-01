"""Diagnose a database, and say what to do about it.

The point of this module is that every way the setup can be wrong should produce
a specific instruction rather than a Postgres error thrown from the middle of a
migration.

Useful on both machines: run it on the fabric node against the server's DSN to
prove reachability, credentials and schema version before enabling the timer.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from . import schema
from .schema import rows, scalar

#: Matches the default in api/config.py and collector/config.py.
DEFAULT_DSN = "postgresql:///infinimap"

_INSTALL_HINT = (
    "install the TimescaleDB package for your PostgreSQL major version "
    "(see https://docs.timescale.com/self-hosted/latest/install/)"
)


class Status(Enum):
    OK = "ok"
    INFO = "info"
    WARN = "warn"
    FAIL = "FAIL"


@dataclass(frozen=True, slots=True)
class Finding:
    status: Status
    label: str
    detail: str
    fix: str = ""

    def render(self) -> str:
        mark = {Status.OK: "[ ok ]", Status.INFO: "[ -- ]",
                Status.WARN: "[warn]", Status.FAIL: "[FAIL]"}[self.status]
        line = f"{mark} {self.label}: {self.detail}"
        if self.fix:
            line += f"\n         -> {self.fix}"
        return line


def check(dsn: str) -> list[Finding]:
    """Every finding for `dsn`, in the order the operator should fix them.

    Stops early only when a later check cannot mean anything -- there is no
    point reporting the schema version of a server we could not reach.
    """
    import psycopg

    try:
        conn = psycopg.connect(dsn, autocommit=True, connect_timeout=10)
    except Exception as exc:                          # noqa: BLE001
        return [Finding(Status.FAIL, "connection", _first_line(exc),
                        "check the DSN, that the server accepts remote "
                        "connections (listen_addresses, pg_hba.conf), and that "
                        "the database exists -- `infinimap-db init` creates it")]

    with conn:
        out = [Finding(Status.OK, "connection", _server_version(conn))]
        out += _timescale_findings(conn)
        out += _schema_findings(conn)
    return out


def _first_line(exc: Exception) -> str:
    """psycopg reports every attempt when a host resolves to both v4 and v6."""
    return str(exc).strip().splitlines()[0]


def _server_version(conn) -> str:
    v = scalar(conn, "SHOW server_version")
    who = rows(conn, "SELECT current_user, current_database()")[0]
    return f"PostgreSQL {v}, connected as {who[0]} to {who[1]}"


def _timescale_findings(conn) -> list[Finding]:
    """Distinguish the four states TimescaleDB can be in."""
    available = rows(conn,
        "SELECT default_version FROM pg_available_extensions WHERE name = 'timescaledb'")
    available = available[0] if available else None
    if not available:
        return [Finding(Status.FAIL, "timescaledb", "not installed on this server",
                        _INSTALL_HINT)]

    preloaded = scalar(conn, "SHOW shared_preload_libraries")
    if "timescaledb" not in preloaded:
        return [Finding(Status.FAIL, "timescaledb",
                        f"installed ({available[0]}) but not preloaded",
                        "sudo timescaledb-tune && sudo systemctl restart postgresql")]

    created = rows(conn,
        "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'")
    created = created[0] if created else None
    if not created:
        return [Finding(Status.FAIL, "timescaledb",
                        f"available ({available[0]}) but not created in this database",
                        "infinimap-db init  (CREATE EXTENSION needs a superuser)")]

    return [Finding(Status.OK, "timescaledb", f"version {created[0]}")]


def _schema_findings(conn) -> list[Finding]:
    if not schema.has_version_table(conn):
        n = len(schema.discover())
        return [Finding(Status.FAIL, "schema", "not initialised",
                        f"infinimap-db migrate  ({n} migrations to apply)")]

    done = schema.applied(conn)
    todo = schema.pending(conn)
    drift = schema.changed_since_applied(conn)

    out = [Finding(
        Status.OK if not todo else Status.WARN,
        "schema",
        f"version {max(done) if done else 'none'}"
        + (f", {len(todo)} pending: {', '.join(str(m) for m in todo)}" if todo else ""),
        "infinimap-db migrate" if todo else "",
    )]
    if drift:
        out.append(Finding(
            Status.WARN, "checksum",
            f"{len(drift)} applied migration(s) edited since: "
            + ", ".join(str(m) for m in drift),
            "the file no longer matches what ran here; deployments from this "
            "checkout may differ",
        ))
    return out


def worst(findings: list[Finding]) -> Status:
    """The most severe status present, for an exit code."""
    for s in (Status.FAIL, Status.WARN, Status.INFO):
        if any(f.status is s for f in findings):
            return s
    return Status.OK
