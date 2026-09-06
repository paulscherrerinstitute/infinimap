import { useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import type { Core } from "cytoscape";
import type { SelectedItem } from "../api/types";
import type { GraphModel } from "../model/graph";
import { registerLayouts } from "../cy/adapter";
import { buildStylesheet } from "../cy/style";
import { setWheelSensitivity } from "../cy/wheel";
import { useSettings } from "../settings/SettingsContext";
import { applyFilters, type Filters } from "../cy/filters";
import { applyPositions, placeNewcomers } from "../cy/positions";
import { reconcile } from "../cy/reconcile";
import { fcoseLayout, relayoutSelected } from "../cy/layout";
import {
  collapseAllLeaves, collapseLeaves, expandAllLeaves, expandLeaves,
  isCollapsed, leafNeighbors, selectLeafGroup,
} from "../cy/leafOps";
import { ContextMenu, type MenuItem } from "./ContextMenu";
import type { Position } from "../layouts/store";

registerLayouts();

// Re-exported so callers keep importing the view's filter type from the view.
export type { Filters };

interface Props {
  model: GraphModel;
  filters: Filters;
  setSelection: React.Dispatch<React.SetStateAction<SelectedItem[]>>;
  cyRef: React.MutableRefObject<Core | null>;
  // Positions from the active saved layout. Read on the first draw to arrange
  // the whole graph, and on every update afterwards to place elements the
  // layout already knows about rather than re-solving them with physics.
  savedPositions?: Record<string, Position>;
  /* Called after every pass that puts elements into the core. */
  onReconciled?: () => void;
}

// Cursor-anchored menu opened by right-click. A `nodeId` means it was opened on
// that switch; without one it was opened on empty canvas and acts on the graph.
type Menu = { x: number; y: number; nodeId?: string } | null;

export function GraphView({
  model, filters, setSelection, cyRef, savedPositions, onReconciled,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [menu, setMenu] = useState<Menu>(null);
  const { settings } = useSettings();

  // The mount effect must build with the CURRENT settings but must not re-run
  // when they change -- a rebuilt core loses positions, selection and collapse
  // state. The two effects below apply changes to the live core instead.
  const settingsRef = useRef(settings);
  settingsRef.current = settings;

  // The reconcile effect re-applies filters to elements it has just added, but
  // must not re-run when a filter toggles: that is a class change, not a graph
  // change, and it has its own effect below.
  const filtersRef = useRef(filters);
  filtersRef.current = filters;

  const onReconciledRef = useRef(onReconciled);
  onReconciledRef.current = onReconciled;

  // True until a model has been drawn into this core. Reset inside the mount
  // effect rather than at declaration, because a ref survives StrictMode's
  // mount -> unmount -> remount and the second mount gets a fresh, empty core
  // that very much does still need laying out.
  const firstRun = useRef(true);

  // Mount: create the core and wire it up, once.
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    firstRun.current = true;

    const cy = cytoscape({
      container,
      elements: [],
      style: buildStylesheet(settingsRef.current),
      layout: { name: "preset" }, // nothing to arrange yet
      wheelSensitivity: settingsRef.current.scrollWeight,
      minZoom: 0.05,
      maxZoom: 4,
      // "single": a plain tap replaces the selection; ctrl/shift/cmd-tap and
      // modifier box-drag accumulate it (for group drag). Cytoscape's own
      // mouseup handler implements both - don't also select from a tap
      // handler, or its additive branch will toggle the element back off.
      selectionType: "single",
      boxSelectionEnabled: true,
    });
    cyRef.current = cy;

    // All selection flows through native Cytoscape selection (tap to replace,
    // modifier-tap and modifier box-drag to accumulate, background tap to
    // clear). Mirror it into an ordered list, since Cytoscape doesn't preserve
    // selection order and appending on select does. Functional updates keep
    // this free of stale closures and naturally dedupe.
    cy.on("select", "node, edge", (e) => {
      const kind = e.target.isNode() ? "node" : "link";
      const id = e.target.id();
      setSelection((prev) => (prev.some((s) => s.id === id) ? prev : [...prev, { kind, id }]));
    });
    cy.on("unselect", "node, edge", (e) => {
      const id = e.target.id();
      setSelection((prev) => prev.filter((s) => s.id !== id));
    });
    // Removal does *not* emit unselect: cytoscape emits only `remove` and the
    // element keeps its selected flag, so without this a reconcile leaves cards
    // open on elements no longer in the fabric. The internal remove inside
    // ele.move() suppresses the event, so reparenting is safe.
    cy.on("remove", "node, edge", (e) => {
      const id = e.target.id();
      setSelection((prev) => prev.filter((s) => s.id !== id));
    });

    // Right-click -> context menu; suppress the browser's native one.
    const suppressNative = (e: Event) => e.preventDefault();
    container.addEventListener("contextmenu", suppressNative);
    cy.on("cxttap", (e) => {
      const { x, y } = e.renderedPosition ?? { x: 0, y: 0 };
      // A switch gets its leaf menu. Empty canvas gets the graph-wide one, and
      // so does any element that is part of the current selection, so that
      // right-clicking the selection reaches the actions that operate on it.
      // Anything else - an unselected edge or leaf CA - has no menu.
      if (e.target !== cy && e.target.isNode() && e.target.data("type") === "switch")
        setMenu({ x, y, nodeId: e.target.id() });
      else if (e.target === cy || e.target.selected()) setMenu({ x, y });
      else setMenu(null);
    });
    // Dismiss on anything that would detach the menu from where it was opened.
    cy.on("tap pan zoom", () => setMenu(null));

    // Cursor. Three states, all set from here because the hover one can only
    // come from Cytoscape: nodes and links are painted on a canvas, so there is
    // no DOM element for CSS to match. A plain drag pans, so bare background
    // reads as grabbable; holding a modifier swaps panning for box-select.
    let overElement = false;
    let modifier = false;
    const applyCursor = () => {
      container.style.cursor = overElement ? "pointer" : modifier ? "default" : "grab";
    };
    const syncModifier = (e: KeyboardEvent) => {
      modifier = e.ctrlKey || e.shiftKey || e.metaKey;
      applyCursor();
    };
    // Losing focus mid-chord never delivers the keyup, which would otherwise
    // leave the cursor stuck in whichever state the key was holding.
    const clearModifier = () => {
      modifier = false;
      applyCursor();
    };
    cy.on("mouseover", "node, edge", () => {
      overElement = true;
      applyCursor();
    });
    cy.on("mouseout", "node, edge", () => {
      overElement = false;
      applyCursor();
    });
    window.addEventListener("keydown", syncModifier);
    window.addEventListener("keyup", syncModifier);
    window.addEventListener("blur", clearModifier);
    applyCursor();

    return () => {
      window.removeEventListener("keydown", syncModifier);
      window.removeEventListener("keyup", syncModifier);
      window.removeEventListener("blur", clearModifier);
      container.removeEventListener("contextmenu", suppressNative);
      cy.destroy();
      cyRef.current = null;
      // Don't let the projected list outlive the cy it mirrors.
      setSelection([]);
    };
  }, [cyRef, setSelection]);

  // Diff the model into the live core. Survivors keep their positions,
  // selection and collapse state; only what actually changed is touched.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;

    const { added } = reconcile(cy, model);

    if (firstRun.current && savedPositions && Object.keys(savedPositions).length > 0) {
      // A saved layout defines every node's spot outright, so let it place the
      // whole graph rather than treating the first load as newcomers.
      applyPositions(cy, savedPositions);
    } else {
      // Only newcomers move, and any the layout already knows about go straight
      // back where they were saved. The viewport is left alone except on the
      // very first draw, where there is no user framing to preserve yet - a
      // mode switch keeps the camera exactly where it was, since the elements
      // it brings in settle among the neighbours already on screen.
      placeNewcomers(cy, added, { fit: firstRun.current, saved: savedPositions });
    }

    // Newcomers arrive with no classes at all, so the current filters have to
    // be re-applied over them.
    applyFilters(cy, filtersRef.current);
    firstRun.current = false;

    // Last, and after the elements are in: the overlay pass this triggers reads
    // the core, so it has to see the finished graph.
    onReconciledRef.current?.();
  }, [model, cyRef, savedPositions]);

  // Element sizes changed. `cy.style()` recomputes appearance and touches
  // nothing else -- elements, positions, selection and collapse all survive,
  // which is why this is a style swap rather than a rebuild.
  useEffect(() => {
    cyRef.current?.style(buildStylesheet(settingsRef.current));
  }, [settings.size, settings.labelMinPx, cyRef]);

  // Zoom sensitivity changed. See cy/wheel.ts for why this is a private write
  // rather than a documented setter.
  useEffect(() => {
    setWheelSensitivity(cyRef.current, settings.scrollWeight);
  }, [settings.scrollWeight, cyRef]);

  // Selection highlight is supplied entirely by the native `:selected` style —
  // no React-driven class needed.

  // Apply view filters (dim healthy / hide leaf CAs)
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    applyFilters(cy, filters);
  }, [filters, cyRef]);

  // Read straight off the core rather than from React state: the menu is built
  // in the render the right-click itself triggers, so the core is current.
  const cy = cyRef.current;
  const menuItems: MenuItem[] =
    !menu || !cy
      ? []
      : [...(menu.nodeId ? switchMenu(cy, menu.nodeId) : canvasMenu(cy)), ...selectionMenu(cy)];

  return (
    <>
      <div ref={containerRef} className="graph-canvas" />
      {menu && <ContextMenu x={menu.x} y={menu.y} items={menuItems} onClose={() => setMenu(null)} />}
    </>
  );
}

