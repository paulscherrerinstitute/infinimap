"""The statement splitter, which has no database to check its work.

Everything here is a case where a naive split on `;` gets it wrong, plus the
property the migration runner actually depends on: the continuous aggregates in
004 must come out *before* that file's BEGIN, or TimescaleDB rejects them.
"""

from __future__ import annotations

import re

import pytest

from infinimap.db.schema import discover
from infinimap.db.sqlsplit import split_statements, strip_comments


def code(stmt: str) -> str:
    """`stmt` with comments removed, for classifying what it actually is.

    Uses the module's own scanner rather than a regex: block comments nest, so
    `re.sub(r"/\\*.*?\\*/", ...)` stops at the first `*/` and leaves the tail
    looking like SQL. That mistake is what this helper existed to make.
    """
    return strip_comments(stmt).strip()


def test_splits_plain_statements():
    assert split_statements("SELECT 1; SELECT 2;") == ["SELECT 1", "SELECT 2"]


def test_trailing_statement_without_semicolon_counts():
    assert split_statements("SELECT 1; SELECT 2") == ["SELECT 1", "SELECT 2"]


def test_ignores_empty_and_comment_only_fragments():
    assert split_statements("") == []
    assert split_statements("   ;;  ") == []
    assert split_statements("-- just a comment\n") == []
    assert split_statements("SELECT 1;\n-- trailing note\n") == ["SELECT 1"]


def test_semicolon_inside_string_is_not_a_separator():
    assert split_statements("SELECT 'a;b';") == ["SELECT 'a;b'"]


def test_doubled_quote_escapes_within_string():
    assert split_statements("SELECT 'it''s; fine';") == ["SELECT 'it''s; fine'"]


def test_semicolon_inside_quoted_identifier():
    assert split_statements('SELECT 1 AS "od;d";') == ['SELECT 1 AS "od;d"']


def test_semicolon_inside_line_comment():
    stmts = split_statements("SELECT 1; -- a; b\nSELECT 2;")
    assert [code(s) for s in stmts] == ["SELECT 1", "SELECT 2"]


def test_semicolon_inside_block_comment():
    stmts = split_statements("SELECT 1; /* a; b */ SELECT 2;")
    assert [code(s) for s in stmts] == ["SELECT 1", "SELECT 2"]


def test_block_comments_nest():
    # PostgreSQL nests /* */, unlike the SQL standard: the inner close must not
    # end the outer comment.
    stmts = split_statements("SELECT 1; /* outer /* inner; */ still; */ SELECT 2;")
    assert [code(s) for s in stmts] == ["SELECT 1", "SELECT 2"]


def test_dollar_quoted_body_stays_one_statement():
    body = (
        "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$\n"
        "BEGIN\n"
        "  PERFORM 1;\n"
        "  PERFORM 2;\n"
        "END $$;"
    )
    stmts = split_statements(body)
    assert len(stmts) == 1
    assert stmts[0].count(";") == 2      # both internal ones survive


def test_tagged_dollar_quoting():
    body = "DO $body$ BEGIN PERFORM 1; END $body$;"
    assert split_statements(body) == ["DO $body$ BEGIN PERFORM 1; END $body$"]


def test_inner_dollar_tag_does_not_close_outer():
    body = "DO $a$ SELECT $b$ ...; $b$; $a$;"
    assert len(split_statements(body)) == 1


# -- properties of the real migrations ---------------------------------------

@pytest.mark.parametrize("migration", discover(), ids=str)
def test_every_statement_is_non_empty(migration):
    assert all(s.strip() for s in split_statements(migration.sql))


@pytest.mark.parametrize("migration", discover(), ids=str)
def test_round_trip_loses_no_sql(migration):
    """Splitting then rejoining must preserve every non-whitespace character."""
    norm = lambda t: re.sub(r"[\s;]+", "", t)
    joined = " ".join(split_statements(migration.sql))
    assert norm(migration.sql) == norm(joined)


def test_004_continuous_aggregates_precede_its_transaction():
    """The property the runner depends on.

    `CREATE MATERIALIZED VIEW ... WITH (timescaledb.continuous)` is rejected
    inside a transaction block, and 004 relies on being fed statement by
    statement so its own BEGIN starts only after the aggregates are built.
    """
    rollups = next(m for m in discover() if m.version == 4)
    stmts = split_statements(rollups.sql)

    first_begin = next(i for i, s in enumerate(stmts) if code(s).upper() == "BEGIN")
    caggs = [i for i, s in enumerate(stmts) if "timescaledb.continuous" in s]

    assert caggs, "expected continuous aggregates in 004"
    assert all(i < first_begin for i in caggs)
