// Navigating by change rather than by time.

import type { SnapshotRow } from "../api/types";
import { canonical } from "./instant";

export interface Sweep {
  /** Epoch ms, for arithmetic and drawing. */
  at: number;
  /**
   * The timestamp to the microsecond, and the only value that may be sent back:
   * a value rebuilt from `at` loses the microseconds and lands *before* the
   * sweep it came from, which resolves to the previous sweep. See ./instant.
   *
   * Canonicalised rather than verbatim, because the server omits the fraction
   * entirely when a sweep lands on a whole second.
   */
  iso: string;
  changed: boolean;
  complete: boolean;
}

/** Ascending, which is the order every function here assumes. `/snapshots`
 *  returns newest-first because that is what a list wants; an axis wants the
 *  opposite. */
export function toSweeps(rows: SnapshotRow[]): Sweep[] {
  return rows
    .map((r) => ({
      at: Date.parse(r.collected_at),
      iso: canonical(r.collected_at),
      changed: r.changed,
      complete: r.complete,
    }))
    .filter((s): s is Sweep => s.iso !== null && Number.isFinite(s.at))
    .sort((a, b) => a.at - b.at);
}

/**
 * The sweeps a request can actually land on.
 */
export const targetable = (sweeps: Sweep[]): Sweep[] => sweeps.filter((s) => s.complete);

/** The sweep an instant should snap to. Nearest rather than at-or-before:
 *  while dragging, the closest sweep is the one the user is aiming at. */
export function nearest(sweeps: Sweep[], at: number): Sweep | null {
  let best: Sweep | null = null;
  let bestGap = Infinity;
  for (const s of sweeps) {
    const gap = Math.abs(s.at - at);
    if (gap < bestGap) {
      best = s;
      bestGap = gap;
    }
  }
  return best;
}

/**
 * The next sweep in a direction, for stepping a handle by keyboard.
 */
export function stepTarget(sweeps: Sweep[], from: number, dir: 1 | -1): Sweep | null {
  if (dir === 1) return sweeps.find((s) => s.at > from) ?? null;
  for (let i = sweeps.length - 1; i >= 0; i--) {
    if (sweeps[i].at < from) return sweeps[i];
  }
  return null;
}

export function nextChange(sweeps: Sweep[], after: number): Sweep | null {
  return sweeps.find((s) => s.changed && s.at > after) ?? null;
}

export function prevChange(sweeps: Sweep[], before: number): Sweep | null {
  for (let i = sweeps.length - 1; i >= 0; i--) {
    const s = sweeps[i];
    if (s.changed && s.at < before) return s;
  }
  return null;
}

/**
 * The window that shows one change on its own: the changed sweep, against the
 * one before it.
 *
 * Null when the changed sweep is the earliest known, since there is then
 * nothing to compare it against.
 */
export function pairFor(
  sweeps: Sweep[],
  changed: Sweep,
): { since: string; until: string } | null {
  const i = sweeps.findIndex((s) => s.at === changed.at);
  if (i <= 0) return null;
  return { since: sweeps[i - 1].iso, until: changed.iso };
}
