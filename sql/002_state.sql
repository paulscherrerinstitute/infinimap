-- 002_state.sql
--
-- The state layer: what the hardware reports about itself, over time.
--
-- Everything here is temporally versioned.  Nothing is updated in place except
-- to close an interval: when a value changes, the open row gets valid_to set
-- and a successor row is inserted with valid_from equal to that same instant.
--
-- The read pattern this buys:
--
--   current      WHERE valid_to IS NULL
--   at time T    WHERE valid_from <= T AND (valid_to IS NULL OR valid_to > T)

BEGIN;

CREATE EXTENSION IF NOT EXISTS btree_gist;
CREATE EXTENSION IF NOT EXISTS intarray;


-- ---------------------------------------------------------------------------
-- Provenance
--
-- One row per collection run, written unconditionally -- whether or not
-- anything changed.  Makes silence readable.
-- ---------------------------------------------------------------------------
CREATE TYPE snapshot_source AS ENUM ('sa_query', 'ibdiagnet');
CREATE TYPE snapshot_origin AS ENUM ('live', 'from_file');

CREATE TABLE topology_snapshot (
    snapshot_id    BIGINT          PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    fabric_id      INTEGER         NOT NULL REFERENCES fabric,
    collected_at   TIMESTAMPTZ     NOT NULL,
    source         snapshot_source NOT NULL,
    topology_hash  BYTEA           NOT NULL,
    changed        BOOLEAN         NOT NULL,
    complete       BOOLEAN         NOT NULL,
    node_count     INTEGER         NOT NULL,
    port_count     INTEGER         NOT NULL,
    link_count     INTEGER         NOT NULL,
    duration_ms    INTEGER,
    origin         snapshot_origin NOT NULL DEFAULT 'live',

    CONSTRAINT snapshot_counts_sane
        CHECK (node_count >= 0 AND port_count >= 0 AND link_count >= 0)
);

COMMENT ON COLUMN topology_snapshot.origin IS
    'Whether the records were fetched from the fabric or read from a directory '
    'of dumps.';

COMMENT ON COLUMN topology_snapshot.duration_ms IS
    'Wall time to acquire and assemble the observation, excluding the database '
    'write.';

COMMENT ON COLUMN topology_snapshot.complete IS
    'False when the collector knows the sweep was truncated. The writer must '
    'refuse to diff an incomplete sweep';

COMMENT ON COLUMN topology_snapshot.topology_hash IS
    'Hash over the canonicalised topology.';

CREATE INDEX snapshot_by_time ON topology_snapshot (fabric_id, collected_at DESC);


-- ---------------------------------------------------------------------------
-- Shared temporal constraint
--
-- Every state table repeats the same three rules, written out per table
-- because exclusion constraints cannot be inherited:
--
--   1. valid_to, if set, must be strictly after valid_from
--   2. no two rows for the same entity may overlap in time
--   3. a partial index over the open rows, for the hot "current" query
-- ---------------------------------------------------------------------------


-- ---------------------------------------------------------------------------
-- Node state
-- ---------------------------------------------------------------------------
CREATE TYPE sm_state AS ENUM ('none', 'discovering', 'standby', 'master');

CREATE TABLE node_state (
    node_id      INTEGER     NOT NULL REFERENCES node,
    valid_from   TIMESTAMPTZ NOT NULL,
    valid_to     TIMESTAMPTZ,
    snapshot_id  BIGINT      NOT NULL REFERENCES topology_snapshot,

    node_desc    TEXT,
    fw_version   TEXT,
    sm_state     sm_state    NOT NULL DEFAULT 'none',

    PRIMARY KEY (node_id, valid_from),

    CONSTRAINT node_state_interval_ordered
        CHECK (valid_to IS NULL OR valid_to > valid_from),

    CONSTRAINT node_state_no_overlap
        EXCLUDE USING gist (
            node_id WITH =,
            tstzrange(valid_from, valid_to) WITH &&
        )
);

