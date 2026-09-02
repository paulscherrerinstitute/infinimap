"""Discovering migrations, recording which ran, and applying the rest.

The numbered files in `migrations/` are the schema. `schema_version` records 
what has run, and a recorded migration is never run again.

Each file is executed one statement at a time over an autocommit connection, so
the BEGIN/COMMIT inside a file decide what is atomic.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from psycopg.rows import tuple_row

from .sqlsplit import split_statements, strip_comments

MIGRATIONS_DIR = Path(__file__).parent / "migrations"

#: `NNN_name.sql` -- the number orders them and is the recorded version.
_FILENAME = re.compile(r"^(\d+)_([A-Za-z0-9_]+)\.sql$")

#: `CREATE EXTENSION [IF NOT EXISTS] name`, quoted or not.
_CREATE_EXTENSION = re.compile(
    r"""CREATE\s+EXTENSION\s+(?:IF\s+NOT\s+EXISTS\s+)?["']?([A-Za-z_][\w]*)""",
    re.IGNORECASE,
)

VERSION_TABLE = """
CREATE TABLE IF NOT EXISTS schema_version (
    version    INTEGER     PRIMARY KEY,
    name       TEXT        NOT NULL,
    checksum   TEXT        NOT NULL,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
)
"""


class MigrationError(RuntimeError):
    """A migration failed, named down to the statement."""


@dataclass(frozen=True, slots=True)
class Migration:
    version: int
    name: str
    path: Path

    @property
    def sql(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @property
    def checksum(self) -> str:
        """sha256 of the file, so an edit to an applied migration is visible."""
        return hashlib.sha256(self.path.read_bytes()).hexdigest()

    def __str__(self) -> str:
        return f"{self.version:03d}_{self.name}"


def discover(directory: Path | None = None) -> list[Migration]:
    """Every migration on disk, in version order."""
    directory = directory or MIGRATIONS_DIR
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        m = _FILENAME.match(path.name)
        if m:
            found.append(Migration(int(m.group(1)), m.group(2), path))
    found.sort(key=lambda mig: mig.version)
    return found


def required_extensions(directory: Path | None = None) -> list[str]:
    """Every extension the migrations create, in first-appearance order.

    This is what lets `check` name a missing prerequisite before a migration 
    trips over it: 002 needs btree_gist and intarray, which RHEL ships in a 
    separate contrib package while Debian and the TimescaleDB image bundle them.
    """
    found: list[str] = []
    for migration in discover(directory):
        for name in _CREATE_EXTENSION.findall(strip_comments(migration.sql)):
            if name not in found:
                found.append(name)
    return found


def missing_extensions(conn, directory: Path | None = None) -> list[str]:
    """Required extensions this server cannot provide, in order.

    "Available" means the files are installed, not that the extension has been
    created -- creating it is a migration's job.
    """
    required = required_extensions(directory)
    if not required:
        return []
    rows_ = rows(conn,
                 "SELECT name FROM pg_available_extensions WHERE name = ANY(%s)",
                 (required,))
    available = {r[0] for r in rows_}
    return [name for name in required if name not in available]


def contrib_hint(conn) -> str:
    """How to install the missing pieces, named for this server's packaging."""
    version = scalar(conn, "SHOW server_version") or ""
    major = version.split(".")[0].split()[0]
    return (f"install the PostgreSQL contrib modules: "
            f"`postgresql{major}-contrib` (RHEL/PGDG) or "
            f"`postgresql-contrib` (Debian/Ubuntu)")


def scalar(conn, sql: str, params=None):
    """First column of the first row, or None."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return None if row is None else row[0]


def rows(conn, sql: str, params=None) -> list[tuple]:
    """Every row as a tuple, whatever the connection's row factory."""
    with conn.cursor(row_factory=tuple_row) as cur:
        cur.execute(sql, params)
        return cur.fetchall()


def ensure_version_table(conn) -> None:
    """Create `schema_version` if absent. Plain SQL; needs no TimescaleDB."""
    conn.execute(VERSION_TABLE)


def has_version_table(conn) -> bool:
    return bool(scalar(conn, "SELECT to_regclass('schema_version')"))


def applied(conn) -> dict[int, tuple[str, str]]:
    """version -> (name, checksum) for everything already run."""
    if not has_version_table(conn):
        return {}
    found = rows(conn,
                 "SELECT version, name, checksum FROM schema_version ORDER BY version")
    return {r[0]: (r[1], r[2]) for r in found}


def pending(conn, directory: Path | None = None) -> list[Migration]:
    """Migrations on disk that `schema_version` has no row for."""
    done = applied(conn)
    return [m for m in discover(directory) if m.version not in done]


def changed_since_applied(conn, directory: Path | None = None) -> list[Migration]:
    """Applied migrations whose file no longer matches the recorded checksum."""
    done = applied(conn)
    out = []
    for m in discover(directory):
        rec = done.get(m.version)
        if rec and rec[1] != m.checksum:
            out.append(m)
    return out


def preflight(conn, directory: Path | None = None) -> None:
    """Refuse before touching anything if a prerequisite is absent."""
    missing = missing_extensions(conn, directory)
    if missing:
        raise MigrationError(
            f"this server cannot provide: {', '.join(missing)}\n"
            f"    {contrib_hint(conn)}"
        )


def apply_one(conn, migration: Migration) -> int:
    """Run one migration and record it. Returns the statement count."""
    statements = split_statements(migration.sql)
    for i, stmt in enumerate(statements, start=1):
        try:
            conn.execute(stmt)
        except Exception as exc:                       # noqa: BLE001 - re-raised
            raise MigrationError(
                f"{migration} failed at statement {i}/{len(statements)} "
                f"({_first_line(stmt)}): {exc}"
            ) from exc

    conn.execute(
        "INSERT INTO schema_version (version, name, checksum) VALUES (%s, %s, %s) "
        "ON CONFLICT (version) DO UPDATE SET checksum = EXCLUDED.checksum, "
        "applied_at = now()",
        (migration.version, migration.name, migration.checksum),
    )
    return len(statements)


def _first_line(stmt: str) -> str:
    """The first line that is actually SQL, for naming a failure."""
    for line in stmt.splitlines():
        line = line.strip()
        if line and not line.startswith("--"):
            return line[:70]
    return stmt.strip()[:70]
