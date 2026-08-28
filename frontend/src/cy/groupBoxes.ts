// The compound chassis boxes (one per systemImageGuid) exist only to frame the
// members inside them. Cytoscape has no notion of a box that has emptied out:
// hide every child and it carries on drawing the frame around nothing, which is
// what left stray rectangles behind after collapsing leaves or hiding CAs.
//
// So the boxes are not set directly by anything. They are re-derived from their
// members by whatever just changed what is on screen - see the call sites in
// `filters.ts` and `leafOps.ts`.

import type { Core, NodeSingular } from "cytoscape";

const isHidden = (n: NodeSingular) => n.hasClass("hidden") || n.hasClass("collapsed");

/** Hide every chassis box whose members are all off screen; show the rest. */
export function syncGroupBoxes(cy: Core): void {
  cy.nodes(":parent").forEach((box) => {
    const members = box.children();
    const allHidden = members.every((m) => isHidden(m as NodeSingular));
    box.toggleClass("collapsed", members.nonempty() && allHidden);
  });
}
