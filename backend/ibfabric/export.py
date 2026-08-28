"""Dump a fabric snapshot as JSON for the frontend.

usage: python -m ibfabric.export <ibdiagnet2.db_csv> [out.json]

Writes to stdout if no output path is given.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from .parse import build_graph, load_guid_mappings
from .serialize import to_dict


def main(argv: list[str]) -> int:
    if not (2 <= len(argv) <= 3):
        print("usage: python -m ibfabric.export <ibdiagnet2.db_csv> [out.json]",
              file=sys.stderr)
        return 2

    mappings = load_guid_mappings()
    data = to_dict(build_graph(argv[1]), mappings)
    text = json.dumps(data, indent=2)

    if len(argv) == 3:
        Path(argv[2]).write_text(text)
        print(f"wrote {argv[2]} ({len(text):,} bytes)", file=sys.stderr)
    else:
        sys.stdout.write(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
