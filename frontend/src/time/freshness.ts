// How far behind the answering sweep is, and whether the newest one failed.

import type { Resolved } from "../api/types";

export interface Freshness {
  /** ms the answering sweep sits behind the instant that was asked for. */
  lagMs: number;
  /** The newest sweep did not complete, so an older complete one answered. */
  incomplete: boolean;
}

export function freshness(r: Resolved): Freshness {
  return {
    lagMs: Date.parse(r.requested_at) - Date.parse(r.collected_at),
    incomplete: r.latest_sweep_complete === false,
  };
}
