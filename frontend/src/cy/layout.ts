import type { Core, LayoutOptions } from "cytoscape";

// fcose tuned for InfiniBand fat-tree topology: switches cluster, the many
// leaf CAs fan out around them. `packComponents` keeps disconnected islands
// from flying off. See https://github.com/iVis-at-Bilkent/cytoscape.js-fcose.
export const fcoseLayout: LayoutOptions = {
  name: "fcose",
  quality: "default",
  animate: false,
  randomize: true,
  packComponents: true,
  nodeRepulsion: 14000, // push leaves apart harder
  idealEdgeLength: 90, // longer hub->leaf spokes = less crowding around switches
  edgeElasticity: 0.4,
  gravity: 0.15, // weaker pull to center lets clusters breathe
  gravityRange: 3.0,
  numIter: 3000,
  nodeSeparation: 120,
} as unknown as LayoutOptions;

// Re-run the force layout over the selected nodes alone.
export function relayoutSelected(cy: Core): void {
  const nodes = cy.nodes(":selected");
  if (nodes.length < 2) return; // nothing to arrange
  nodes
    .union(nodes.edgesWith(nodes))
    .layout({ ...fcoseLayout, fit: false, boundingBox: nodes.boundingBox() } as unknown as LayoutOptions)
    .run();
}
