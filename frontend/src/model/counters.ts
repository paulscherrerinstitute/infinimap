// Per-port counter deltas, rolled up to the things the graph draws.
//
// The server sends counter columns sparsely and never sums, so that ticking a
// checkbox is a re-sum here rather than a refetch.
//
// A counter belongs to a port, but most are receive-side physical-layer errors
// that indict the cable rather than the port reporting them -- so links carry
// the primary value and nodes are a rollup over their ports.
//
// Nodes roll up with `max` by default, not `sum`: summing makes every switch
// the reddest thing on screen, answering "how many ports does this have"
// rather than "does this need attention". `sum` stays available.

import type {
  CounterName, ErrorDeltas, PortErrorDelta, TrafficRates,
} from "../api/types";
import type { GraphModel } from "./graph";

export type Rollup = "max" | "sum";

/** The counter set, grouped by what the counter accuses -- which is what tells
 *  a reader whether to look at the edge or at the node. */
export const COUNTER_GROUPS = {
  /** The edge. A bad cable, a dirty connector, a marginal transceiver. */
  cable: [
    "symbol_error", "link_error_recovery", "link_downed", "rcv_errors",
    "rcv_remote_phys_errors", "local_link_integrity",
    "excessive_buffer_overrun",
  ],
  /** The node and its downstream. Buffers, not physics. */
  congestion: ["xmit_discards", "vl15_dropped", "qp1_dropped"],
  /** The node. Partition keys and routing - an operator mistake, not hardware. */
  config: [
    "xmit_constraint_errors", "rcv_constraint_errors",
    "rcv_switch_relay_errors",
  ],
} as const satisfies Record<string, readonly CounterName[]>;

export type CounterGroup = keyof typeof COUNTER_GROUPS;

/** `xmit_wait` gets its own congestion mask. */
export const CONGESTION_COUNTER: CounterName = "xmit_wait";

/** Cable/link counters - what you want lit up before you have picked anything. */
export const DEFAULT_SELECTION: readonly CounterName[] = COUNTER_GROUPS.cable;

export interface OverlayValues {
  /** node guid -> summed value over the selected counters. */
  nodes: Map<string, number>;
  /** link id (`edge_id` form) -> the worse of its two ends. */
  links: Map<string, number>;
  /**
   * Element ids whose number is not a measurement. Never drawn on the ramp:
   * the port went unpolled, a counter was cleared so the delta is only a
   * floor, or the port fabricates a zero for a selected counter. All three
   * read as zero and none means "fine".
   */
  untrusted: Set<string>;
  /** Largest value present, for the legend's upper label. */
  peak: number;
  /**
   * Selected counters no port in the fabric can report. A legend fact stated
   * once, not a per-element taint: tainting would grey the whole map the
   * moment somebody ticks a counter no hardware reports. Port-specific gating
   * is a different thing and does taint.
   */
  omitted: Set<CounterName>;
  /**
   * Nothing was measured at all, so everything in `untrusted` is there for
   * that reason rather than a per-port one. Affects wording only -- both
   * cases draw the same grey.
   */
  unmeasured: boolean;
}

const EMPTY: OverlayValues = {
  nodes: new Map(), links: new Map(), untrusted: new Set(), peak: 0,
  omitted: new Set(), unmeasured: true,
};

/** `"guid:port"` - the key both the port rows and the link ends reduce to. */
const portKey = (node: string, port: number): string => `${node}:${port}`;

/**
 * Counters no port in the fabric can report. The wire states this once in
 * `coverage` rather than per row, so a port's zero for X is fabricated if X is
 * in that port's own `unsupported` OR in coverage's.
 */
export function unsupportedEverywhere(d: ErrorDeltas): Set<CounterName> {
  const total = d.coverage.ports_expected;
  const out = new Set<CounterName>();
  if (total <= 0) return out;
  for (const [name, n] of Object.entries(d.coverage.unsupported ?? {})) {
    if (n >= total) out.add(name as CounterName);
  }
  return out;
}

/** The selected counters summed on one port, and whether that sum is honest. */
function portValue(
  p: PortErrorDelta,
  selected: ReadonlySet<CounterName>,
): { value: number; trusted: boolean } {
  // A port that was never polled has no number at all - not a zero.
  if (p.no_data) return { value: 0, trusted: false };

  let value = 0;
  const d = p.d ?? {};
  for (const name of selected) {
    value += d[name] ?? 0;
  }

  // A reset floors every delta on the port, so the sum is a lower bound.
  //
  // A fabricated zero taints only when it is specific to THIS port and the user
  // selected it - then this port's figure is incomplete next to its neighbours,
  // which is a per-element fact worth drawing. A counter no port can report is
  // not: it goes to `omitted` and is said once.
  const fabricated = (p.unsupported ?? []).some((c) => selected.has(c));
  return { value, trusted: !p.reset && !fabricated };
}

