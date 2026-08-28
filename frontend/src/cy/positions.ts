// Read positions off the live graph and replay saved ones back onto it

import type { Core } from "cytoscape";
import type { Position } from "../layouts/store";
import { fcoseLayout } from "./layout";
import { logicalPosition, resetAllCollapsed } from "./leafOps";

/** A node held exactly where it is while fcose solves for everything else. */
type Pin = { nodeId: string; position: Position };

// Snapshot node positions as a guid -> {x, y} map, using each node's *logical*
// position so a layout saved while switches are collapsed records where the
// leaves belong rather than their parked spot on the switch.
//
// Diff-only elements are captured like anything else. Replay is keyed by the
// nodes present now, so those entries are dead keys in live mode and pin the
// ghosts in compare rather than letting fcose re-solve them every visit.
export function capturePositions(cy: Core): Record<string, Position> {
  const out: Record<string, Position> = {};
  cy.nodes().forEach((n) => {
    if (n.isParent()) return;
    const p = logicalPosition(cy, n);
    out[n.id()] = { x: Math.round(p.x), y: Math.round(p.y) };
  });
  return out;
}

// Shared by both callers below: hold `fixed` in place and let fcose solve only
// for the rest, from their current positions rather than reshuffling.
function layoutAround(cy: Core, fixed: Pin[], onDone?: () => void): void {
  const layout = cy.layout({
    ...fcoseLayout,
    randomize: fixed.length === 0,
    fixedNodeConstraint: fixed,
    animate: false,
  } as unknown as typeof fcoseLayout);
  if (onDone) layout.one("layoutstop", onDone);
  layout.run();
}

// Replay a saved layout. When every current node has a saved position we place
// them directly (no physics). If the snapshot has since gained nodes the saved
// layout never saw, we pin the known nodes exactly where they were saved and
// run fcose only on the newcomers , so each new node settles next to its real
// neighbours instead of being scattered.
export function applyPositions(cy: Core, positions: Record<string, Position>): void {
  // A layout defines every node's spot outright, so drop any collapse state
  // first rather than fighting the follow-listener or leaving leaves hidden.
  resetAllCollapsed(cy);
  const fit = () => cy.fit(undefined, 30);

  const placeable = cy.nodes().filter((n) => !n.isParent());

  // Place every known node at its saved spot up front
  const fixed: Pin[] = [];
  cy.batch(() => {
    placeable.forEach((n) => {
      const p = positions[n.id()];
      if (p) {
        n.position(p);
        fixed.push({ nodeId: n.id(), position: p });
      }
    });
  });

  // No newcomers -> the saved positions are the whole layout, we're done
  if (fixed.length === placeable.length) {
    fit();
    return;
  }

  layoutAround(cy, fixed, fit);
}

// Settle nodes the reconciler has just added, without disturbing anything that
// was already on screen: every other node is pinned where it currently sits, so
// only the newcomers move.
export function placeNewcomers(
  cy: Core,
  ids: string[],
  opts: { fit?: boolean; saved?: Record<string, Position> } = {},
): void {
  const fit = () => cy.fit(undefined, 30);

  // The overwhelmingly common case: a sweep where nothing appeared. Costs no
  // physics at all.
  if (ids.length === 0) {
    if (opts.fit) fit();
    return;
  }

  const saved = opts.saved ?? {};
  const isNew = new Set(ids);
  const fixed: Pin[] = [];
  let unplaced = 0;

  cy.batch(() => {
    cy.nodes().forEach((n) => {
      // Compound parents must stay out of the pin list
      if (n.isParent()) return;

      const id = n.id();
      if (!isNew.has(id)) {
        fixed.push({ nodeId: id, position: { ...n.position() } });
        return;
      }

      const p = saved[id];
      if (p) {
        n.position(p);
        fixed.push({ nodeId: id, position: p });
      } else {
        unplaced += 1;
      }
    });
  });

  // Every newcomer had a saved spot, so there is nothing left to solve for.
  if (unplaced === 0) {
    if (opts.fit) fit();
    return;
  }

  layoutAround(cy, fixed, opts.fit ? fit : undefined);
}
