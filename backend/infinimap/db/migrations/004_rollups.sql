-- 004_rollups.sql
--
-- Continuous aggregates, retention and compression policies, and the views that
-- turn raw cumulative readings into deltas and rates.
--
-- THE ARITHMETIC LIVES HERE, NOT IN THE FACT TABLES.
--
-- The policies below assume a 300 s error cadence and a 30 s traffic cadence,
-- which are the collector's defaults. CHANGE THEM TO MATCH.


-- ===========================================================================
-- Part 1 -- continuous aggregates.  No transaction.
-- ===========================================================================

-- Raw traffic answers questions at the sample cadence. Everything wider reads
-- a rollup.
CREATE MATERIALIZED VIEW traffic_1m
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 minute', time) AS bucket,
       port_id,
       count(*)                AS samples,
       min(time)               AS first_at,
       max(time)               AS last_at,
       first(xmit_data, time)  AS xmit_data_first,
       last(xmit_data,  time)  AS xmit_data_last,
       first(rcv_data,  time)  AS rcv_data_first,
       last(rcv_data,   time)  AS rcv_data_last,
       first(xmit_pkts, time)  AS xmit_pkts_first,
       last(xmit_pkts,  time)  AS xmit_pkts_last,
       first(rcv_pkts,  time)  AS rcv_pkts_first,
       last(rcv_pkts,   time)  AS rcv_pkts_last
  FROM port_traffic
 GROUP BY bucket, port_id
WITH NO DATA;

-- Hierarchical: built from traffic_1m.
CREATE MATERIALIZED VIEW traffic_1h
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 hour', bucket) AS bucket,
       port_id,
       sum(samples)                   AS samples,
       min(first_at)                  AS first_at,
       max(last_at)                   AS last_at,
       first(xmit_data_first, bucket) AS xmit_data_first,
       last(xmit_data_last,   bucket) AS xmit_data_last,
       first(rcv_data_first,  bucket) AS rcv_data_first,
       last(rcv_data_last,    bucket) AS rcv_data_last,
       first(xmit_pkts_first, bucket) AS xmit_pkts_first,
       last(xmit_pkts_last,   bucket) AS xmit_pkts_last,
       first(rcv_pkts_first,  bucket) AS rcv_pkts_first,
       last(rcv_pkts_last,    bucket) AS rcv_pkts_last
  FROM traffic_1m
 GROUP BY 1, 2
WITH NO DATA;

-- Errors poll on a lower cadence
CREATE MATERIALIZED VIEW errors_1h
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 hour', time) AS bucket,
       port_id,
       count(*)  AS samples,
       min(time) AS first_at,
       max(time) AS last_at,
       first(symbol_error, time)             AS symbol_error_first,
       last(symbol_error,  time)             AS symbol_error_last,
       first(link_error_recovery, time)      AS link_error_recovery_first,
       last(link_error_recovery,  time)      AS link_error_recovery_last,
       first(link_downed, time)              AS link_downed_first,
       last(link_downed,  time)              AS link_downed_last,
       first(rcv_errors, time)               AS rcv_errors_first,
       last(rcv_errors,  time)               AS rcv_errors_last,
       first(rcv_remote_phys_errors, time)   AS rcv_remote_phys_errors_first,
       last(rcv_remote_phys_errors,  time)   AS rcv_remote_phys_errors_last,
       first(rcv_switch_relay_errors, time)  AS rcv_switch_relay_errors_first,
       last(rcv_switch_relay_errors,  time)  AS rcv_switch_relay_errors_last,
       first(xmit_discards, time)            AS xmit_discards_first,
       last(xmit_discards,  time)            AS xmit_discards_last,
       first(xmit_constraint_errors, time)   AS xmit_constraint_errors_first,
       last(xmit_constraint_errors,  time)   AS xmit_constraint_errors_last,
       first(rcv_constraint_errors, time)    AS rcv_constraint_errors_first,
       last(rcv_constraint_errors,  time)    AS rcv_constraint_errors_last,
       first(local_link_integrity, time)     AS local_link_integrity_first,
       last(local_link_integrity,  time)     AS local_link_integrity_last,
       first(excessive_buffer_overrun, time) AS excessive_buffer_overrun_first,
       last(excessive_buffer_overrun,  time) AS excessive_buffer_overrun_last,
       first(vl15_dropped, time)             AS vl15_dropped_first,
       last(vl15_dropped,  time)             AS vl15_dropped_last,
       first(xmit_wait, time)                AS xmit_wait_first,
       last(xmit_wait,  time)                AS xmit_wait_last,
       first(qp1_dropped, time)              AS qp1_dropped_first,
       last(qp1_dropped,  time)              AS qp1_dropped_last
  FROM port_errors
 GROUP BY bucket, port_id
