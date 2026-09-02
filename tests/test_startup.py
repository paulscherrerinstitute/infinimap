"""The startup handshake: schema version comparison and clock-skew arithmetic."""

from __future__ import annotations

import pytest

from infinimap.db import startup
from infinimap.db.schema import discover


class FakeCursor:
    """A cursor, because that is what the helpers actually use.

    They take their own cursor with an explicit row factory rather than calling
    `conn.execute`, since the API's pool hands out dict rows.
    """

    def __init__(self, conn: FakeConn):
        self._conn = conn
        self._row: tuple | None = None

    def __enter__(self) -> FakeCursor:
        return self

    def __exit__(self, *_exc) -> bool:
        return False

    def execute(self, sql: str, params=None) -> None:
        self._conn.queries.append(sql)
        self._row = self._conn.answer(sql)

    def fetchone(self) -> tuple | None:
        return self._row

    def fetchall(self) -> list[tuple]:
        return [self._row] if self._row is not None else []


class FakeConn:
    """Answers the queries `startup` asks, and nothing else.

    `version` of None means the schema_version table does not exist.
    `server_epoch` is a callable so a test can move the server's clock.
    """

    def __init__(self, *, version: int | None, server_epoch=None):
        self.version = version
        self.server_epoch = server_epoch or (lambda: 1_000_000.0)
        self.queries: list[str] = []

    def cursor(self, row_factory=None) -> FakeCursor:
        return FakeCursor(self)

    def answer(self, sql: str) -> tuple | None:
        if "to_regclass" in sql:
            return (None if self.version is None else "schema_version",)
        if "max(version)" in sql:
            return (self.version,)
        if "clock_timestamp" in sql:
            return (self.server_epoch(),)
        raise AssertionError(f"unexpected query: {sql}")


# -- expected version --------------------------------------------------------

def test_expected_version_comes_from_the_shipped_migrations():
    """Derived, not declared, so it cannot drift from the files in the wheel."""
    assert startup.EXPECTED_VERSION == max(m.version for m in discover())


# -- schema handshake --------------------------------------------------------

def test_matching_schema_passes():
    conn = FakeConn(version=startup.EXPECTED_VERSION)
    assert startup.check_schema(conn, component="test") == startup.EXPECTED_VERSION


def test_uninitialised_database_is_fatal_and_names_init():
    conn = FakeConn(version=None)
    with pytest.raises(startup.SchemaMismatch, match="infinimap-db init"):
        startup.check_schema(conn, component="test")


def test_older_schema_is_fatal_and_names_migrate():
    conn = FakeConn(version=startup.EXPECTED_VERSION - 1)
    with pytest.raises(startup.SchemaMismatch, match="infinimap-db migrate"):
        startup.check_schema(conn, component="test")


def test_newer_schema_warns_but_continues(caplog):
    """A server upgraded ahead of its collectors must not take them all down."""
    conn = FakeConn(version=startup.EXPECTED_VERSION + 1)
    with caplog.at_level("WARNING"):
        got = startup.check_schema(conn, component="collector")
    assert got == startup.EXPECTED_VERSION + 1
    assert "newer than this build" in caplog.text


def test_unreadable_schema_version_names_the_grant():
    """`to_regclass` sees a table this role may not read; say so specifically."""
    import psycopg

    class Denying(FakeConn):
        def answer(self, sql: str):
            if "max(version)" in sql:
                raise psycopg.errors.InsufficientPrivilege(
                    "permission denied for table schema_version")
            return super().answer(sql)

    with pytest.raises(startup.SchemaMismatch, match="needs SELECT"):
        startup.check_schema(Denying(version=4), component="api")


# -- clock skew --------------------------------------------------------------

def test_no_skew_measures_near_zero():
    import time
    conn = FakeConn(version=0, server_epoch=time.time)
    skew, round_trip = startup.measure_skew(conn, samples=3)
    assert abs(skew) < 1.0
    assert round_trip >= 0.0


def test_server_ahead_gives_positive_skew():
    import time
    conn = FakeConn(version=0, server_epoch=lambda: time.time() + 120.0)
    skew, _ = startup.measure_skew(conn, samples=3)
    assert 119.0 < skew < 121.0


def test_server_behind_gives_negative_skew():
    import time
    conn = FakeConn(version=0, server_epoch=lambda: time.time() - 120.0)
    skew, _ = startup.measure_skew(conn, samples=3)
    assert -121.0 < skew < -119.0


def test_skew_over_the_limit_raises_for_a_writer():
    import time
    conn = FakeConn(version=0, server_epoch=lambda: time.time() + 90.0)
    with pytest.raises(startup.ClockSkew, match="over the 5s limit"):
        startup.check_clock(conn, component="collector", max_skew_s=5.0)


