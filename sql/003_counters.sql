-- 003_counters.sql
--
-- Counter telemetry: errors, congestion and traffic, in one design.
--
-- ONE RULE, AND EVERYTHING FOLLOWS FROM IT
--
-- Store the raw cumulative reading, densely, one row per port per sweep.
-- Nothing is interpreted at write time.
--
-- Every link in that chain hangs off the first one. Dense storage makes the
-- previous reading the previous row and none of it is needed.
--
--
-- TWO TABLES
--
-- port_errors and port_traffic have the same shape and different columns
-- because they come from different MADs at different cadences:
--
--   port_errors    ibqueryerrors (plain).  Two MADs per port. Prints only NONZERO
--                  fields, so absence in the dump means zero and the collector
--                  materialises those zeros against the topology port set.
--
--   port_traffic   ibqueryerrors --counters.  One MAD per port (0x1D). Prints 
--                  every field including zeros, and reports every port it checks.
BEGIN;

-- ---------------------------------------------------------------------------
-- Sweep provenance
-- ---------------------------------------------------------------------------
CREATE TYPE sweep_source AS ENUM ('errors', 'traffic');

CREATE TABLE counter_sweep (
    sweep_id      BIGINT          PRIMARY KEY GENERATED ALWAYS AS IDENTITY,
    fabric_id     INTEGER         NOT NULL REFERENCES fabric,
    source        sweep_source    NOT NULL,
    started_at    TIMESTAMPTZ     NOT NULL,
    finished_at   TIMESTAMPTZ     NOT NULL,
    ports_polled  INTEGER         NOT NULL,
    ports_written INTEGER         NOT NULL,
    origin        snapshot_origin NOT NULL DEFAULT 'live',
    collector_id  TEXT,

    CONSTRAINT sweep_finished_after_start CHECK (finished_at >= started_at),

    CONSTRAINT sweep_counts_sane
        CHECK (ports_polled >= 0 AND ports_written >= 0)
);

COMMENT ON TABLE counter_sweep IS
    'One row per ibqueryerrors run, either mode.';

COMMENT ON COLUMN counter_sweep.ports_polled IS
    'How many ports the sweep reported checking.';

COMMENT ON COLUMN counter_sweep.ports_written IS
    'How many fact rows this sweep produced.';

COMMENT ON COLUMN counter_sweep.origin IS
    'Whether the sweep read the fabric or a directory of dumps.';

CREATE INDEX sweep_by_time ON counter_sweep (fabric_id, source, started_at DESC);


-- ---------------------------------------------------------------------------
-- Performance-management capabilities
--
-- What a port's counters actually mean. One row per port, rewritten
-- only when a port's masks change -- a firmware upgrade, or a different tool
-- answering.
--
-- The IBTA carries this in the PerfMgt ClassPortInfo attribute, and
-- ibqueryerrors already reads it: query_cap_mask() issues a CLASS_PORT_INFO PMA
-- query per port before anything else and keeps both masks. It then prints
-- neither, and no stock CLI exposes them -- perfquery has no ClassPortInfo
-- option.
--
-- From libibmad/iba_types.h:
--
--   CapabilityMask   bit  8  IB_PM_ALL_PORT_SELECT
--                    bit  9  IB_PM_EXT_WIDTH_SUPPORTED
--                    bit 10  IB_PM_EXT_WIDTH_NOIETF_SUP
--                    bit 11  IB_PM_SAMPLES_ONLY_SUP
--                    bit 12  IB_PM_PC_XMIT_WAIT_SUP
--                    bit 15  IB_PM_IS_QP1_DROP_SUP
--   CapabilityMask2  bit  0  IB_PM_IS_PM_KEY_SUPPORTED
--                    bit  1  IB_PM_IS_ADDL_PORT_CTRS_EXT_SUP
--
-- The mask does NOT carry per-counter widths. It says which ATTRIBUTE answers,
-- and the widths are specification constants per attribute.
--
--   bit 9 | bit 10        PortCountersExtended (0x1D) answers, so the four
--                         data/packet counters are 64 bits.
--
--   capmask2 bit 1        the ERROR counters are 64 bits too, from the
--                         additional extended attribute -- IB_PC_EXT_ERR_SYM_F
--                         through IB_PC_EXT_QP1_DROP_F, exactly the set below.
--
--   neither               the fixed PortCounters (0x12) widths: 16, 8 and 4
--                         bits, which is what port_counter_pegged compares
--                         against in 004.
--
-- Two of the bits are not about width at all, and both change what a stored
-- zero means; see the column commentcap_masks.
-- ---------------------------------------------------------------------------

