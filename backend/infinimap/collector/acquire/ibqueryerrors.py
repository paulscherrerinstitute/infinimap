"""The ibqueryerrors error/congestion dump -- live, or from a local directory.

Run plain: no -r, no --data, no --counters. Plain output is the thirteen
PortCounters error fields plus PortXmitWait.

NEVER add -k or -K. They clear the counters, which destroys the baseline every
other reader in the system depends on and breaks delta arithmetic for hours.

Do not substitute --counters for the plain run: that mode drops the error set
and PortXmitWait entirely and returns only the data counters. It is a SEPARATE
sweep at a separate cadence -- see collect_counters below -- not an alternative
to this one.
"""

from __future__ import annotations

from .result import (DEFAULT_TIMEOUT_S, QueryResult, read_fixture, run_tool)

TOOL = "ibqueryerrors"

# The traffic mode
TOOL_COUNTERS = "ibqueryerrors.counters"

# The line every complete run ends with. Its presence is the only trustworthy
# success signal this tool gives -- see is_clean.
_SUMMARY = "## Summary:"


def is_clean(result: QueryResult) -> bool:
    """Whether this run produced a usable dump."""
    return not result.empty and _SUMMARY in result.text


def collect(from_dir: str | None,
            timeout_s: float = DEFAULT_TIMEOUT_S) -> QueryResult:
    """One dump. Unlike saquery there is nothing to iterate: a single invocation
    covers the whole fabric."""
    if from_dir is None:
        return run_tool(["sudo", TOOL], TOOL, timeout_s=timeout_s)
    return read_fixture(from_dir, TOOL)


def collect_counters(from_dir: str | None,
                     timeout_s: float = DEFAULT_TIMEOUT_S) -> QueryResult:
    """One `--counters` dump: the four traffic counters for every port."""
    if from_dir is None:
        return run_tool(["sudo", TOOL, "--counters"], TOOL_COUNTERS,
                        timeout_s=timeout_s)
    return read_fixture(from_dir, TOOL_COUNTERS)