// Right-click on a switch: act on the leaves hanging off it.
function switchMenu(cy: Core, nodeId: string): MenuItem[] {
  // No selectable/hideable leaves for e.g. a spine switch (only switch peers)
  // or one whose leaves are already collapsed.
  const noLeaves = leafNeighbors(cy.getElementById(nodeId)).empty();
  return [
    {
      label: "Select all leaf nodes",
      disabled: noLeaves,
      onClick: () => selectLeafGroup(cy, nodeId),
    },
    isCollapsed(cy, nodeId)
      ? { label: "Show leaf nodes", onClick: () => expandLeaves(cy, nodeId) }
      : { label: "Hide leaf nodes", disabled: noLeaves, onClick: () => collapseLeaves(cy, nodeId) },
  ];
}

// Right-click on empty canvas: act on the whole graph. The toolbar answers
// "what am I looking at"; these answer "move it".
function canvasMenu(cy: Core): MenuItem[] {
  return [
    { label: "Fit to viewport", onClick: () => cy.fit(undefined, 30) },
    { label: "Re-run layout", onClick: () => cy.layout(fcoseLayout).run() },
    { label: "Hide all leaf nodes", onClick: () => collapseAllLeaves(cy) },
    { label: "Show all leaf nodes", onClick: () => expandAllLeaves(cy) },
  ];
}

// Appended to whichever menu opened, so the selection can be acted on from a
// right-click on it or on the canvas around it.
function selectionMenu(cy: Core): MenuItem[] {
  const n = cy.nodes(":selected").length;
  if (n < 2) return [];
  return [{ label: `Re-layout selected (${n})`, onClick: () => relayoutSelected(cy) }];
}