-- Where the answer came from, in ascending order of trust.
CREATE TYPE cap_source AS ENUM (
    -- Never established. The narrow default, which is the safe direction to be
    -- wrong in: it over-reports saturation rather than hiding it.
    'assumed',

    -- Proved by a reading.
    -- One-way: it promotes a port and must never demote one.
    'inferred',

    -- Read from the PerfMgt ClassPortInfo. The only source that can establish a
    -- NARROW port positively, or answer the two non-width bits at all.
    'classportinfo'
);

CREATE TABLE port_pm_caps (
    port_id       INTEGER     PRIMARY KEY REFERENCES port,

    -- Kept verbatim alongside the decoded booleans.
    cap_mask      INTEGER,
    cap_mask2     INTEGER,

    wide_data     BOOLEAN     NOT NULL DEFAULT false,
    wide_errors   BOOLEAN     NOT NULL DEFAULT false,
    xmit_wait_sup BOOLEAN     NOT NULL DEFAULT true,
    qp1_drop_sup  BOOLEAN     NOT NULL DEFAULT false,

    source        cap_source  NOT NULL DEFAULT 'assumed',
    observed_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE port_pm_caps IS
    'One row per port, not per reading.';

COMMENT ON COLUMN port_pm_caps.cap_mask2 IS
    'Stored SHIFTED, exactly as ibqueryerrors keeps it: query_cap_mask() does '
    'ntohl(raw) >> 5 because CapabilityMask2 is a 27-bit field. So bit 1 here '
    'is IB_PM_IS_ADDL_PORT_CTRS_EXT_SUP.';

COMMENT ON COLUMN port_pm_caps.wide_data IS
    'CapabilityMask bit 9 or bit 10. True where the four data/packet counters' 
    'are 64 bits.';

COMMENT ON COLUMN port_pm_caps.wide_errors IS
    'CapabilityMask2 bit 1. True where the ERROR counters are also 64 bits.';

COMMENT ON COLUMN port_pm_caps.xmit_wait_sup IS
    'CapabilityMask bit 12.';

COMMENT ON COLUMN port_pm_caps.qp1_drop_sup IS
    'CapabilityMask bit 15.';

COMMENT ON COLUMN port_pm_caps.source IS
    'Lets this table be useful before the ClassPortInfo probe exists. Seed '
    'every port assumed, promote to inferred for free wherever a stored value '
    'exceeds a narrow ceiling, replace both with classportinfo once the probe '
    'lands.';

CREATE INDEX pm_caps_unprobed ON port_pm_caps (port_id)
    WHERE source <> 'classportinfo';


-- ---------------------------------------------------------------------------
-- Traffic
--
-- Four counters, from `ibqueryerrors --counters`.
--
-- Do NOT substitute --data. It emits PortXmitWait, which is tempting, but it
-- selects the ports it reports.
-- ---------------------------------------------------------------------------
CREATE TABLE port_traffic (
    time      TIMESTAMPTZ NOT NULL,
    port_id   INTEGER     NOT NULL,
    sweep_id  BIGINT      NOT NULL,

    xmit_data BIGINT      NOT NULL,   -- PortXmitData
    rcv_data  BIGINT      NOT NULL,   -- PortRcvData
    xmit_pkts BIGINT      NOT NULL,   -- PortXmitPkts
    rcv_pkts  BIGINT      NOT NULL,   -- PortRcvPkts

    -- Deduplication and the query index in one. The collector writes one row
    -- per port per sweep.
    PRIMARY KEY (port_id, time),

    CONSTRAINT traffic_non_negative
        CHECK (xmit_data >= 0 AND rcv_data >= 0
               AND xmit_pkts >= 0 AND rcv_pkts >= 0)
);

COMMENT ON COLUMN port_traffic.time IS
    'When the reading was taken.';

COMMENT ON COLUMN port_traffic.sweep_id IS
    'Carried per row rather than joined by timestamp.';

COMMENT ON COLUMN port_traffic.xmit_data IS
    'PortXmitData: total data octets DIVIDED BY 4, all VLs. Multiply by 4 for '
    'bytes.';

COMMENT ON COLUMN port_traffic.rcv_data IS
    'PortRcvData, on the same terms as xmit_data.';

COMMENT ON COLUMN port_traffic.xmit_pkts IS
    'PortXmitPkts, all VLs, link packets excluded.';

COMMENT ON COLUMN port_traffic.rcv_pkts IS
    'PortRcvPkts. Whether packets containing errors are counted is '
    'implementation-dependent in IBTA, which is one reason a peer''s xmit_pkts '
    'never exactly equals this beyond read skew.';

SELECT create_hypertable('port_traffic', 'time',
                         chunk_time_interval => INTERVAL '1 day');

CREATE INDEX traffic_by_sweep ON port_traffic (sweep_id);

ALTER TABLE port_traffic SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'port_id',
    timescaledb.compress_orderby   = 'time DESC'
);