/**
 * Per-port rows to per-element values, against the topology already on screen.
 *
 * The topology is what supplies port -> link: `LinkElement` carries both ends
 * as `(guid, port)`, so the browser can do this mapping without the server
 * precomputing a rollup it could not have precomputed anyway - the sum depends
 * on a checkbox set the server never sees.
 */
export function rollup(
  deltas: ErrorDeltas | undefined,
  model: GraphModel | null | undefined,
  selected: ReadonlySet<CounterName>,
  mode: Rollup = "max",
): OverlayValues {
  if (!model) return EMPTY;

  if (!deltas || selected.size === 0 || deltas.coverage.ports_measured <= 0) {
    return unmeasured(model, deltas, selected);
  }

  // Both ends of every link, keyed the way a port row is.
  const endToLink = new Map<string, string>();
  for (const l of model.links) {
    endToLink.set(portKey(l.el.source, l.el.source_port), l.el.id);
    endToLink.set(portKey(l.el.target, l.el.target_port), l.el.id);
  }

  // Only hardware the graph is actually drawing.
  const drawn = new Set(model.nodes.map((n) => n.el.id));

  const nodes = new Map<string, number>();
  const links = new Map<string, number>();
  const untrusted = new Set<string>();

  // Seed the drawn topology at zero before applying any row.
  //
  // The payload is sparse and absence means "measured, zero, trusted".
  //
  // Reaching here means something WAS measured; `ports_measured === 0` took
  // the unmeasured path above. Ports polled and failed come back flagged
  // `no_data` and override this seed below.
  for (const n of model.nodes) nodes.set(n.el.id, 0);
  for (const l of model.links) links.set(l.el.id, 0);

  for (const p of deltas.ports) {
    if (!drawn.has(p.node)) continue;
    const { value, trusted } = portValue(p, selected);
    const linkId = endToLink.get(portKey(p.node, p.port));

    // Nodes roll up over ALL their ports, including ones in no link. Switch
    // port 0 is where vl15_dropped lands and it has no cable, so a link-only
    // rollup would render the SMA's own errors invisible.
    const seen = nodes.get(p.node);
    nodes.set(p.node, seen === undefined ? value
      : mode === "sum" ? seen + value : Math.max(seen, value));

    if (linkId !== undefined) {
      // Worse of the two ends. Summing them would conflate "A received 400 bad
      // packets" with "each end saw 200", which are different faults.
      links.set(linkId, Math.max(links.get(linkId) ?? 0, value));
    }

    if (!trusted) {
      untrusted.add(p.node);
      if (linkId !== undefined) untrusted.add(linkId);
    }
  }

  let peak = 0;
  for (const v of links.values()) peak = Math.max(peak, v);
  for (const v of nodes.values()) peak = Math.max(peak, v);

  return {
    nodes, links, untrusted, peak,
    omitted: omittedOf(deltas, selected),
    unmeasured: false,
  };
}

/** Selected counters no port in the fabric can report. A legend line, not a taint. */
function omittedOf(
  deltas: ErrorDeltas, selected: ReadonlySet<CounterName>,
): Set<CounterName> {
  const fabricWide = unsupportedEverywhere(deltas);
  const out = new Set<CounterName>();
  for (const c of selected) if (fabricWide.has(c)) out.add(c);
  return out;
}

function unmeasured(
  model: GraphModel,
  deltas: ErrorDeltas | undefined,
  selected: ReadonlySet<CounterName>,
): OverlayValues {
  const untrusted = new Set<string>();
  for (const n of model.nodes) untrusted.add(n.el.id);
  for (const l of model.links) untrusted.add(l.el.id);
  return {
    nodes: new Map(), links: new Map(), untrusted, peak: 0,
    omitted: deltas ? omittedOf(deltas, selected) : new Set(),
    unmeasured: true,
  };
}


// ---- traffic ---------------------------------------------------------------
//
// Two rollups over one payload, because "how full is this link" and "how much
// is moving through it" are different questions with different units:
//
//   rollupTraffic      utilisation, percent of each link's own line rate
//   rollupThroughput   absolute flow rate, Gbps
//
// A port in no link contributes nothing to its node's utilisation. Switch port
// 0 has no cable and no line rate, so "how full is it" has no meaning, and
// inventing a denominator would put a fabricated number on a measured ramp.
// Throughput has no divisor and so has no such gap: it counts every port that
// reported bytes, port 0 included.

/**
 * Both ends of every link, keyed the way a port row is, with the line rate to
 * divide by. Shared by both rollups below -- throughput ignores the rate, but
 * still needs the port -> link mapping to give an edge a value.
 */