COMMENT ON COLUMN node_state.node_desc IS
    'The human-readable name. State rather than identity, because admins '
    'rename things - and the rename belongs on a timeline.';

CREATE INDEX node_state_current ON node_state (node_id)
    WHERE valid_to IS NULL;


-- ---------------------------------------------------------------------------
-- Port state
--
-- Speed and width are stored as the raw masks the hardware reports; decoding
-- is presentation and belongs in the application. Speed merges the base and 
-- extended fields as (ext << 8) | base, so the column is wider than the 
-- on-wire base field.
--
-- The enabled and supported masks sit alongside the active values because that
-- comparison is what makes degradation detectable.
-- ---------------------------------------------------------------------------
CREATE TYPE port_log_state  AS ENUM ('down', 'init', 'armed', 'active');
CREATE TYPE port_phys_state AS ENUM (
    'sleep', 'polling', 'disabled', 'training',
    'linkup', 'link_error_recovery', 'phy_test'
);

CREATE TABLE port_state (
    port_id      INTEGER     NOT NULL REFERENCES port,
    valid_from   TIMESTAMPTZ NOT NULL,
    valid_to     TIMESTAMPTZ,
    snapshot_id  BIGINT      NOT NULL REFERENCES topology_snapshot,

    log_state    port_log_state,
    phys_state   port_phys_state,

    active_speed     INTEGER,
    active_width     SMALLINT,
    enabled_speed    INTEGER,
    enabled_width    SMALLINT,
    supported_speed  INTEGER,
    supported_width  SMALLINT,

    lid          INTEGER,
    mtu          SMALLINT,   -- raw IBTA enum; see port_state_mtu_is_enum

    PRIMARY KEY (port_id, valid_from),

    CONSTRAINT port_state_interval_ordered
        CHECK (valid_to IS NULL OR valid_to > valid_from),

    CONSTRAINT port_state_lid_valid
        CHECK (lid IS NULL OR lid BETWEEN 0 AND 65535),

    -- Guards the encoding. saquery prints MTU decoded to bytes (2048, 4096)
    -- while the schema stores the raw enum.
    CONSTRAINT port_state_mtu_is_enum
        CHECK (mtu IS NULL OR mtu BETWEEN 1 AND 5),

    CONSTRAINT port_state_no_overlap
        EXCLUDE USING gist (
            port_id WITH =,
            tstzrange(valid_from, valid_to) WITH &&
        )
);

CREATE INDEX port_state_current ON port_state (port_id)
    WHERE valid_to IS NULL;

COMMENT ON COLUMN port_state.lid IS
    'SM-assigned, so state rather than identity.';

COMMENT ON COLUMN port_state.mtu IS
    'Raw IBTA MTU enum: 1=256, 2=512, 3=1024, 4=2048, 5=4096. Value 0 comes '
    'from ports that answer the MAD with zeros and must be normalised to NULL '
    'at ingest, not stored.';

COMMENT ON COLUMN port_state.log_state IS
    'NULL where the port reported no usable state -- in practice switch port 0, '
    'whose firmware may return zeros rather than invent a link state for what '
    'is an internal path to the SMA. Vendor-dependent.';

COMMENT ON COLUMN port_state.active_width IS
    'Meaningful only while log_state = active. A down port may report width 0 '
    'or retain its last negotiated value, so the active-vs-enabled degradation '
    'check must be gated on the port being up.';

CREATE INDEX port_state_by_lid ON port_state (lid)
    WHERE valid_to IS NULL AND lid IS NOT NULL;


