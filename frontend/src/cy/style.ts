import type { StylesheetStyle } from "cytoscape";
import {
  CHANGE_COLOR, ERROR_RAMP, HEALTH_COLOR, LOAD_RAMP, THROUGHPUT_RAMP,
  UNTRUSTED_COLOR,
} from "./palette";
import { DEFAULTS, type Settings } from "../settings/defaults";

// Cytoscape stylesheet. Selectors read the lean element fields
// (`type`, `health`, `status`) straight off element `data`.

/** The 26x20 rectangle every switch-shaped node uses. */
const RECT_RATIO = 20 / 26;

/** `size` as a (width, height) pair for a rectangular node. */
const rect = (size: number) => ({ width: size, height: size * RECT_RATIO });

const fontFor = (size: number, base: number, baseSize: number): number =>
  Math.max(6, Math.round((size / baseSize) * base * 10) / 10);

/* One rule per (overlay, bin), generated rather than written out. */
function overlayRules(): StylesheetStyle[] {
  const ramps = [
    ["errors", ERROR_RAMP],
    ["congestion", LOAD_RAMP],
    ["utilisation", LOAD_RAMP],
    ["throughput", THROUGHPUT_RAMP],
  ] as const;

  const out: StylesheetStyle[] = [];
  for (const [overlay, ramp] of ramps) {
    ramp.forEach((color, bin) => {
      out.push({
        selector: `node[overlay="${overlay}"][bin=${bin}]`,
        style: { "background-color": color },
      });
      out.push({
        selector: `edge[overlay="${overlay}"][bin=${bin}]`,
        style: { "line-color": color, opacity: bin === 0 ? 0.5 : 0.95 },
      });
    });
  }
  return out;
}

