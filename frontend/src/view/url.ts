// ViewState <-> query string.
//
// The same discipline as time/url.ts: "the graph coloured by symbol errors over
// the last six hours" has to be a link you can paste into a ticket.
//
// Pure and separate from React. Parameters this module does not own are
// preserved, so `?at=` survives a mask change and `?mask=` survives a scrub.

import type { CounterName } from "../api/types";
import {
  DEFAULT_VIEW, DEFAULT_WINDOW, MASKS, SELECTABLE, isWindowKey,
  type Mask, type ViewState, type WindowKey,
} from "./mask";

const MASK = "mask";
const WIN = "win";
const COUNTERS = "counters";
const ROLLUP = "rollup";

const OWNED = [MASK, WIN, COUNTERS, ROLLUP];

const isMask = (s: string): s is Mask => s in MASKS;

/** A mask name from a URL, current or retired, or null if it is neither. */
function readMask(raw: string | null): Mask | null {
  if (raw === null) return null;
  if (isMask(raw)) return raw;
  return null;
}

/**
 * Read view state out of a query string.
 *
 * Every field falls back independently, so a mangled `counters=` leaves the
 * mask and window intact. `mask` is NOT reconciled against the time mode here
 * -- `maskFor()` does that at the point of use, where the mode is known.
 */
export function parseView(search: string): ViewState {
  const q = new URLSearchParams(search);

  const mask = readMask(q.get(MASK)) ?? DEFAULT_VIEW.mask;

  const rawWin = q.get(WIN);
  const window: WindowKey =
    rawWin && isWindowKey(rawWin) ? rawWin : DEFAULT_WINDOW;

  // Unknown names are dropped rather than poisoning the set — the counter list
  // changes by IBTA revision, so an old link naming a retired counter should
  // still open.
  const rawCounters = q.get(COUNTERS);
  let counters = DEFAULT_VIEW.counters;
  if (rawCounters !== null) {
    const named = rawCounters
      .split(",")
      .map((s) => s.trim())
      .filter((s): s is CounterName => (SELECTABLE as readonly string[]).includes(s));
    // An explicitly empty selection is a real state - the user unticked
    // everything - and must not silently spring back to the default.
    counters = named;
  }

  const rollup = q.get(ROLLUP) === "sum" ? "sum" : DEFAULT_VIEW.rollup;

  return { mask, window, counters, rollup };
}

/**
 * The query string for `view`, preserving parameters this module does not own.
 *
 * Defaults are omitted rather than written out, so a plain live view stays a
 * bare URL and a link only carries what somebody actually chose. The exception
 * is an empty counter selection, which is a choice that has to survive.
 */
export function toSearch(view: ViewState, current: string): string {
  const q = new URLSearchParams(current);
  for (const key of OWNED) q.delete(key);

  if (view.mask !== DEFAULT_VIEW.mask) q.set(MASK, view.mask);
  if (view.window !== DEFAULT_WINDOW) q.set(WIN, view.window);
  if (view.rollup !== DEFAULT_VIEW.rollup) q.set(ROLLUP, view.rollup);

  // Only meaningful under a mask that reads it; writing it always would put a
  // counter list on a health link that has nothing to do with counters.
  if (MASKS[view.mask].overlay === "errors" && !sameSet(view.counters, DEFAULT_VIEW.counters)) {
    q.set(COUNTERS, view.counters.join(","));
  }

  const s = q.toString();
  return s ? `?${s}` : "";
}

function sameSet(a: readonly string[], b: readonly string[]): boolean {
  if (a.length !== b.length) return false;
  const set = new Set(b);
  return a.every((x) => set.has(x));
}
