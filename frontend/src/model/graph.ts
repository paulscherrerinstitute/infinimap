// What the graph draws, from either endpoint.
//
// A /diff response contains everything a /topology response does: every diff
// entry carries `after ?? before`, so a diff is a topology plus two fields.

import type {
  Change, Counts, Diff, Health, LinkElement, NodeElement, NodeKind, Resolved,
  SystemGroup, Topology,
} from "../api/types";

export interface GraphElement<T> {
  /** The element to draw: `after ?? before`, so always present. */
  el: T;
  change: Change;
  /** Events touching this element in the window; 0 outside compare mode. */
  churn: number;
  before?: T;
  after?: T;
}

export type GraphNode = GraphElement<NodeElement>;
export type GraphLink = GraphElement<LinkElement>;

export interface GraphModel {
  mode: "single" | "compare";
  nodes: GraphNode[];
  links: GraphLink[];
  systemGroups: Record<string, SystemGroup>;
  /** The sweep that answered. In compare mode, the later one. */
  resolved: Resolved;
  /** Compare mode only: the earlier sweep. */
  since?: Resolved;
  counts?: Counts;
  /** Compare mode only: true when the net diff is provably empty. */
  unchangedGuaranteed?: boolean;
}

// ---- producers ------------------------------------------------------------

const asUnchanged = <T>(el: T): GraphElement<T> =>
  ({ el, change: "unchanged", churn: 0, after: el });

export function fromTopology(t: Topology): GraphModel {
  return {
    mode: "single",
    nodes: t.nodes.map(asUnchanged),
    links: t.links.map(asUnchanged),
    systemGroups: t.system_groups,
    resolved: t.resolved,
    counts: t.counts,
  };
}

export function fromDiff(d: Diff): GraphModel {
  return {
    mode: "compare",
    nodes: d.nodes.map(toElement),
    links: d.links.map(toElement),
    // Optional in the schema because pydantic builds it from a default_factory,
    // though the server always sends it.
    systemGroups: d.system_groups ?? {},
    resolved: d.until,
    since: d.since,
    unchangedGuaranteed: d.unchanged_guaranteed,
  };
}

/** A diff entry to a drawable element.
 *
 *  `after ?? before` is the whole trick: `removed` carries only `before`,
 *  everything else carries `after`, so exactly one of them is always there. */
function toElement<T>(d: {
  change: Change; churn: number;
  before?: T | null; after?: T | null;
}): GraphElement<T> {
  return {
    el: (d.after ?? d.before) as T,
    change: d.change,
    churn: d.churn,
    before: d.before ?? undefined,
    after: d.after ?? undefined,
  };
}

// ---- reading --------------------------------------------------------------

/** Worth its own name because "did anything happen here" is not the same
 *  question as "is this different". A link that flapped two hundred times and
 *  came back is `unchanged` with churn in the hundreds, and is usually the
 *  most interesting thing on the screen - so a plain change filter would hide
 *  exactly the elements worth looking at. */
export const isInteresting = <T>(g: GraphElement<T>): boolean =>
  g.change !== "unchanged" || g.churn > 0;


// ---- counting -------------------------------------------------------------

export interface Tally {
  nodes: number;
  links: number;
  kinds: Partial<Record<NodeKind, number>>;
  /** Over links, matching what the server's `Counts.health` reported. */
  health: Partial<Record<Health, number>>;
  change: Partial<Record<Change, number>>;
  /** Elements that churned without a net change - the flappers, which a change
   *  breakdown alone would file under "unchanged" and hide. */
  churned: number;
}

/**
 * Counts derived from the model rather than read off the response.
 *
 * `Topology` carries a `counts` block but `Diff` does not, so a legend reading
 * the server's numbers goes blank in compare mode - and the per-change counts it
 * needs there have to be computed here anyway. Deriving both from the model is
 * one code path instead of two, and it cannot disagree with what is drawn.
 */
export function tally(m: GraphModel): Tally {
  const t: Tally = {
    nodes: m.nodes.length,
    links: m.links.length,
    kinds: {},
    health: {},
    change: {},
    churned: 0,
  };

  const bump = <K extends string>(into: Partial<Record<K, number>>, key: K) => {
    into[key] = (into[key] ?? 0) + 1;
  };

  for (const n of m.nodes) {
    bump(t.kinds, n.el.type);
    bump(t.change, n.change);
    if (n.change === "unchanged" && n.churn > 0) t.churned += 1;
  }
  for (const l of m.links) {
    bump(t.health, l.el.health);
    bump(t.change, l.change);
    if (l.change === "unchanged" && l.churn > 0) t.churned += 1;
  }
  return t;
}