export function buildStylesheet(settings: Settings = DEFAULTS): StylesheetStyle[] {
  const sz = settings.size;
  const labelMin = settings.labelMinPx;
  return [
  // ---- nodes -------------------------------------------------------------
  {
    selector: "node",
    style: {
      label: "data(label)",
      color: "#c9d1d9",
      "font-size": 7,
      "text-wrap": "ellipsis",
      "text-max-width": "80px",
      "text-valign": "bottom",
      "text-margin-y": 3,
      "background-color": HEALTH_COLOR.ok,
      "border-width": 1,
      "border-color": "#0d1117",
      width: sz.ca,
      height: sz.ca,
      // hide labels once they'd render smaller than this (px) — declutters the
      // overview; labels reappear as you zoom into a cluster. 0 always shows.
      "min-zoomed-font-size": labelMin,
      "z-index": 1,
    },
  },
  // shape by node kind. Switches are few and important, so their labels always show.
  { selector: 'node[type="switch"]', style: { shape: "round-rectangle", ...rect(sz.switch), "font-size": fontFor(sz.switch, 8, 26), "min-zoomed-font-size": 0 } },
  { selector: 'node[type="ca"]', style: { shape: "ellipse" } },
  { selector: 'node[type="router"]', style: { shape: "diamond", width: sz.router, height: sz.router } },
  // fill by health rollup
  { selector: 'node[health="degraded"]', style: { "background-color": HEALTH_COLOR.degraded } },
  { selector: 'node[health="down"]', style: { "background-color": HEALTH_COLOR.down } },
  { selector: 'node[health="unknown"]', style: { "background-color": HEALTH_COLOR.unknown } },
  // ---- subnet managers ---------------------------------------------------
  {
    // Any SM, whatever its state: always labelled, always on top. The two
    // roles below then set their own size over this.
    selector: "node[?sm_role]",
    style: { "min-zoomed-font-size": 0, "z-index": 3 },
  },
  {
    selector: 'node[sm_role="master"]',
    style: {
      "padding": "10px",
      "shape": "round-rectangle",
      ...rect(sz.smMaster),
      "border-width": 3,
      "background-image": "url(/icons/crown.svg)",
      "background-fit": "none",
      "background-image-opacity": 1,
      "background-width": "80%",
      "background-height": "80%",
      "background-position-x": "50%",
      "background-position-y": "50%",
    },
  },
  {
    selector: ":parent",
    style: {
      "background-color": "#64748b",
      "background-opacity": 0.15,
      "shape": "round-rectangle",       // or "ellipse" for a circle
      "label": "data(label)",
      "text-valign": "top",
      "text-margin-y": -1,
      "color": "#94a3b8",
      "font-size": 9,
    },
  },
  {
    selector: 'node[sm_role="standby"]',
    style: {
      "padding": "5px",
      ...rect(sz.smStandby),
      "border-width": 2,
      "background-image": "url(/icons/clock.svg)",
      "background-fit": "none",
      "background-image-opacity": 1,
      "background-width": "80%",
      "background-height": "80%",
      "background-position-x": "50%",
      "background-position-y": "50%",
    },
  },
  {
    selector: 'node[sm_role="discovering"]',
    style: {
      "padding": "5px",
      "border-width": 2,
      "border-style": "dashed",
      "background-image": "url(/icons/search.svg)",
      "background-fit": "none",
      "background-image-opacity": 1,
      "background-width": "80%",
      "background-height": "80%",
      "background-position-x": "50%",
      "background-position-y": "50%",
    },
  },

  // ---- edges -------------------------------------------------------------
  {
    selector: "edge",
    style: {
      width: sz.edge,
      "line-color": HEALTH_COLOR.ok,
      // bezier (not straight) so parallel links between the same switch pair fan
      // out into separate arcs instead of stacking pixel-on-pixel — otherwise two
      // links between the same nodes overlap.
      "curve-style": "bezier",
      "control-point-step-size": 14,
      opacity: 0.75,
      "z-index": 0,
    },
  },
  { selector: 'edge[health="degraded"]', style: { "line-color": HEALTH_COLOR.degraded, width: sz.edge * 1.7, opacity: 0.95, "z-index": 2 } },
  { selector: 'edge[health="down"]', style: { "line-color": HEALTH_COLOR.down, width: sz.edge * 1.7, opacity: 0.95, "line-style": "dashed", "z-index": 2 } },
  { selector: 'edge[health="unknown"]', style: { "line-color": HEALTH_COLOR.unknown, "line-style": "dotted" } },

  // ---- compare mode ------------------------------------------------------

  { selector: 'node[mode="compare"]', style: { "background-color": CHANGE_COLOR.unchanged } },
  // Chassis boxes are synthetic and carry no `change`, so only the rule above
  // reaches them. Put their wash back, or every compound turns element-grey.
  { selector: 'node[mode="compare"]:parent', style: { "background-color": "#64748b" } },
  { selector: 'edge[mode="compare"]', style: { "line-color": CHANGE_COLOR.unchanged, "line-style": "solid", opacity: 0.55 } },

  { selector: 'node[mode="compare"][change="added"]', style: { "background-color": CHANGE_COLOR.added } },
  { selector: 'edge[mode="compare"][change="added"]', style: { "line-color": CHANGE_COLOR.added, width: sz.edge * 1.7, opacity: 0.95, "z-index": 3 } },

  { selector: 'node[mode="compare"][change="modified"]', style: { "background-color": CHANGE_COLOR.modified } },
  { selector: 'edge[mode="compare"][change="modified"]', style: { "line-color": CHANGE_COLOR.modified, width: sz.edge * 1.7, opacity: 0.95, "z-index": 3 } },

  // Removed elements are ghosts: they exist at `since` and not at `until`, so
  // they are drawn to show the hole rather than to be read as present.
  {
    selector: 'node[mode="compare"][change="removed"]',
    style: {
      "background-color": CHANGE_COLOR.removed,
      "border-width": 2,
      "border-color": "#ffb3ae",
      "border-style": "dashed",
      opacity: 0.9,
    },
  },
  {
    selector: 'edge[mode="compare"][change="removed"]',
    style: { "line-color": CHANGE_COLOR.removed, "line-style": "dashed", width: sz.edge * 1.7, opacity: 0.8, "z-index": 3 },
  },

  { selector: 'edge[mode="compare"][churn > 0]', style: { width: sz.edge * 2, opacity: 0.9, "z-index": 4 } },
  { selector: 'edge[mode="compare"][churn >= 20]', style: { width: sz.edge * 3.3, "z-index": 5 } },
  { selector: 'node[mode="compare"][churn > 0]', style: { "border-width": 2, "border-color": "#e6edf3" } },

  // ---- counter overlays --------------------------------------------------
  // Colour only: edge width is already spoken for by churn in compare mode, and
  // doubling magnitude onto both buys no information.
  ...overlayRules(),

  { selector: "node[?untrusted]", style: { "background-color": UNTRUSTED_COLOR } },
  {
    selector: "edge[?untrusted]",
    style: { "line-color": UNTRUSTED_COLOR, "line-style": "dotted", opacity: 0.9 },
  },

  // ---- interaction states ------------------------------------------------
  {
    selector: ".faded",
    style: { opacity: 0.08, "text-opacity": 0, "z-index": 0 },
  },
  // Not the same as `.faded`: this means "not part of what changed", and has
  // to leave the fabric's shape legible -- changed links floating in blank
  // space do not tell you they are all on one spine switch.
  {
    selector: ".receded",
    style: { opacity: 0.22, "text-opacity": 0, "z-index": 0 },
  },
  {
    selector: ".hidden, .collapsed",
    style: { display: "none" },
  },
  // Selection is unified on native `:selected` - every selected element (whether
  // one or a whole group from box-select / "select all leaf nodes") gets the same
  // highlight, and the detail panel's card list mirrors exactly this set.
  // Split per element type: `width` means node size vs. edge thickness, so a
  // shared rule would squish selected nodes into a sliver.
  {
    selector: "node:selected",
    style: {
      "overlay-color": "#58a6ff",
      "overlay-opacity": 0.25,
      "overlay-padding": 6,
      "border-width": 3,
      "border-color": "#58a6ff",
      "min-zoomed-font-size": 0, // always show the label of a selected node
      "z-index": 20,
    },
  },
  {
    selector: "edge:selected",
    style: {
      "line-color": "#58a6ff",
      width: sz.edge * 2.3,
      opacity: 1,
      "z-index": 20,
    },
  },
  ];
}

/** The default-settings stylesheet, for anything that builds a core before the
 *  provider is reachable. GraphView re-applies the real one on mount. */
export const stylesheet: StylesheetStyle[] = buildStylesheet();
