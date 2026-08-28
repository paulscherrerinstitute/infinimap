// Switch-centric leaf operations driven by the right-click menu: select a
// switch with its leaves for group dragging, and collapse/expand those leaves
// into and out of the switch.

import type { Core, NodeCollection, NodeSingular, Position } from "cytoscape";
import { syncGroupBoxes } from "./groupBoxes";

export const IB_NS = "_ib";
const FOLLOW = "position.*";
const DURATION = 300;

interface Scratch {
  collapsedUnder?: string; // (leaf) id of the switch it collapsed into
  offset?: { dx: number; dy: number }; // (leaf) position relative to that switch
  collapsed?: boolean; // (switch) whether its leaves are hidden
  leafIds?: string[]; // (switch) the leaves it owns while collapsed
}

function sc(ele: NodeSingular): Scratch {
  return (ele.scratch(IB_NS) as Scratch | undefined) ?? {};
}

// Animate a node to a position and resolve when it lands
function slideTo(l: NodeSingular, x: number, y: number, easing: string): Promise<void> {
  return l
    .animation({ position: { x, y }, duration: DURATION, easing } as never)
    .play()
    .promise()
    .then(() => undefined);
}

// Non-switch neighbours available to act on (skips leaves already collapsed
// under some other switch - a dual-homed HCA belongs to whoever grabbed it).
export function leafNeighbors(sw: NodeSingular): NodeCollection {
  return sw
    .neighborhood("node")
    .filter((n) => n.data("type") !== "switch" && !sc(n as NodeSingular).collapsedUnder);
}

export function isCollapsed(cy: Core, switchId: string): boolean {
  return !!sc(cy.getElementById(switchId) as NodeSingular).collapsed;
}

// Where a node "really" is, ignoring the collapsed parking spot - the position
// saved layouts should record. For a collapsed leaf that's switch + offset.
export function logicalPosition(cy: Core, node: NodeSingular): Position {
  const s = sc(node);
  if (s.collapsedUnder && s.offset) {
    const sw = cy.getElementById(s.collapsedUnder);
    if (sw.nonempty()) {
      const p = sw.position();
      return { x: p.x + s.offset.dx, y: p.y + s.offset.dy };
    }
  }
  return node.position();
}

// Feature 1 - select the switch + its leaves natively so Cytoscape's built-in
// group drag moves them together. Native selection drives the halo (node:selected)
// and is cleared by a plain background/node tap (see GraphView).
export function selectLeafGroup(cy: Core, switchId: string): void {
  const sw = cy.getElementById(switchId) as NodeSingular;
  if (sw.empty()) return;
  const group = leafNeighbors(sw).union(sw);
  cy.elements().unselect();
  group.select();
}

// Feature 2 - collapse: animate leaves into the switch, then hide them and keep
// them parked on it so it drags them around while hidden.
export function collapseLeaves(cy: Core, switchId: string): void {
  const sw = cy.getElementById(switchId) as NodeSingular;
  if (sw.empty() || sc(sw).collapsed) return;

  const leaves = leafNeighbors(sw);
  if (leaves.empty()) return;

  const swPos = { ...sw.position() };
  leaves.forEach((l) => {
    const p = l.position();
    l.scratch(IB_NS, { collapsedUnder: switchId, offset: { dx: p.x - swPos.x, dy: p.y - swPos.y } });
  });

  const edges = leaves.connectedEdges();
  sw.scratch(IB_NS, { collapsed: true, leafIds: leaves.map((l) => l.id()) });

  // Keep the hidden leaves under the switch as it moves. The ids are re-read
  // from scratch on every fire rather than closed over: the reconciler can
  // remove a leaf out from under a collapsed switch, and a captured collection
  // would go on positioning an element that is no longer in the graph.
  sw.on(FOLLOW, () => {
    const p = sw.position();
    for (const id of sc(sw).leafIds ?? []) {
      const l = cy.getElementById(id);
      if (l.nonempty()) l.position({ x: p.x, y: p.y });
    }
  });

  const anims = leaves.map((l) => slideTo(l, swPos.x, swPos.y, "ease-in"));
  Promise.all(anims).then(() => {
    // Bail if an expand raced in during the animation (scratch already cleared),
    // otherwise we'd re-hide leaves that were just brought back.
    if (!sc(sw).collapsed) return;
    leaves.addClass("collapsed");
    edges.addClass("collapsed");
    // A chassis whose every member just went under the switch has nothing left
    // to frame. Inside the `then` so the box goes as the leaves land, not while
    // they are still animating out of it.
    syncGroupBoxes(cy);
  });
}

