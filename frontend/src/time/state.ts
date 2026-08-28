// What instant the app is looking at. Separate from TimeContext so the URL
// codec can import it without a cycle: `state` is the domain, `url` its wire
// encoding, `TimeContext` its React delivery.

import type { Instant } from "../api/types";

export type TimeState =
  | { mode: "live" }
  | { mode: "point"; at: string }
  /** `until: null` means "…to now". */
  | { mode: "compare"; since: string; until: string | null };

export const LIVE: TimeState = { mode: "live" };

/**
 * The instant a single-model view should read. In compare mode that is the
 * later endpoint: `before` is carried per element rather than refetched, so a
 * compare URL renders the "after" topology - incomplete, but not wrong.
 */
export function instantOf(time: TimeState): Instant {
  switch (time.mode) {
    case "live":
      return null;
    case "point":
      return time.at;
    case "compare":
      return time.until;
  }
}