def test_zero_limit_never_raises():
    """The reader's setting, and the operator's override."""
    import time
    conn = FakeConn(version=0, server_epoch=lambda: time.time() + 5000.0)
    assert startup.check_clock(conn, component="api", max_skew_s=0.0) > 4000.0


def test_skew_within_the_limit_but_over_the_warn_line_warns(caplog):
    import time
    over = startup.WARN_SKEW_S + 1.0
    conn = FakeConn(version=0, server_epoch=lambda: time.time() + over)
    with caplog.at_level("WARNING"):
        startup.check_clock(conn, component="collector", max_skew_s=5.0)
    assert "clock is" in caplog.text


def test_small_skew_stays_quiet(caplog):
    import time
    conn = FakeConn(version=0, server_epoch=time.time)
    with caplog.at_level("WARNING"):
        startup.check_clock(conn, component="collector", max_skew_s=5.0)
    assert caplog.text == ""


# -- measurement uncertainty -------------------------------------------------

def test_certain_skew_subtracts_half_the_round_trip():
    # The server could have answered at either end of the round trip, so only
    # what lies outside it is proven.
    assert startup.certain_skew(0.120, 0.060) == pytest.approx(0.090)
    assert startup.certain_skew(-0.120, 0.060) == pytest.approx(0.090)


def test_certain_skew_never_goes_negative():
    """A slow link cannot prove the clock is better than perfect."""
    assert startup.certain_skew(0.001, 0.500) == 0.0


def test_a_distant_database_does_not_trip_the_warning(caplog):
    """The point of subtracting uncertainty: 60ms of WAN must not read as skew."""
    import time
    rtt = 0.12                       # 120ms round trip, well over the 100ms line
    conn = FakeConn(version=0, server_epoch=lambda: time.time() + rtt / 2)
    with caplog.at_level("WARNING"):
        startup.check_clock(conn, component="api", max_skew_s=0.0)
    assert caplog.text == ""


def test_measurement_uses_the_fastest_sample():
    """A slow round trip is the entire uncertainty, so the best one wins."""
    import time
    delays = iter([0.05, 0.0, 0.05])

    def epoch():
        time.sleep(next(delays, 0.0))
        return time.time()

    conn = FakeConn(version=0, server_epoch=epoch)
    _, round_trip = startup.measure_skew(conn, samples=3)
    assert round_trip < 0.04


# -- required extensions -----------------------------------------------------

def test_required_extensions_are_read_from_the_migrations():
    """Parsed from the SQL, not listed, so the two cannot drift apart."""
    from infinimap.db.schema import required_extensions

    found = required_extensions()
    assert found == ["timescaledb", "btree_gist", "intarray"]


def test_required_extensions_ignores_commented_out_sql(tmp_path):
    from infinimap.db.schema import required_extensions

    (tmp_path / "000_x.sql").write_text(
        "-- CREATE EXTENSION IF NOT EXISTS never_wanted;\n"
        "/* CREATE EXTENSION also_not; */\n"
        "CREATE EXTENSION IF NOT EXISTS wanted;\n"
        'CREATE EXTENSION "quoted_one";\n',
        encoding="utf-8",
    )
    assert required_extensions(tmp_path) == ["wanted", "quoted_one"]


def test_required_extensions_deduplicates_across_files(tmp_path):
    from infinimap.db.schema import required_extensions

    (tmp_path / "000_a.sql").write_text("CREATE EXTENSION a;", encoding="utf-8")
    (tmp_path / "001_b.sql").write_text(
        "CREATE EXTENSION a;\nCREATE EXTENSION b;", encoding="utf-8")
    assert required_extensions(tmp_path) == ["a", "b"]


def test_connection_hint_names_the_actual_cause():
    """The generic hint sent a real operator looking at pg_hba for a missing role."""
    from infinimap.db.check import _connection_hint

    role = _connection_hint(Exception('FATAL:  role "matos_s" does not exist'))
    assert "sudo -u postgres" in role

    db = _connection_hint(Exception('FATAL:  database "infinimap" does not exist'))
    assert "init" in db and "sudo" not in db

    auth = _connection_hint(Exception("FATAL:  password authentication failed"))
    assert "pg_hba" in auth

    # A bare machine: no socket file (local) or nothing listening (remote).
    socket = _connection_hint(Exception(
        'connection to server on socket "/var/run/postgresql/.s.PGSQL.5432" '
        "failed: No such file or directory"))
    assert "not installed" in socket and "Install PostgreSQL" in socket

    refused = _connection_hint(Exception("Connection refused"))
    assert "Install PostgreSQL" in refused

    other = _connection_hint(Exception("timeout expired"))
    assert "listen_addresses" in other
