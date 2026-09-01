"""The saquery record dumps -- live, or from a local directory.

saquery asks the Subnet Administrator for its published view of the fabric. That
view is authoritative for identity and topology, and it is the only source the
schema's 001/002 tables are written from.
"""

from __future__ import annotations

import re

from .result import QueryResult, read_fixture, run_tool

# The three records the topology is built from.
RECORDS = ("NodeRecord", "LinkRecord", "PortInfoRecord")

# Records that enrich a sweep without being load-bearing.
OPTIONAL = ("SMInfoRecord",)

ALL_RECORDS = RECORDS + OPTIONAL

# Forgive the mad_dump_* family of formatter diagnostics, which are benign and
# expected in some cases (e.g. a link with width 0). The rest of the ibwarn:
# messages are real transport failures and indicate a missing record.
_BENIGN_STDERR = re.compile(r"^ibwarn:\s*(?:\[\d+\]\s*)?mad_dump_\w+:")


def errors(result: QueryResult) -> str:
    """stderr with the known-benign formatter diagnostics removed."""
    return "\n".join(
        ln for ln in result.stderr.splitlines()
        if ln.strip() and not _BENIGN_STDERR.match(ln.strip())
    )


def benign(result: QueryResult) -> int:
    """How many stderr lines were forgiven, for a debug-level count."""
    total = len([ln for ln in result.stderr.splitlines() if ln.strip()])
    return total - len(errors(result).splitlines())


def is_clean(result: QueryResult) -> bool:
    """Clean iff the process succeeded and said nothing on stderr beyond the
    benign formatter noise. Anything else counts as not-clean even at rc 0."""
    return result.rc == 0 and errors(result) == ""


def collect(from_dir: str | None) -> dict[str, QueryResult]:
    """Fetch every record, required and optional. Returns record -> QueryResult."""
    if from_dir is None:
        return {r: run_tool(["sudo", "saquery", r], r) for r in ALL_RECORDS}
    return {r: read_fixture(from_dir, r) for r in ALL_RECORDS}