WITH NO DATA;


-- ===========================================================================
-- Part 2 -- policies and views.
-- ===========================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Refresh policies
--
-- start_offset must exceed the retention of the tier below, or a refresh will
-- read chunks that have already been dropped.
-- ---------------------------------------------------------------------------
SELECT add_continuous_aggregate_policy('traffic_1m',
    start_offset      => INTERVAL '2 hours',
    end_offset        => INTERVAL '1 minute',
    schedule_interval => INTERVAL '1 minute',
    if_not_exists     => true);

SELECT add_continuous_aggregate_policy('traffic_1h',
    start_offset      => INTERVAL '1 day',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => true);

SELECT add_continuous_aggregate_policy('errors_1h',
    start_offset      => INTERVAL '1 day',
    end_offset        => INTERVAL '1 hour',
    schedule_interval => INTERVAL '1 hour',
    if_not_exists     => true);


-- ---------------------------------------------------------------------------
-- Compression and retention
--
-- One day behind the write head. Backfill into a compressed chunk works but is
-- slow; decompress_chunk() first when re-ingesting an old dump.
-- ---------------------------------------------------------------------------
SELECT add_compression_policy('port_traffic', INTERVAL '1 day', if_not_exists => true);
SELECT add_compression_policy('port_errors',  INTERVAL '1 day');

-- Raw traffic is the only expensive tier.
SELECT add_retention_policy('port_traffic', INTERVAL '30 days', if_not_exists => true);
SELECT add_retention_policy('traffic_1m',   INTERVAL '1 year', if_not_exists => true);
SELECT add_retention_policy('port_errors', INTERVAL '1 year', if_not_exists => true);


-- ---------------------------------------------------------------------------
-- Capability inference
--
-- Fills in port_pm_caps from data already stored, on a schedule, without
-- talking to the fabric.
--
-- A reading STRICTLY ABOVE a narrow ceiling cannot have come from the narrow
-- attribute: PortCounters (0x12) gives SymbolErrorCounter sixteen bits and
-- LocalLinkIntegrityErrors four, and IBTA fields saturate rather than wrap. A
-- stored 300,986 in xmit_discards is therefore proof that the port answers from
-- the additional extended attribute and its error counters are 64 bits. The
-- same argument on port_traffic proves wide_data against the 32-bit
-- PortCounters data fields.
--
-- Worth doing because narrow is the default, and narrow is what
-- port_counter_pegged compares against: an unprobed wide port whose counter has
-- climbed past a narrow ceiling appears there as pegged. Inference removes it.
--
-- STRICTLY greater, never >=. A value sitting exactly ON a ceiling is the
-- ambiguous case -- a pegged narrow field and a wide field that happens to hold
-- that number are indistinguishable -- and reading it as proof of width would
-- promote exactly the ports that are broken, hiding them.
--
-- One-way, and enforced rather than assumed: the UPDATE never fires on a row
-- whose source is 'classportinfo', so a probe always wins and a port it
-- established as narrow is never demoted by a later reading. Inference cannot
-- prove a port NARROW, and says nothing about xmit_wait_sup or qp1_drop_sup --
-- those need the mask.
-- ---------------------------------------------------------------------------
CREATE OR REPLACE PROCEDURE promote_wide_caps(job_id INT DEFAULT 0,
                                   config JSONB DEFAULT NULL)