-- ---------------------------------------------------------------------------
-- Errors and congestion
--
-- The thirteen PortCounters error fields plus PortXmitWait, from plain
-- `ibqueryerrors`.
--
-- PortXmitWait is not an error and is here anyway, because it arrives in the
-- same MAD as the ones that are.
--
-- Column names are the IBTA field names. The collector's mapping is exactly 
-- this and nothing else needs to know it:
--
--   symbol_error              SymbolErrorCounter
--   link_error_recovery       LinkErrorRecoveryCounter
--   link_downed               LinkDownedCounter
--   rcv_errors                PortRcvErrors
--   rcv_remote_phys_errors    PortRcvRemotePhysicalErrors
--   rcv_switch_relay_errors   PortRcvSwitchRelayErrors
--   xmit_discards             PortXmitDiscards
--   xmit_constraint_errors    PortXmitConstraintErrors
--   rcv_constraint_errors     PortRcvConstraintErrors
--   local_link_integrity      LocalLinkIntegrityErrors
--   excessive_buffer_overrun  ExcessiveBufferOverrunErrors
--   vl15_dropped              VL15Dropped
--   xmit_wait                 PortXmitWait
--   qp1_dropped               QP1Dropped
-- ---------------------------------------------------------------------------
CREATE TABLE port_errors (
    time     TIMESTAMPTZ NOT NULL,
    port_id  INTEGER     NOT NULL,
    sweep_id BIGINT      NOT NULL,

    symbol_error             BIGINT NOT NULL,
    link_error_recovery      BIGINT NOT NULL,
    link_downed              BIGINT NOT NULL,
    rcv_errors               BIGINT NOT NULL,
    rcv_remote_phys_errors   BIGINT NOT NULL,
    rcv_switch_relay_errors  BIGINT NOT NULL,
    xmit_discards            BIGINT NOT NULL,
    xmit_constraint_errors   BIGINT NOT NULL,
    rcv_constraint_errors    BIGINT NOT NULL,
    local_link_integrity     BIGINT NOT NULL,
    excessive_buffer_overrun BIGINT NOT NULL,
    vl15_dropped             BIGINT NOT NULL,
    xmit_wait                BIGINT NOT NULL,
    qp1_dropped              BIGINT NOT NULL,

    PRIMARY KEY (port_id, time),

    CONSTRAINT errors_non_negative CHECK (
        symbol_error >= 0 AND link_error_recovery >= 0 AND link_downed >= 0
        AND rcv_errors >= 0 AND rcv_remote_phys_errors >= 0
        AND rcv_switch_relay_errors >= 0 AND xmit_discards >= 0
        AND xmit_constraint_errors >= 0 AND rcv_constraint_errors >= 0
        AND local_link_integrity >= 0 AND excessive_buffer_overrun >= 0
        AND vl15_dropped >= 0 AND xmit_wait >= 0 AND qp1_dropped >= 0)
);

COMMENT ON TABLE port_errors IS
    'Dense: one row per port polled per sweep, zeros included.';

COMMENT ON COLUMN port_errors.xmit_wait IS
    'Ticks during which the port had data to send and sent none: no credits, or '
    'lost arbitration.';

COMMENT ON COLUMN port_errors.qp1_dropped IS
    'QP1 packets dropped. Capability-gated on CapabilityMask bit 15.';

COMMENT ON COLUMN port_errors.local_link_integrity IS
    'Local physical error threshold exceeded. FOUR BITS on a narrow port: pegs '
    'at 15, which is a number an ordinary busy link reaches.';

SELECT create_hypertable('port_errors', 'time',
                         chunk_time_interval => INTERVAL '7 days');

CREATE INDEX errors_by_sweep ON port_errors (sweep_id);

ALTER TABLE port_errors SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'port_id',
    timescaledb.compress_orderby   = 'time DESC'
);

COMMIT;