-- ---------------------------------------------------------------------------
-- Link state
--
-- No separate identity table: the canonicalised port pair IS the stable
-- identity.
--
-- Identity survives a down/up cycle -- unplug at 14:00, replug at 14:30, and
-- that is one link with two intervals. This matters because things point at
-- links: cable inventory, materialised health, and operator annotations
-- ("known bad, ticket #4471"). Under per-interval identity a cable blip
-- orphans those against a dead reference.
-- ---------------------------------------------------------------------------
CREATE TABLE link_state (
    port_a_id    INTEGER     NOT NULL REFERENCES port,
    port_b_id    INTEGER     NOT NULL REFERENCES port,
    valid_from   TIMESTAMPTZ NOT NULL,
    valid_to     TIMESTAMPTZ,
    snapshot_id  BIGINT      NOT NULL REFERENCES topology_snapshot,

    PRIMARY KEY (port_a_id, port_b_id, valid_from),

    -- Canonical ordering. Without it the same physical cable acquires a
    -- different identity depending on which end the scan walked first.
    CONSTRAINT link_canonical_order CHECK (port_a_id < port_b_id),

    CONSTRAINT link_state_interval_ordered
        CHECK (valid_to IS NULL OR valid_to > valid_from),

    -- One socket, one cable: a port may be in at most one link at any instant.
    CONSTRAINT link_one_cable_per_port
        EXCLUDE USING gist (
            (ARRAY[port_a_id, port_b_id]) gist__int_ops WITH &&,
            tstzrange(valid_from, valid_to) WITH &&
        )
);

COMMENT ON TABLE link_state IS
    'Data ports only. Switch port 0 is an internal path to the SMA, never a '
    'cable end, so health rules that walk links never see it.';

CREATE INDEX link_state_current ON link_state (port_a_id, port_b_id)
    WHERE valid_to IS NULL;

CREATE INDEX link_state_by_port_b ON link_state (port_b_id)
    WHERE valid_to IS NULL;


-- ---------------------------------------------------------------------------
-- Event feed
--
-- Strictly redundant - every event here could be reconstructed by diffing
-- validity intervals across the state tables - but that reconstruction is a
-- self-join per event type, and the frontend timeline wants a flat
-- chronological list it can pull with one indexed range scan.
--
-- It also holds things that are not state transitions at all: a counter reset,
-- an incomplete sweep, an SM failover.
-- ---------------------------------------------------------------------------
CREATE TYPE topology_event_type AS ENUM (
    'node_added',    'node_removed',    'node_renamed',
    'port_added',
    'link_added',    'link_removed',
    'speed_changed', 'width_changed',   'port_state_changed',
    'sm_failover',
    'sweep_incomplete'
);

CREATE TABLE topology_event (
    event_id      BIGINT              PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    fabric_id     INTEGER             NOT NULL REFERENCES fabric,
    occurred_at   TIMESTAMPTZ         NOT NULL,
    snapshot_id   BIGINT              REFERENCES topology_snapshot,
    event_type    topology_event_type NOT NULL,

    node_id       INTEGER,
    port_id       INTEGER,
    peer_port_id  INTEGER,

    detail        JSONB,

    -- Composite rather than plain foreign keys, so an event cannot claim one
    -- fabric while pointing at hardware in another.
    CONSTRAINT event_node_fabric_agree
        FOREIGN KEY (node_id, fabric_id) REFERENCES node (node_id, fabric_id),

    CONSTRAINT event_port_fabric_agree
        FOREIGN KEY (port_id, fabric_id) REFERENCES port (port_id, fabric_id),

    CONSTRAINT event_peer_port_fabric_agree
        FOREIGN KEY (peer_port_id, fabric_id) REFERENCES port (port_id, fabric_id)
);

COMMENT ON COLUMN topology_event.detail IS
    'Event-specific payload -- old and new speed, previous name, which SM took '
    'over.';

CREATE INDEX event_by_time ON topology_event (fabric_id, occurred_at DESC);
CREATE INDEX event_by_port ON topology_event (port_id, occurred_at DESC)
    WHERE port_id IS NOT NULL;
CREATE INDEX event_by_type ON topology_event (fabric_id, event_type, occurred_at DESC);

COMMIT;
