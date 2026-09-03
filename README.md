# infinimap

[![CI](https://github.com/paulscherrerinstitute/infinimap/actions/workflows/ci.yml/badge.svg)](https://github.com/paulscherrerinstitute/infinimap/actions/workflows/ci.yml)

A live, time-aware map of an InfiniBand fabric.

It answers two questions: **what is broken now**, and **what changed, when, and
what did it look like before**. Every collection run is recorded, so the fabric
has a history you can scrub through.

## What it does

- **Topology map.** The fabric as an interactive graph - switches, HCAs and the
  links between them, coloured by health: `ok`, `degraded`, `down`, `unknown`.
- **Time travel.** Any view can be asked for at a past instant. A slider moves
  the whole map through recorded history.
- **Diffs and events.** What appeared, disappeared or changed between two
  instants, including links that flapped and came back.
- **Counters.** Per-port error deltas and transmit/receive rates over a window.
- **Detail on demand.** Click a node or a link for port state, widths and speeds,
  firmware, cables, and the reason a link is not healthy.

## How it fits together

```
  fabric node                          server
┌──────────────┐              ┌──────────────────────────┐
│  collector   │─── writes ──▶│  PostgreSQL + TimescaleDB│
│              │              │            ▲             │
│ saquery      │              │            │ reads       │
│ ibqueryerrors│              │  ┌─────────┴──────────┐  │
└──────────────┘              │  │  API  +  web UI    │◀─┼── browser
                              │  └────────────────────┘  │
                              └──────────────────────────┘
```

Only the **collector** has to run on a fabric-attached node - it shells out to
`saquery` and `ibqueryerrors`. Everything else can live anywhere that can reach
the database. The API serves the web UI itself, so there is one process, one
port, and no CORS or reverse proxy to configure.

Both halves ship in one package. Install the part each machine needs.

## Requirements

| | |
|---|---|
| Python | 3.11 or newer |
| Database | **TimescaleDB** - plain PostgreSQL is not enough |
| Extensions | `timescaledb`, plus `btree_gist` and `intarray` from **contrib** |
| Fabric node | `saquery` and `ibqueryerrors` (from `infiniband-diags`) |

`btree_gist` and `intarray` are not optional extras; Debian, Ubuntu and 
the TimescaleDB Docker image bundle contrib; **RHEL and PGDG ship it
separately**:

```bash
sudo dnf install postgresql18-contrib          # match your major version
```

`infinimap-db check` reports any that are missing, and `init` and `migrate`
refuse to start if so.

## Install

### Getting the package

Not on PyPI yet, so `pip` has nothing to look up by name. Build **one** wheel and
copy it to both machines:

```bash
# once, on any machine with the repo, Python and Node:
./packaging/build-wheel.sh                                   # -> dist/*.whl
scp dist/infinimap-*.whl SERVER: FABRICNODE:

# on the server -- as root, see below:
sudo python3.11 -m pip install './infinimap-0.1.0-py3-none-any.whl[server]'

# on the fabric node:
pip install './infinimap-0.1.0-py3-none-any.whl[collector]'
```

The same wheel serves both roles; the extra selects which dependencies come with
it.

The `sudo` on the server is not decoration. Without it pip installs into
`~/.local/bin`, and the `init` step below runs `infinimap-db` as the `postgres`
user, which cannot read your home directory - the failure is a bare `Permission
denied`. On a fabric node a user install is fine.

Build with that script rather than `python -m build` directly: **`pip` cannot run
`npm`**, so the UI is only inside the wheel if the build put it there first. The
script does that and then verifies the artefact really contains it.

### Server - database, API and UI

```bash
# Tells you exactly what is missing.
infinimap-db check

# ...install TimescaleDB per that output, then preload it. On RHEL pg_config is
# not on root's PATH, so name it:
#   sudo timescaledb-tune --pg-config=/usr/pgsql-18/bin/pg_config
#   sudo systemctl restart postgresql-18

# Creates the database, the extension, two least-privilege roles, and applies
# every migration. The only step that needs a superuser.
sudo -u postgres $(command -v infinimap-db) init --dsn postgresql:///postgres

# init just printed the two role passwords -- this is the only time it will.
# Give the API its one (must be @localhost over TCP: the Unix socket
# authenticates by OS user, which is not infinimap_api):
cat > api.toml <<'EOF'
dsn = "postgresql://infinimap_api:PASSWORD@localhost/infinimap"
EOF
chmod 600 api.toml                   # it holds a password

infinimap-api --config api.toml      # API and UI on http://localhost:8000
```

`init` prints the generated role passwords once. The API connects read-only; the
collector gets write access but not `DELETE`, so a fault on the collecting side
cannot destroy history.

### Reaching it from other machines

The API and PostgreSQL both listen on localhost only by default. The paths and
the service name below are PGDG's on RHEL, which carry the major version -
substitute yours for the `18`.

**The API, for browsers.** It is unauthenticated (see
[Operating](#operating)), so to look at it without exposing it, tunnel instead:
`ssh -L 8000:localhost:8000 SERVER`. To serve the network directly, add
`host = "0.0.0.0"` to `api.toml` and open the port:

```bash
sudo firewall-cmd --add-port=8000/tcp --permanent && sudo firewall-cmd --reload
```

**PostgreSQL, for the collector.** Three server-side changes:

```bash
# Accept the collector role, on this database only, password required:
echo 'host infinimap infinimap_collector 0.0.0.0/0 scram-sha-256' \
  | sudo tee -a /var/lib/pgsql/18/data/pg_hba.conf

# Listen on the network, not just loopback:
sudo -u postgres psql -c "ALTER SYSTEM SET listen_addresses = '*'"

# Restart (listen_addresses ignores reloads), then open the port:
sudo systemctl restart postgresql-18
sudo firewall-cmd --add-port=5432/tcp --permanent && sudo firewall-cmd --reload
```

To admit less than the whole network, tighten the third column of the
`pg_hba.conf` line: `10.7.3.21/32` for one collector, `10.7.3.0/24` for a
subnet, or `samenet` for any subnet the server sits on directly. After editing,
`sudo systemctl reload postgresql-18` is enough.

### Fabric node - collector

```bash
# Prove it can reach the database before committing to a loop.
infinimap-db check --dsn 'postgresql://infinimap_collector:PASSWORD@SERVER/infinimap'

# Persist the same DSN so every later run finds it:
cat > collector.toml <<'EOF'
dsn = "postgresql://infinimap_collector:PASSWORD@SERVER/infinimap"
EOF
chmod 600 collector.toml

infinimap-collector --once --config collector.toml   # one sweep, to see it work
infinimap-collector --loop --config collector.toml   # continuous
```

The collector invokes `sudo saquery` and `sudo ibqueryerrors` - the account
needs passwordless sudo for exactly those two commands, and nothing else. No
other privilege is required on a fabric node: `pip install --user` and a config
file in the home directory are enough.

Nothing appears in the UI until the collector's first successful sweep - the API
returns 404 for a fabric it has never seen.

## Configuration

Settings resolve **flag → environment → config file → built-in default**.

Pass the config file with `--config FILE`; each package ships a commented
`.toml.example` next to its code. Without `--config`, the commands fall back to
`/etc/infinimap/api.toml` and `/etc/infinimap/collector.toml` if those exist.
The environment overrides the file:

| Variable | Applies to |
|---|---|
| `INFINIMAP_DSN` | both |
| `INFINIMAP_FABRIC` | both |
| `INFINIMAP_HOST`, `INFINIMAP_PORT`, `INFINIMAP_WEB_ROOT` | API |
| `INFINIMAP_INTERVAL`, `INFINIMAP_TRAFFIC_INTERVAL` | collector |
| `INFINIMAP_MAX_CLOCK_SKEW` | collector |

### Clocks

The collector stamps history from its own clock, so both halves check themselves
against the database's at startup. The collector refuses to run past
`max_clock_skew_s` (5s by default); the API only warns.

## Operating

```bash
curl -s localhost:8000/healthz
{"status":"ok","version":"0.1.0","schema_version":4,"expects_schema":4}
```

Both halves verify the schema at startup. Older than the build expects is fatal
and names the fix; newer is only a warning, so upgrading the server does not take
every collector down at once. **Upgrade the server first** - package, then
`infinimap-db migrate`, then the collectors.

The API is **unauthenticated**. Bind it to localhost and put your own
authenticating proxy in front of it if the fabric's topology and hostnames are
not something you want served to anyone who can reach the port.

## Development

```bash
git clone https://github.com/paulscherrerinstitute/infinimap
cd infinimap
pip install -e '.[server,dev]'
python -m pytest tests/

cd frontend && npm ci
npm run dev          # http://localhost:5173, proxying /api to :8000
```

`npm run dev` is for hot reload. In production the API serves the built UI from
`frontend/dist`; a released wheel carries the build inside the package.

Types are generated, so CI checks it:

```
backend/infinimap/api/models.py → openapi.json → npm run gen:api → schema.d.ts
```

Change the Pydantic model and regenerate both. Never hand-edit `schema.d.ts`.


## Licence

Not yet chosen. Until one is added, no licence is granted.
