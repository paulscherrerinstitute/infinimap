# ibfabric

Parse an `ibdiagnet2.db_csv` snapshot into a **fabric graph** - the foundation for a live InfiniBand topology map.

This first pass does one thing: read a single `db_csv` file into in-memory dataclasses and assemble them into a `FabricGraph`. No database, no history, no daemon yet.

## Usage

```bash
# print a summary of a snapshot
python3 -m ibfabric /.../ibdiagnet2.db_csv
```

```python
from ibfabric import build_graph, LinkStatus

g = build_graph("/.../ibdiagnet2.db_csv")

print(g.counts())                       # {'nodes': 203, 'switches': 13, 'links': 270, ...}
for link in g.unhealthy_links():        # links that aren't OK
    print(link.status, link.a.node_guid, link.a.port_num)

node = g.nodes[0x08c0eb0300cbc456]      # by GUID
for peer in g.neighbors(node.guid):     # connected nodes
    print(peer.desc)
```

## Model

- **`FabricGraph`** - the whole snapshot: `nodes` (by GUID), `links`, `switches`, `sm`,
  `check_events`, plus provenance (`run_timestamp`, tool `versions`, `args`).
- **`Node`** - a switch/HCA; holds its `ports` (by port number).
- **`Port`** - link state (`state`, width/speed/FEC), with `counters` (PM_INFO),
  `counters_delta` (PM_DELTA), and `cable` (optics) attached.
- **`Link`** - an edge between two `Port`s;
  `LinkStatus` (`ok` / `degraded` / `down` / `unknown`) for map coloring.
- **`PortCounters`, `Cable`, `Switch`, `SmInfo`, `LinkCheckEvent`** - supporting data.

Link health (simple, for now): `down` if an endpoint isn't Active; `degraded` if the
port was flagged by a speed/width check or has nonzero error counters in the delta;
`ok` otherwise.

## What it reads

Only `db_csv`, the master structured file. Sections consumed: `RUN_INFO`, `NODES`,
`PORTS`, `LINKS`, `PM_INFO`, `PM_DELTA`, `CABLE_INFO`, `SWITCHES`, `SM_INFO`,
`ERRORS_LINKS_SPEED_CHECK`, `ERRORS_LINKS_WIDTH_CHECK`, `WARNINGS_FW_CHECK`. Sections are
mapped by column **name**, so extra columns in other ibdiagnet versions are tolerated.

## Next steps (not in this pass)

Persist snapshots to SQLite, diff topology across snapshots (change history), and build
counter time-series — the model is shaped so these drop in without a rewrite.
