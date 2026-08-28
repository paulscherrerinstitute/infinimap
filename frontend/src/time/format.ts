// Rendering instants and durations.

/** UTC, seconds precision. The API guarantees every timestamp is UTC. */
export function stamp(iso: string): string {
  return iso.replace("T", " ").replace(/\.\d+/, "").replace(/Z?$/, " UTC");
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const MONTH = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

const pad = (n: number) => String(n).padStart(2, "0");

/**
 * An axis label, at the coarseness the tick step implies: a clock time when
 * ticks are closer together than a day, a date when they are further apart.
 *
 * UTC like everything else here.
 */
export function axisLabel(ms: number, stepMs: number): string {
  const d = new Date(ms);
  if (stepMs >= DAY) return `${MONTH[d.getUTCMonth()]} ${pad(d.getUTCDate())}`;
  // Midnight gets the date even on a fine axis, or a multi-day window reads as
  // one endlessly repeating day.
  if (d.getUTCHours() === 0 && d.getUTCMinutes() === 0) {
    return `${MONTH[d.getUTCMonth()]} ${pad(d.getUTCDate())}`;
  }
  return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`;
}

/** A duration at one significant unit — "42s", "7m", "3d". */
export function duration(ms: number): string {
  const abs = Math.abs(ms);
  if (abs < MINUTE) return `${Math.round(abs / 1000)}s`;
  if (abs < HOUR) return `${Math.round(abs / MINUTE)}m`;
  if (abs < DAY) return `${Math.round(abs / HOUR)}h`;
  return `${Math.round(abs / DAY)}d`;
}