LANGUAGE plpgsql AS $$
DECLARE
    -- Promotion is permanent, so the window only has to cover ground since the
    -- last run; the default overlaps the hourly schedule twice, so a late or
    -- skipped run loses nothing. Pass '{"lookback":"all"}' for a one-off pass
    -- over existing history.
    lookback TEXT := COALESCE(config->>'lookback', '2 hours');
    since    TIMESTAMPTZ;
    n_err    INT;
    n_dat    INT;
BEGIN
    since := CASE WHEN lookback = 'all' THEN '-infinity'::timestamptz
                  ELSE now() - lookback::interval END;

    -- Error counters -> wide_errors.
    WITH proven AS (
        SELECT DISTINCT e.port_id
          FROM port_errors e
         WHERE e.time >= since
           AND (e.symbol_error             > 65535
             OR e.link_error_recovery      > 255
             OR e.link_downed              > 255
             OR e.rcv_errors               > 65535
             OR e.rcv_remote_phys_errors   > 65535
             OR e.rcv_switch_relay_errors  > 65535
             OR e.xmit_discards            > 65535
             OR e.xmit_constraint_errors   > 255
             OR e.rcv_constraint_errors    > 255
             OR e.local_link_integrity     > 15
             OR e.excessive_buffer_overrun > 15
             OR e.vl15_dropped             > 65535
             OR e.xmit_wait                > 4294967295)
           -- Skip what is already known wide: the set shrinks over time to the
           -- ports nothing has proved yet, which is what keeps the job cheap.
           AND NOT EXISTS (SELECT 1 FROM port_pm_caps c
                            WHERE c.port_id = e.port_id AND c.wide_errors)
    )
    INSERT INTO port_pm_caps (port_id, wide_errors, source, observed_at)
    SELECT port_id, true, 'inferred', now() FROM proven
    ON CONFLICT (port_id) DO UPDATE
       SET wide_errors = true, source = 'inferred', observed_at = now()
     WHERE port_pm_caps.source <> 'classportinfo';
    GET DIAGNOSTICS n_err = ROW_COUNT;

    -- Data counters -> wide_data. The PortCounters forms are 32 bits, so
    -- anything past 2^32-1 came from PortCountersExtended (0x1D).
    WITH proven AS (
        SELECT DISTINCT t.port_id
          FROM port_traffic t
         WHERE t.time >= since
           AND (t.xmit_data > 4294967295 OR t.rcv_data > 4294967295
             OR t.xmit_pkts > 4294967295 OR t.rcv_pkts > 4294967295)
           AND NOT EXISTS (SELECT 1 FROM port_pm_caps c
                            WHERE c.port_id = t.port_id AND c.wide_data)
    )
    INSERT INTO port_pm_caps (port_id, wide_data, source, observed_at)
    SELECT port_id, true, 'inferred', now() FROM proven
    ON CONFLICT (port_id) DO UPDATE
       SET wide_data = true, source = 'inferred', observed_at = now()
     WHERE port_pm_caps.source <> 'classportinfo';
    GET DIAGNOSTICS n_dat = ROW_COUNT;

    IF n_err > 0 OR n_dat > 0 THEN
        RAISE LOG 'promote_wide_caps: % port(s) proved wide_errors, % wide_data '
                  '(since %)', n_err, n_dat, since;
    END IF;
END $$;

