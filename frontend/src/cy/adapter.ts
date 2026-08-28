// Cytoscape setup

import cytoscape from "cytoscape";
import type { ElementDefinition } from "cytoscape";
import fcose from "cytoscape-fcose";
import type { GraphModel } from "../model/graph";

// Register the fcose layout once (guard against React StrictMode double-invoke)
let registered = false;
export function registerLayouts(): void {
  if (registered) return;
  cytoscape.use(fcose);
  registered = true;
}

/** Compound parent id for a chassis. One place, because the reconciler and the
 *  adapter must agree on it exactly. */
export const parentId = (systemImageGuid: string) => `sysimg_${systemImageGuid}`;

// GraphModel -> Cytoscape elements. A straight wrap: the model's lean elements
// already carry `id`/`source`/`target`, and detail stays out of the graph.
//
// `mode`, `change` and `churn` ride on element data so the stylesheet can
// select on them (`edge[mode="compare"][change="removed"]`). `mode` is what
// makes that safe: `change` is "unchanged" on every live element too, so a bare
// `[change="unchanged"]` rule would grey out the live graph as well.
export function toElements(model: GraphModel): ElementDefinition[] {
  const nodes: ElementDefinition[] = [];
  const emittedParents = new Set<string>();
  const mode = model.mode;

  for (const n of model.nodes) {
    const el = n.el;
    const sysimg = el.system_image_guid ?? null;
    const group = sysimg !== null ? model.systemGroups[sysimg] : undefined;

    // Emit the compound parent once, the first time we see its system group
    if (group && sysimg !== null && !emittedParents.has(sysimg)) {
      nodes.push({
        group: "nodes",
        data: { id: parentId(sysimg), label: group.label, mode },
      });
      emittedParents.add(sysimg);
    }

    nodes.push({
      group: "nodes",
      data: {
        ...el,
        mode,
        change: n.change,
        churn: n.churn,
        ...(group && sysimg !== null ? { parent: parentId(sysimg) } : {}),
      },
    });
  }

  const edges: ElementDefinition[] = model.links.map((l) => ({
    group: "edges",
    data: { ...l.el, mode, change: l.change, churn: l.churn },
  }));

  return [...nodes, ...edges];
}
