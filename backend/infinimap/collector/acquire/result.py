"""What running one tool produced. Tool-neutral, and deliberately unjudged."""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

#: Seconds one tool invocation may take. The collector's
#: `query_timeout_s` overrides it per run.
DEFAULT_TIMEOUT_S = 120.0


@dataclass(frozen=True)
class QueryResult:
    name: str        # record name, or tool name -- also the fixture filename
    text: str        # stdout
    stderr: str
    rc: int          # return code; see the module docstring before trusting it

    @property
    def empty(self) -> bool:
        return not self.text.strip()


def run_tool(argv: list[str], name: str,
             timeout_s: float = DEFAULT_TIMEOUT_S) -> QueryResult:
    """Run a command, capturing everything. Never raises: a tool that is missing
    or hung is a QueryResult like any other, so one bad source cannot take down
    the cycle before the rest of it has been recorded."""
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout_s,
        )
    except FileNotFoundError:
        return QueryResult(name, "", f"{argv[0]} not found on PATH", 127)
    except subprocess.TimeoutExpired:
        return QueryResult(name, "", f"{' '.join(argv)} timed out", 124)
    return QueryResult(name, proc.stdout, proc.stderr, proc.returncode)


def read_fixture(directory: str | Path, name: str) -> QueryResult:
    """Read one tool's output from a directory of files named after the tool."""
    path = Path(directory) / name
    try:
        return QueryResult(name, path.read_text(errors="replace"), "", 0)
    except FileNotFoundError:
        return QueryResult(name, "", f"fixture {path} not found", 2)
