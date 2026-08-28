// View filters, as classes on the graph.
//
// Every mode-specific rule selects on `mode` too, so a flag set in one mode
// cannot leak into the other -- the toolbar only shows the current mode's
// checkbox, so a leaked flag would be invisible and unclearable.

import type { Core } from "cytoscape";
import { syncGroupBoxes } from "./groupBoxes";

export interface Filters {
  dimHealthy: boolean;
  hideCAs: boolean;
  /** Compare mode: recede everything that neither changed nor churned. */
  highlightChanges: boolean;
}

export function applyFilters(cy: Core, filters: Filters): void {
  cy.batch(() => {
    // `collapsed` is deliberately not cleared here: it is collapse state, not
    // filter state, and it outlives both a filter toggle and a reconcile.
    cy.elements().removeClass("faded hidden receded");

    if (filters.hideCAs) {
      cy.nodes('[type="ca"]').addClass("hidden");
      // edges to hidden CAs drop out automatically via connected nodes
      cy.edges().forEach((e) => {
        if (e.source().hasClass("hidden") || e.target().hasClass("hidden")) {
          e.addClass("hidden");
        }
      });
    }

    if (filters.dimHealthy) {
      cy.nodes('[mode="single"][health="ok"]').addClass("faded");
      cy.edges('[mode="single"][health="ok"]').addClass("faded");
    }

    if (filters.highlightChanges) {
      // `isInteresting()` inverted -- NOT a plain change filter: a link that
      // flapped repeatedly and came back is `unchanged` with high churn, and
      // hiding it would hide the element most worth looking at.
      cy.elements('[mode="compare"][change="unchanged"][churn = 0]').addClass("receded");
    }

    // Last: hiding every CA in a chassis empties its box.
    syncGroupBoxes(cy);
  });
}