function endToLink(model: GraphModel): Map<string, { id: string; rate: number | null }> {
  const out = new Map<string, { id: string; rate: number | null }>();
  for (const l of model.links) {
    const at = { id: l.el.id, rate: l.el.rate_gbps ?? null };
    out.set(portKey(l.el.source, l.el.source_port), at);
    out.set(portKey(l.el.target, l.el.target_port), at);
  }
  return out;
}

/** Both directions of one port, as a share of the link's line rate. */
const utilisationOf = (
  tx: number, rx: number, rateGbps: number | null | undefined,
): number | null =>
  rateGbps != null && rateGbps > 0
    ? (Math.max(tx, rx) / rateGbps) * 100
    : null;

/**
 * Per-port rates to per-element utilisation, against the drawn topology.
 *
 * Same contract as `rollup`, with one difference that matters: an omitted port
 * here is one the server floored as idle, which IS a measurement, where an
 * omitted port in the error payload is one that reported clean.
 */
export function rollupTraffic(
  rates: TrafficRates | undefined,
  model: GraphModel | null | undefined,
  mode: Rollup = "max",
): OverlayValues {
  if (!model) return EMPTY;
  if (!rates || rates.coverage.ports_measured <= 0) {
    return unmeasured(model, undefined, new Set());
  }

  const ends = endToLink(model);

  const drawn = new Set(model.nodes.map((n) => n.el.id));
  const nodes = new Map<string, number>();
  const links = new Map<string, number>();
  const untrusted = new Set<string>();

  // Seed at zero, as the error rollup does: an omitted port is below the idle
  // floor, which is a reading of "idle" rather than an absence of one.
  for (const n of model.nodes) nodes.set(n.el.id, 0);
  for (const l of model.links) links.set(l.el.id, 0);

  for (const p of rates.ports) {
    if (!drawn.has(p.node)) continue;
    const at = ends.get(portKey(p.node, p.port));

    // Unpolled, or a wrapped byte counter. Neither is idle -- a 32-bit
    // PortXmitData wraps in seconds at line rate, so the second is common at
    // exactly the rates worth looking at.
    if (p.no_data || p.reset) {
      untrusted.add(p.node);
      if (at) untrusted.add(at.id);
      continue;
    }

    const pct = utilisationOf(p.tx ?? 0, p.rx ?? 0, at?.rate);
    if (pct === null) {
      // Only the link is untrusted here: the node may have other ports that
      // do have a rate, and greying it for one unrated port would hide them.
      if (at) untrusted.add(at.id);
      continue;
    }

    const seen = nodes.get(p.node);
    nodes.set(p.node, seen === undefined ? pct
      : mode === "sum" ? seen + pct : Math.max(seen, pct));

    // Worse of the two ends, matching the error rollup. The ends genuinely
    // differ -- read skew routinely has a receiver counting more than the
    // sender sent.
    if (at) links.set(at.id, Math.max(links.get(at.id) ?? 0, pct));
  }

  let peak = 0;
  for (const v of links.values()) peak = Math.max(peak, v);
  for (const v of nodes.values()) peak = Math.max(peak, v);

  return { nodes, links, untrusted, peak, omitted: new Set(), unmeasured: false };
}


/**
 * Per-port rates to per-element THROUGHPUT, in Gbps, against the drawn topology.
 */
export function rollupThroughput(
  rates: TrafficRates | undefined,
  model: GraphModel | null | undefined,
  mode: Rollup = "max",
): OverlayValues {
  if (!model) return EMPTY;
  if (!rates || rates.coverage.ports_measured <= 0) {
    return unmeasured(model, undefined, new Set());
  }

  const ends = endToLink(model);
  const drawn = new Set(model.nodes.map((n) => n.el.id));
  const nodes = new Map<string, number>();
  const links = new Map<string, number>();
  const untrusted = new Set<string>();

  for (const n of model.nodes) nodes.set(n.el.id, 0);
  for (const l of model.links) links.set(l.el.id, 0);

  for (const p of rates.ports) {
    if (!drawn.has(p.node)) continue;
    const at = ends.get(portKey(p.node, p.port));

    // Unpolled, or a wrapped byte counter.
    if (p.no_data || p.reset) {
      untrusted.add(p.node);
      if (at) untrusted.add(at.id);
      continue;
    }

    // The busier direction.
    const gbps = Math.max(p.tx ?? 0, p.rx ?? 0);

    const seen = nodes.get(p.node);
    nodes.set(p.node, seen === undefined ? gbps
      : mode === "sum" ? seen + gbps : Math.max(seen, gbps));

    // Worse of the two ends, matching every other link rollup here.
    if (at) links.set(at.id, Math.max(links.get(at.id) ?? 0, gbps));
  }

  let peak = 0;
  for (const v of links.values()) peak = Math.max(peak, v);
  for (const v of nodes.values()) peak = Math.max(peak, v);

  return { nodes, links, untrusted, peak, omitted: new Set(), unmeasured: false };
}
