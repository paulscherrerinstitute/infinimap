"""One-time setup: database, extension, roles, grants, migrations.

This is the only step that needs a superuser, and it is deliberately the only
place a superuser DSN appears. Afterwards the API connects as a read-only role
and the collector as a write role, so neither running service holds a
privileged credential.

Re-running is safe: every step checks for what it is about to create, and the
migrations are guarded by `schema_version`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field

from psycopg import sql

from . import schema

DEFAULT_DB = "infinimap"
API_ROLE = "infinimap_api"
COLLECTOR_ROLE = "infinimap_collector"


@dataclass
class InitResult:
    log: list[str] = field(default_factory=list)
    #: Only populated for roles this run created, so a re-run does not print a
    #: password that does not work.
    passwords: dict[str, str] = field(default_factory=dict)

    def say(self, msg: str) -> None:
        self.log.append(msg)


def initialise(superuser_dsn: str, *, dbname: str = DEFAULT_DB,
               api_password: str | None = None,
               collector_password: str | None = None,
               migrate: bool = True) -> InitResult:
    """Create everything infinimap needs, connected as a superuser.

    `superuser_dsn` names a maintenance database (typically `postgres`); the
    target database is created beside it and connected to separately, because
    CREATE DATABASE cannot run from inside the database being created.
    """
    import psycopg
    from psycopg.conninfo import conninfo_to_dict, make_conninfo

    res = InitResult()

    # -- 1. the database ----------------------------------------------------
    with psycopg.connect(superuser_dsn, autocommit=True) as conn:
        if _exists(conn, "SELECT 1 FROM pg_database WHERE datname = %s", dbname):
            res.say(f"database {dbname!r} already exists")
        else:
            conn.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(dbname)))
            res.say(f"created database {dbname!r}")

        # Roles are cluster-wide, so they belong on this connection.
        for role, given in ((API_ROLE, api_password),
                            (COLLECTOR_ROLE, collector_password)):
            if _exists(conn, "SELECT 1 FROM pg_roles WHERE rolname = %s", role):
                res.say(f"role {role!r} already exists (password unchanged)")
                continue
            password = given or secrets.token_urlsafe(24)
            conn.execute(
                sql.SQL("CREATE ROLE {} LOGIN PASSWORD {}").format(
                    sql.Identifier(role), sql.Literal(password))
            )
            res.passwords[role] = password
            res.say(f"created role {role!r}")

    # -- 2. inside the target database --------------------------------------
    params = conninfo_to_dict(superuser_dsn)
    params["dbname"] = dbname
    target_dsn = make_conninfo(**params)

    with psycopg.connect(target_dsn, autocommit=True) as conn:
        conn.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
        res.say("timescaledb extension present")

        if migrate:
            schema.ensure_version_table(conn)
            todo = schema.pending(conn)
            if not todo:
                res.say("schema already up to date")
            for m in todo:
                n = schema.apply_one(conn, m)
                res.say(f"applied {m} ({n} statements)")

        for stmt in _grants(dbname):
            conn.execute(stmt)
        res.say(f"granted {API_ROLE} read-only, {COLLECTOR_ROLE} write")

    return res


def _exists(conn, query: str, param: str) -> bool:
    return conn.execute(query, (param,)).fetchone() is not None


def _grants(dbname: str) -> list[sql.Composed]:
    """Least privilege: the API only reads, the collector never drops.

    ALTER DEFAULT PRIVILEGES covers tables a *later* migration creates, so this
    does not have to be re-run after every upgrade.
    """
    api, col, db = (sql.Identifier(API_ROLE), sql.Identifier(COLLECTOR_ROLE),
                    sql.Identifier(dbname))
    return [
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}, {}").format(db, api, col),
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}, {}").format(api, col),
        sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA public TO {}").format(api),
        sql.SQL("GRANT SELECT, INSERT, UPDATE ON ALL TABLES IN SCHEMA public "
                "TO {}").format(col),
        sql.SQL("GRANT USAGE ON ALL SEQUENCES IN SCHEMA public TO {}").format(col),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT ON TABLES TO {}").format(api),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT SELECT, INSERT, UPDATE ON TABLES TO {}").format(col),
        sql.SQL("ALTER DEFAULT PRIVILEGES IN SCHEMA public "
                "GRANT USAGE ON SEQUENCES TO {}").format(col),
    ]
