import { COUNTER_GROUPS, CONGESTION_COUNTER } from "../../model/counters";
export { COUNTER_LABEL } from "../../cy/palette";
import type { Health, PortDetail, SelectedItem, CounterName } from "../../api/types";
import type { GraphModel } from "../../model/graph";

export interface Bucket {
  key: string;
  ids: string[];
}

export interface WorstLink {
  id: string;
  label: string;
  health: Health;
}

export interface SelectionSummary {
  total: number;
  nodeIds: string[];
  linkIds: string[];
  unresolvedIds: string[]; // Failsafe for stale or compound selections that don't resolve to a node or link.

  nodeHealth: Bucket[];
  linkHealth: Bucket[];
  nodeTypes: Bucket[];
  smRoles: Bucket[];

  /** Down/degraded links only, worst first, capped. */
  worstLinks: WorstLink[];
}

const WORST_LINKS_MAX = 5;

const SEVERITY: Record<string, number> = { down: 0, degraded: 1, unknown: 2, ok: 3 };
const severity = (h: string): number => SEVERITY[h] ?? 2;

// ---- grouping -------------------------------------------------------------

type Grouped = Map<string, string[]>;

function push(m: Grouped, key: string, id: string): void {
  const cur = m.get(key);
  if (cur) cur.push(id);
  else m.set(key, [id]);
}

function toBuckets(m: Grouped, cmp: (a: Bucket, b: Bucket) => number): Bucket[] {
  return [...m].map(([key, ids]) => ({ key, ids })).sort(cmp);
}

const byHealth = (a: Bucket, b: Bucket): number => severity(a.key) - severity(b.key);
const byCount = (a: Bucket, b: Bucket): number =>
  b.ids.length - a.ids.length || a.key.localeCompare(b.key);

// ---- main -----------------------------------------------------------------

// Aggregates a selection from the lean graph model alone.
export function summarize(model: GraphModel, selection: SelectedItem[]): SelectionSummary {
  const nodes = new Map(model.nodes.map((n) => [n.el.id, n.el]));
  const links = new Map(model.links.map((l) => [l.el.id, l.el]));

  const nodeIds: string[] = [];
  const linkIds: string[] = [];
  const unresolvedIds: string[] = [];

  const nodeHealth: Grouped = new Map();
  const linkHealth: Grouped = new Map();
  const nodeTypes: Grouped = new Map();
  const smRoles: Grouped = new Map();

  const worst: WorstLink[] = [];

  for (const item of selection) {
    if (item.kind === "node") {
      const el = nodes.get(item.id);
      if (!el) {
        // Compound parents and stale ids land here.
        unresolvedIds.push(item.id);
        continue;
      }
      nodeIds.push(item.id);
      push(nodeHealth, el.health, item.id);
      push(nodeTypes, el.type, item.id);
      if (el.sm_role) push(smRoles, el.sm_role, item.id);
      continue;
    }

    const link = links.get(item.id);
    if (!link) {
      unresolvedIds.push(item.id);
      continue;
    }
    linkIds.push(item.id);
    push(linkHealth, link.health, item.id);

    if (severity(link.health) <= 1) {
      worst.push({ id: item.id, label: linkLabel(nodes, link), health: link.health });
    }
  }

  worst.sort((a, b) => severity(a.health) - severity(b.health) || a.label.localeCompare(b.label));

  return {
    total: selection.length,
    nodeIds,
    linkIds,
    unresolvedIds,
    nodeHealth: toBuckets(nodeHealth, byHealth),
    linkHealth: toBuckets(linkHealth, byHealth),
    nodeTypes: toBuckets(nodeTypes, byCount),
    smRoles: toBuckets(smRoles, byCount),
    worstLinks: worst.slice(0, WORST_LINKS_MAX),
  };
}

// ---- shared ---------------------------------------------------------------

// The counters that count as errors
const ERROR_FIELDS: readonly CounterName[] = Object.values(COUNTER_GROUPS).flat();

// Summed error delta on a port, excluding `xmit_wait`.
export function errorCount(p: PortDetail): number {
  const c = p.counters_delta;
  if (!c) return 0;
  return ERROR_FIELDS.reduce((sum, f) => sum + (c[f] ?? 0), 0);
}

// `xmit_wait` on its own, which is where it belongs.
export function waitTicks(p: PortDetail): number {
  return p.counters_delta?.[CONGESTION_COUNTER] ?? 0;
}

// Which counters moved, worst first, or **null** when no delta exists.
export function countersMoved(p: PortDetail): [CounterName, number][] | null {
  const c = p.counters_delta;
  if (!c) return null;
  return (Object.entries(c) as [CounterName, number][])
    .filter(([, v]) => v > 0)
    .sort((a, b) => b[1] - a[1]);
}


function linkLabel(
  nodes: Map<string, { label: string }>,
  link: { source: string; target: string; source_port: number; target_port: number },
): string {
  const name = (guid: string): string => nodes.get(guid)?.label ?? guid;
  return `${name(link.source)}:${link.source_port} ↔ ${name(link.target)}:${link.target_port}`;
}