// Feature 2 - expand: unhide and animate leaves back out to switch + offset.
export function expandLeaves(cy: Core, switchId: string): void {
  const sw = cy.getElementById(switchId) as NodeSingular;
  const s = sc(sw);
  if (sw.empty() || !s.collapsed || !s.leafIds) return;

  sw.off(FOLLOW);
  const swPos = sw.position();
  const leaves = cy.nodes().filter((n) => s.leafIds!.includes(n.id()));

  leaves.removeClass("collapsed");
  leaves.connectedEdges().removeClass("collapsed");
  // Before the animation, not after: the leaves have to fan out into a box that
  // is already on screen.
  syncGroupBoxes(cy);
  leaves.forEach((l) => {
    const off = sc(l as NodeSingular).offset ?? { dx: 0, dy: 0 };
    l.position({ x: swPos.x, y: swPos.y }); // start converged, then fan out
    slideTo(l as NodeSingular, swPos.x + off.dx, swPos.y + off.dy, "ease-out").then(() =>
      l.removeScratch(IB_NS),
    );
  });
  sw.removeScratch(IB_NS);
}

export function collapseAllLeaves(cy: Core): void {
  if (!cy) return;
  cy.nodes('[type="switch"]').forEach((sw) => collapseLeaves(cy, sw.id()));
}

export function expandAllLeaves(cy: Core): void {
  if (!cy) return;
  cy.nodes('[type="switch"]').forEach((sw) => expandLeaves(cy, sw.id()));
}

// Instantly un-collapse one switch: leaves return to switch + offset, classes
// and scratch drop, the follow listener detaches. The un-animated counterpart
// of expandLeaves, for the two cases where an animation would be wrong - a
// switch about to be removed from the graph (whose leaves would otherwise stay
// display:none with no way back), and a saved layout about to place every node
// itself.
export function releaseCollapsed(cy: Core, sw: NodeSingular): void {
  const s = sc(sw);
  if (!s.collapsed) return;

  sw.off(FOLLOW);
  const p = sw.position();
  for (const id of s.leafIds ?? []) {
    const l = cy.getElementById(id) as NodeSingular;
    if (l.empty()) continue;
    const off = sc(l).offset ?? { dx: 0, dy: 0 };
    l.position({ x: p.x + off.dx, y: p.y + off.dy });
    l.removeClass("collapsed");
    l.connectedEdges().removeClass("collapsed");
    l.removeScratch(IB_NS);
  }
  sw.removeScratch(IB_NS);
  syncGroupBoxes(cy);
}

// Instantly drop all collapse state (no animation) - used before applying a
// saved layout, which then positions every node itself.
export function resetAllCollapsed(cy: Core): void {
  cy.nodes().forEach((n) => releaseCollapsed(cy, n));
  // A leaf whose switch has already left the graph has no one to release it,
  // so sweep whatever the loop above could not reach.
  cy.nodes().forEach((n) => {
    n.removeScratch(IB_NS);
  });
  cy.elements().removeClass("collapsed");
  // The sweep above un-hid every box too; re-derive rather than assume, since
  // a filter may still want some of them gone.
  syncGroupBoxes(cy);
}