COMMENT ON PROCEDURE promote_wide_caps IS
    'Populates port_pm_caps from stored readings. Costs no MADs and touches no '
    'hardware. Safe to call directly: CALL promote_wide_caps(0, '
    '''{"lookback":"all"}'') does the one-off pass over existing history that '
    'the hourly job does not.';

-- Hourly, on the same TimescaleDB scheduler as the policies above.
SELECT delete_job(job_id) FROM timescaledb_information.jobs
 WHERE proc_name = 'promote_wide_caps';
SELECT add_job('promote_wide_caps', INTERVAL '1 hour');

-- One-off catch-up over history already collected.
CALL promote_wide_caps(0, '{"lookback":"all"}');



-- ---------------------------------------------------------------------------
-- Pegged counters
--
-- A counter sitting at its maximum is dead: IBTA fields saturate rather than
-- wrap, so it reports a delta of zero for every future interval until somebody
-- clears it -- and zero is what a healthy port looks like. Nothing in the delta
-- stream reveals this; the evidence is the raw reading.
--
-- The ceilings are 2^n - 1 for the widths the specification gives PortCounters
-- (0x12), written as literals because they are constants of the protocol.
--
-- COALESCE defaults wide_errors to false, so a port with no caps row is
-- compared against the narrow ceilings and may appear here wrongly. That is the
-- deliberate direction to be wrong in: a false positive is a port somebody
-- looks at and clears, a false negative is a port silently unmonitored for that
-- counter. Read cap_source to see which rows rest on a probe and which on an
-- assumption.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS port_counter_pegged;
CREATE VIEW port_counter_pegged AS
SELECT l.port_id,
       pt.fabric_id,
       l.time      AS as_of,
       p.counter,
       p.value,
       p.ceiling,
       COALESCE(c.source, 'assumed') AS cap_source
  FROM (SELECT DISTINCT ON (port_id) *
          FROM port_errors
         ORDER BY port_id, time DESC) l
  JOIN port pt ON pt.port_id = l.port_id
  LEFT JOIN port_pm_caps c ON c.port_id = l.port_id
 CROSS JOIN LATERAL (VALUES
        ('SymbolErrorCounter',           l.symbol_error,             65535::bigint),
        ('LinkErrorRecoveryCounter',     l.link_error_recovery,        255::bigint),
        ('LinkDownedCounter',            l.link_downed,                255::bigint),
        ('PortRcvErrors',                l.rcv_errors,               65535::bigint),
        ('PortRcvRemotePhysicalErrors',  l.rcv_remote_phys_errors,   65535::bigint),
        ('PortRcvSwitchRelayErrors',     l.rcv_switch_relay_errors,  65535::bigint),
        ('PortXmitDiscards',             l.xmit_discards,            65535::bigint),
        ('PortXmitConstraintErrors',     l.xmit_constraint_errors,     255::bigint),
        ('PortRcvConstraintErrors',      l.rcv_constraint_errors,      255::bigint),
        ('LocalLinkIntegrityErrors',     l.local_link_integrity,        15::bigint),
        ('ExcessiveBufferOverrunErrors', l.excessive_buffer_overrun,    15::bigint),
        ('VL15Dropped',                  l.vl15_dropped,             65535::bigint),
        ('PortXmitWait',                 l.xmit_wait,           4294967295::bigint)
 ) AS p(counter, value, ceiling)
 WHERE NOT COALESCE(c.wide_errors, false)
   AND p.value >= p.ceiling;

COMMENT ON VIEW port_counter_pegged IS
    'Ports whose counters have stopped counting: silently unmonitored for that '
    'counter, and clearing them is the only remedy. QP1Dropped is absent '
    'because a port that does not support it reports a constant zero, which '
    'pegs nothing. ';


-- ---------------------------------------------------------------------------
-- Sweeps whose timing makes their readings untrustworthy
--
-- A sweep that took materially longer than its cadence had a MAD time out in
-- it, and every port polled after that timeout carries a shifted sample time.
-- The rates that follow are not obviously wrong, which is why this needs
-- surfacing.
--
-- The thresholds are empirical. Set them from the collector's observed timings.
-- ---------------------------------------------------------------------------
DROP VIEW IF EXISTS counter_sweep_degraded;
CREATE VIEW counter_sweep_degraded AS
SELECT sweep_id,
       fabric_id,
       source,
       started_at,
       finished_at - started_at AS duration,
       ports_polled,
       ports_polled - ports_written AS ports_lost
  FROM counter_sweep
 WHERE ports_written < ports_polled
    OR (source = 'traffic' AND finished_at - started_at > INTERVAL '1 second')
    OR (source = 'errors'  AND finished_at - started_at > INTERVAL '3 seconds');

COMMIT;
