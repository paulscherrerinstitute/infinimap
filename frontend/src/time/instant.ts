// One spelling per instant, without throwing away what the server said.
//
// An instant keys a react-query cache entry and rides in the URL, so it needs
// exactly one spelling. Sweep timestamps carry microseconds and JavaScript
// dates hold milliseconds, so normalising through `new Date(ms).toISOString()`
// truncates to an instant *before* the sweep -- which then resolves to the
// previous sweep, or 404s.
//
// Hence the canonical form: UTC, `Z`, six fractional digits.

/** Postgres `timestamptz` resolution, which is what is on the other end. */
const DIGITS = 6;

/** `YYYY-MM-DDTHH:MM:SS.` - everything before the fraction. */
const HEAD = 20;

/**
 * The canonical spelling of an instant, or null if it is not one. Accepts
 * anything `Date.parse` does and answers in UTC. Sub-millisecond digits come
 * from the source string, not the parsed number, which cannot hold them.
 */
export function canonical(raw: string | null | undefined): string | null {
  if (!raw) return null;
  const ms = Date.parse(raw);
  if (Number.isNaN(ms)) return null;

  // V8 truncates a long fraction rather than rounding, so `base`'s ms are
  // always the first three digits of `frac` and splicing cannot cross a
  // second boundary.
  const base = new Date(ms).toISOString();
  const frac = /\.(\d+)/.exec(raw)?.[1] ?? "";
  return `${base.slice(0, HEAD)}${frac.padEnd(DIGITS, "0").slice(0, DIGITS)}Z`;
}

/**
 * An epoch-ms instant, canonically spelled. The last three digits are zero by
 * construction, so this is wrong for an instant naming a sweep: carry those as
 * the server's own string (`Sweep.iso`).
 */
export const instantAt = (ms: number): string =>
  new Date(ms).toISOString().replace("Z", "000Z");
