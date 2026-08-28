// What the graph is coloured by.
//
// The node fill can only ever say one thing, so a mask names which thing. A
// mask IS a data key: every legend row calls `addMaskToSelection(key, value)`
// with the element data key it colours by -- `("health", "degraded")`,
// `("change", "added")` -- so a mask names its key, its legend enumerates that
// key's values, and click-a-swatch-to-select needs no per-mask code. Counters
// and traffic add one more key, `bin`.
//
// A counters mask REPLACES health rather than demoting it to a second channel;
// the border is not available for that, since `sm_role` already owns border
// width and style. Two views, two purposes, read one at a time -- the detail
// cards are where both facts appear at once for a selected element.

import type { CounterName } from "../api/types";
import type { Overlay } from "../cy/overlay";
import type { Mode } from "../time/modes";
import { COUNTER_GROUPS, DEFAULT_SELECTION, type Rollup } from "../model/counters";

export type Mask = "health" | "change" | "errors" | "congestion" | "traffic";

/**
 * A value a legend row can select on -- whatever the mask's data key holds.
 * `addMaskToSelection` compares with `===`, so the union has to be honest or
 * clicking a swatch silently selects nothing.
 */
export type MaskValue = string | number | boolean;

export interface MaskSpec {
  label: string;
  /** The element data key the stylesheet reads, and the legend selects on. */
  key: "health" | "change" | "bin";
  /** The overlay to compute, or null when the key is already on the element. */
  overlay: Overlay | null;
  /** Which counters this mask sums, or null when it does not use them. */
  counters: readonly CounterName[] | null;
}

export const MASKS: Record<Mask, MaskSpec> = {
  health: { label: "Health", key: "health", overlay: null, counters: null },
  change: { label: "Changes", key: "change", overlay: null, counters: null },
  errors: {
    label: "Errors", key: "bin", overlay: "errors",
    // The default is the cable group; the panel can widen it. `xmit_wait` is
    // deliberately not reachable from here — it is the congestion mask.
    counters: DEFAULT_SELECTION,
  },
  congestion: {
    label: "Congestion", key: "bin", overlay: "congestion",
    counters: ["xmit_wait"],
  },
  traffic: { label: "Traffic", key: "bin", overlay: "traffic", counters: null },
};

/**
 * The masks a given time mode can offer.
 *
 * Change is not a free-standing peer: it means nothing outside compare, and
 * inside compare it is the only thing worth colouring by. Deriving the list
 * from the mode keeps the control honest.
 */
export function masksFor(mode: Mode): Mask[] {
  return mode === "compare"
    ? ["change"]
    : ["health", "errors", "congestion", "traffic"];
}

export function defaultMask(mode: Mode): Mask {
  return mode === "compare" ? "change" : "health";
}

/** Coerce a mask to one the current mode actually offers. */
export function maskFor(mode: Mode, wanted: Mask): Mask {
  return masksFor(mode).includes(wanted) ? wanted : defaultMask(mode);
}


// ---- the window -----------------------------------------------------------

/**
 * Lookback presets, in seconds. A trailing lookback off whatever instant the
 * app is showing, not a second pair of drag handles. 24h is the raw path's
 * ceiling -- the endpoint 422s past it.
 */
export const WINDOWS = {
  "5m": 300,
  "15m": 900,
  "1h": 3_600,
  "6h": 21_600,
  "24h": 86_400,
} as const;

export type WindowKey = keyof typeof WINDOWS;
export const DEFAULT_WINDOW: WindowKey = "1h";
export const isWindowKey = (s: string): s is WindowKey => s in WINDOWS;

// ---- the whole of it ------------------------------------------------------

export interface ViewState {
  mask: Mask;
  window: WindowKey;
  /** Counters the user ticked. Only meaningful under the errors mask. */
  counters: readonly CounterName[];
  rollup: Rollup;
}

export const DEFAULT_VIEW: ViewState = {
  mask: "health",
  window: DEFAULT_WINDOW,
  counters: DEFAULT_SELECTION,
  rollup: "max",
};

/** Every counter a checkbox can reach, in the order the panel lists them. */
export const SELECTABLE: readonly CounterName[] = [
  ...COUNTER_GROUPS.cable,
  ...COUNTER_GROUPS.congestion,
  ...COUNTER_GROUPS.config,
];

/**
 * The counters a mask actually sums. Only the errors mask honours the checkbox
 * set: congestion is `xmit_wait` alone by definition, and letting checkboxes
 * leak into it would put the counter that swamps every other back in the sum.
 */
export function countersFor(view: ViewState): readonly CounterName[] {
  const spec = MASKS[view.mask];
  if (spec.overlay === "congestion") return spec.counters ?? [];
  if (spec.overlay === "errors") return view.counters;
  return [];
}

/** Seconds of lookback, for building the request window. */
export const lookbackOf = (view: ViewState): number => WINDOWS[view.window];
