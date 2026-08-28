// TimeState <-> query string. Pure and separate from the context.

import { canonical } from "./instant";
import { LIVE, type TimeState } from "./state";

const AT = "at";
const SINCE = "since";
const UNTIL = "until";

/** The parameters this module owns. Anything else in the URL is left alone. */
const OWNED = [AT, SINCE, UNTIL];

/** Normalise to the exact spelling used as a cache key, or null if unusable.
 *  Microsecond-preserving -- see ./instant for why truncating breaks links. */
const instant = canonical;

/**
 * Read the time state out of a query string.
 *
 * No `mode` parameter: the mode is implied by which instants are present, so a
 * self-contradicting URL (`?mode=point&since=…`) is unrepresentable. Anything
 * unparseable drops that parameter, so the worst a mangled link does is live.
 */
export function parseTime(search: string): TimeState {
  const q = new URLSearchParams(search);

  const since = instant(q.get(SINCE));
  if (since) return { mode: "compare", since, until: instant(q.get(UNTIL)) };

  const at = instant(q.get(AT));
  if (at) return { mode: "point", at };

  return LIVE;
}

/** The query string for `time`. */
export function toSearch(time: TimeState, current: string): string {
  const q = new URLSearchParams(current);
  for (const key of OWNED) q.delete(key);

  if (time.mode === "point") {
    q.set(AT, time.at);
  } else if (time.mode === "compare") {
    q.set(SINCE, time.since);
    // Omitted rather than empty: "…to now" is the absence of an end, and it
    // has to keep meaning "now" when the link is opened tomorrow.
    if (time.until !== null) q.set(UNTIL, time.until);
  }

  const s = q.toString();
  return s ? `?${s}` : "";
}
