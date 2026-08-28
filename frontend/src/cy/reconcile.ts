// Diff a GraphModel into a live Cytoscape core, in place.
//
// Rebuilding from scratch would throw away positions, selection and collapsed
// groups. Keeping the layout stable is correctness rather than polish: you
// cannot see that a link vanished if every other element moved at the same
// time.

import type { Core, ElementDefinition, NodeSingular } from "cytoscape";
import type { GraphModel } from "../model/graph";
import { toElements } from "./adapter";
import { releaseCollapsed } from "./leafOps";

export interface ReconcileResult {
  /** Ids of elements that were added - what placeNewcomers needs to lay out. */
  added: string[];
  /** Ids of elements that left, including cascaded edge and child removals. */
  removed: string[];
}

export function reconcile(cy: Core, model: GraphModel): ReconcileResult {
  const want = new Map<string, ElementDefinition>();
  for (const def of toElements(model)) want.set(String(def.data.id), def);

  const added: string[] = [];
  const removed: string[] = [];

  cy.batch(() => {
    // ---- 1. depart --------------------------------------------------------
    //
    // Compound parents are excluded: cy.remove() cascades to descendants, so
    // removing a parent here would take live nodes with it. They are swept in
    // step 4, once every survivor has been reparented off them.
    const gone = cy.elements().filter(
      (e) => !want.has(e.id()) && !(e.isNode() && (e as NodeSingular).isParent()),
    );

    // A collapsed switch on its way out would leave its leaves carrying
    // `collapsed` (display: none) with expandLeaves unreachable -- hardware in
    // the model that can never be seen again.
    gone.nodes('[type="switch"]').forEach((sw) => releaseCollapsed(cy, sw));

    gone.remove().forEach((e) => {
      removed.push(e.id());
    });

    // ---- 2. add -----------------------------------------------------------
    //
    // Newcomers go in before the patch step, so that a survivor moving into a
    // brand-new chassis has somewhere to move to: ele.move() silently no-ops
    // when the target parent does not exist yet.
    const survivors: ElementDefinition[] = [];
    const newNodes: ElementDefinition[] = [];
    const newEdges: ElementDefinition[] = [];

    for (const [id, def] of want) {
      if (cy.hasElementWithId(id)) {
        survivors.push(def);
      } else {
        (def.group === "edges" ? newEdges : newNodes).push(def);
        added.push(id);
      }
    }

    // toElements emits parents before their children, and a filtered
    // subsequence preserves that, so a single add() is safe.
    if (newNodes.length > 0) cy.add(newNodes);
    if (newEdges.length > 0) {
      // A link whose endpoint is missing would throw and take the whole
      // reconcile with it. The API cannot produce one; this refuses to turn an
      // impossible state into a broken graph.
      cy.add(newEdges.filter((e) =>
        cy.hasElementWithId(String(e.data.source)) &&
        cy.hasElementWithId(String(e.data.target))));
    }

    // ---- 3. patch survivors ----------------------------------------------
    for (const def of survivors) {
      const ele = cy.getElementById(String(def.data.id));

      // `parent` is held back from the data patch: data() would set the field
      // without rebuilding the compound refs behind it. move() does it
      // properly and keeps the same element object, so scratch, position and
      // selection survive being reparented.
      const { parent, ...rest } = def.data as Record<string, unknown>;
      ele.data(rest);

      if (ele.isNode()) {
        const node = ele as NodeSingular;
        const current = node.parent().map((p) => p.id())[0];
        if (current !== parent) node.move({ parent: (parent as string) ?? null });
      }
    }

    // ---- 4. sweep childless parents ---------------------------------------
    //
    // Step 1 removed every non-parent absentee, so anything still here and
    // unwanted is a leftover compound parent. The emptiness check is what stops
    // step 1's cascade biting: a stray empty box beats deleting live nodes.
    cy.nodes()
      .filter((n) => !want.has(n.id()) && n.children().empty())
      .remove()
      .forEach((n) => {
        removed.push(n.id());
      });
  });

  return { added, removed };
}
