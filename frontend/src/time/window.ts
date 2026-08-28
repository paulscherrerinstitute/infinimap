// The stretch of history the timeline is showing.
//
// A *view* concern, deliberately separate from TimeState, which is the
// artifact: which instants you are comparing is what you send someone, your
// scroll position is not.

import type { TimeState } from "./state";

export interface Win {
  from: number;
  to: number;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

/** Below this a window is all rounding error; above it, meaningless. */
const MIN_SPAN = 5 * MINUTE;
const MAX_SPAN = 730 * DAY;

export const span = (w: Win): number => w.to - w.from;

/** Never past `now`, and never absurdly narrow or wide. Applied after every
 *  zoom or pan rather than inside them. */
export function clampWin(w: Win, now: number): Win {
  const wanted = Math.min(Math.max(span(w), MIN_SPAN), MAX_SPAN);
  // Anchor on `to` when shifting, so clamping at the right edge does not also
  // drag the left edge somewhere unexpected.
  const to = Math.min(w.to, now);
  return { from: to - wanted, to };
}

/** The instants a mode actually cares about - what has to stay on screen. */
export function rangeOf(time: TimeState, now: number): { lo: number; hi: number } {
  switch (time.mode) {
    case "live":
      return { lo: now, hi: now };
    case "point": {
      const at = Date.parse(time.at);
      return { lo: at, hi: at };
    }
    case "compare": {
      const lo = Date.parse(time.since);
      const hi = time.until === null ? now : Date.parse(time.until);
      return { lo, hi };
    }
  }
}

export function defaultWindow(time: TimeState, now: number): Win {
  const { lo, hi } = rangeOf(time, now);
  if (time.mode === "compare") {
    // Pad either side, so both handles are reachable rather than pinned to the
    // edges of the strip.
    const pad = Math.max((hi - lo) * 0.25, 30 * MINUTE);
    return clampWin({ from: lo - pad, to: hi + pad }, now);
  }
  if (time.mode === "point") {
    return clampWin({ from: lo - 12 * HOUR, to: lo + 12 * HOUR }, now);
  }
  return clampWin({ from: now - DAY, to: now }, now);
}

/**
 * Keep the view unless the artifact would be off-screen. TimeState changes from
 * several places, and re-deriving every time yanks the view while never
 * re-deriving strands the handles outside it.
 */
export function ensureVisible(w: Win, time: TimeState, now: number): Win {
  const { lo, hi } = rangeOf(time, now);
  if (lo >= w.from && hi <= w.to) return w;
  return defaultWindow(time, now);
}

/**
 * The window after the head sweep moved. A window parked at the head follows
 * it; without this, live mode fails `ensureVisible` on every landed sweep and
 * throws the user's zoom away once per collector cadence.
 *
 * Forward only: sweeps accumulate, so a backwards step means the head is being
 * read from an instant that has not caught up yet.
 */
export function onHeadMove(w: Win, prev: number, now: number, time: TimeState): Win {
  const moved = now - prev;
  const slid = moved > 0 && w.to >= prev ? { from: w.from + moved, to: w.to + moved } : w;
  return ensureVisible(slid, time, now);
}

/** `factor` < 1 zooms in. `atFraction` is where the cursor sits across the
 *  strip, and stays over the same instant throughout. */
export function zoom(w: Win, factor: number, atFraction: number): Win {
  const anchor = w.from + span(w) * atFraction;
  const wanted = Math.min(Math.max(span(w) * factor, MIN_SPAN), MAX_SPAN);
  return { from: anchor - wanted * atFraction, to: anchor - wanted * atFraction + wanted };
}

/** Positive moves the view forward in time. Span is preserved exactly. */
export function pan(w: Win, byFraction: number): Win {
  const by = span(w) * byFraction;
  return { from: w.from + by, to: w.to + by };
}

export const toX = (t: number, w: Win, width: number): number =>
  ((t - w.from) / span(w)) * width;

export const atX = (px: number, w: Win, width: number): number =>
  w.from + (px / width) * span(w);

// ---- axis -----------------------------------------------------------------

/** Steps a human reads without doing arithmetic. */
const STEPS = [
  MINUTE, 5 * MINUTE, 15 * MINUTE, 30 * MINUTE,
  HOUR, 3 * HOUR, 6 * HOUR, 12 * HOUR,
  DAY, 7 * DAY, 30 * DAY, 90 * DAY, 365 * DAY,
];

const MIN_TICK_PX = 90;

export interface Tick {
  at: number;
  x: number;
}

/** Labelled positions along the axis, never closer together than a label is
 *  wide. Aligned to the step so they land on round times. */
export function ticks(w: Win, width: number): { step: number; ticks: Tick[] } {
  const wanted = (MIN_TICK_PX / Math.max(width, 1)) * span(w);
  const step = STEPS.find((s) => s >= wanted) ?? STEPS[STEPS.length - 1];

  const out: Tick[] = [];
  for (let t = Math.ceil(w.from / step) * step; t <= w.to; t += step) {
    out.push({ at: t, x: toX(t, w, width) });
  }
  return { step, ticks: out };
}

// ---- density --------------------------------------------------------------

export interface Column {
  x: number;
  count: number;
  changed: boolean;
}

/** Pixel width of one density column. */
export const COLUMN_PX = 2;

/**
 * Sweeps bucketed by pixel column, which is what keeps the strip cheap: at a
 * 5-minute cadence a week holds more sweeps than the strip has pixels, so one
 * element per sweep would be mostly overdraw. The counts double as the density
 * strip -- how many sweeps a column holds, and whether any of them changed.
 */
export function columns(
  sweeps: { at: number; changed: boolean }[],
  w: Win,
  width: number,
): Column[] {
  const byX = new Map<number, Column>();
  for (const s of sweeps) {
    if (s.at < w.from || s.at > w.to) continue;
    const x = Math.floor(toX(s.at, w, width) / COLUMN_PX) * COLUMN_PX;
    const col = byX.get(x);
    if (col) {
      col.count += 1;
      col.changed ||= s.changed;
    } else {
      byX.set(x, { x, count: 1, changed: s.changed });
    }
  }
  return [...byX.values()].sort((a, b) => a.x - b.x);
}
