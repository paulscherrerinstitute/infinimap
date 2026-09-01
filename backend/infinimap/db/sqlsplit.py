"""Split a SQL script into statements, the way psql does."""

from __future__ import annotations

import re

__all__ = ["split_statements", "strip_comments"]

# A dollar quote is $$ or $tag$, where tag is an identifier.
_DOLLAR_OPEN = re.compile(r"\$([A-Za-z_][A-Za-z0-9_]*)?\$")


def split_statements(sql: str) -> list[str]:
    """The non-empty statements of `sql`, in order, without their separators."""
    out: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)

    while i < n:
        ch = sql[i]

        if sql.startswith("--", i) or sql.startswith("/*", i):
            j = _end_of_comment(sql, i)
            buf.append(sql[i:j])
            i = j
            continue

        # 'string' or "identifier" -- doubling the quote escapes it.
        if ch in "'\"":
            j = _end_of_quoted(sql, i)
            buf.append(sql[i:j])
            i = j
            continue

        # $$ ... $$ / $tag$ ... $tag$ -- no escapes inside, ends at the same tag.
        if ch == "$":
            m = _DOLLAR_OPEN.match(sql, i)
            if m:
                j = _end_of_dollar(sql, m)
                buf.append(sql[i:j])
                i = j
                continue

        if ch == ";":
            stmt = "".join(buf).strip()
            if _is_executable(stmt):
                out.append(stmt)
            buf = []
            i += 1
            continue

        buf.append(ch)
        i += 1

    # A trailing statement without its semicolon still counts.
    stmt = "".join(buf).strip()
    if _is_executable(stmt):
        out.append(stmt)
    return out


def strip_comments(sql: str) -> str:
    """`sql` with comments removed, quoting respected."""
    out: list[str] = []
    i, n = 0, len(sql)

    while i < n:
        ch = sql[i]

        if sql.startswith("--", i) or sql.startswith("/*", i):
            i = _end_of_comment(sql, i)
            continue

        if ch in "'\"":
            j = _end_of_quoted(sql, i)
            out.append(sql[i:j])
            i = j
            continue

        if ch == "$":
            m = _DOLLAR_OPEN.match(sql, i)
            if m:
                j = _end_of_dollar(sql, m)
                out.append(sql[i:j])
                i = j
                continue

        out.append(ch)
        i += 1

    return "".join(out)


def _is_executable(stmt: str) -> bool:
    """False for whitespace and for fragments that are only comments."""
    return bool(strip_comments(stmt).strip())


def _end_of_comment(sql: str, start: int) -> int:
    """Index just past the comment opening at `start`."""
    n = len(sql)
    if sql.startswith("--", start):
        end = sql.find("\n", start)
        return n if end == -1 else end          # keep the newline as whitespace

    depth, j = 1, start + 2
    while j < n and depth:
        if sql.startswith("/*", j):
            depth, j = depth + 1, j + 2
        elif sql.startswith("*/", j):
            depth, j = depth - 1, j + 2
        else:
            j += 1
    return j


def _end_of_quoted(sql: str, start: int) -> int:
    """Index just past the string or identifier opening at `start`."""
    quote, n = sql[start], len(sql)
    j = start + 1
    while j < n:
        if sql[j] == quote:
            if j + 1 < n and sql[j + 1] == quote:
                j += 2
                continue
            return j + 1
        j += 1
    return n


def _end_of_dollar(sql: str, match: re.Match[str]) -> int:
    """Index just past the dollar-quoted string whose opener `match` found."""
    tag = match.group(0)
    end = sql.find(tag, match.end())
    return len(sql) if end == -1 else end + len(tag)
