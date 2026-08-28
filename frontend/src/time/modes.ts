// Picking a mode selects a mode but not its parameters, so each needs a
// default. Both anchor on the sweep currently on screen, not wall-clock now.

import type { Resolved } from "../api/types";
import { canonical, instantAt } from "./instant";
import { LIVE, type TimeState } from "./state";

export type Mode = TimeState["mode"];

export const MODE_LABEL: Record<Mode, string> = {
  live: "Live",
  point: "Point",
  compare: "Diff",
};

const DIFF_WINDOW_MS = 24 * 60 * 60 * 1000;

export function defaultFor(mode: Mode, resolved: Resolved): TimeState {
  switch (mode) {
    case "live":
      return LIVE;
    case "point":
      // "Freeze on what I am looking at" has to name that sweep exactly:
      // rebuilding the stamp from a number drops the microseconds and lands
      // before it, freezing on the previous sweep instead.
      return { mode: "point", at: canonical(resolved.collected_at) ?? resolved.collected_at };
    case "compare":
      // An offset instant names no sweep in particular, so milliseconds are all
      // it ever had. Canonical anyway: one spelling per instant, everywhere.
      return {
        mode: "compare",
        since: instantAt(Date.parse(resolved.collected_at) - DIFF_WINDOW_MS),
        until: null, // …to now
      };
  }
}
