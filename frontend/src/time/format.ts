// Rendering instants and durations.
//
// THE ONE RULE HERE: a timezone may only ever produce a string a human reads.
//
// Every instant on the wire, in the URL, in a react-query key and in every
// comparison is UTC, canonically spelled. So the conversion happens here,
// at the last possible moment, and nowhere else.

const UTC = "UTC";

/**
 * `Intl.DateTimeFormat` is expensive to construct and the timeline builds one
 * label per axis tick, so they are cached per (zone, kind).
 */
const cache = new Map<string, Intl.DateTimeFormat>();

function formatter(zone: string, opts: Intl.DateTimeFormatOptions,
                   kind: string): Intl.DateTimeFormat {
  const key = `${kind}|${zone}`;
  const hit = cache.get(key);
  if (hit) return hit;

  let made: Intl.DateTimeFormat;
  try {
    made = new Intl.DateTimeFormat("en-GB", { ...opts, timeZone: zone });
  } catch {
    // A stored zone this browser does not know.
    made = new Intl.DateTimeFormat("en-GB", { ...opts, timeZone: UTC });
  }
  cache.set(key, made);
  return made;
}

// Parts by type, so nothing here ever depends on a locale's own ordering.
function parts(fmt: Intl.DateTimeFormat, ms: number): Record<string, string> {
  const out: Record<string, string> = {};
  for (const p of fmt.formatToParts(ms)) out[p.type] = p.value;
  return out;
}

const STAMP: Intl.DateTimeFormatOptions = {
  year: "numeric", month: "2-digit", day: "2-digit",
  hour: "2-digit", minute: "2-digit", second: "2-digit",
  hour12: false,
  timeZoneName: "short",
};

/**
 * A full timestamp: `2026-09-04 13:03:22 CEST`.
 */
export function stamp(iso: string | number | null | undefined,
                      zone: string = UTC): string {
  if (iso === null || iso === undefined) return "—";
  const ms = typeof iso === "number" ? iso : Date.parse(iso);
  if (Number.isNaN(ms)) return String(iso);

  const p = parts(formatter(zone, STAMP, "stamp"), ms);
  const name = p.timeZoneName ?? zone;
  return `${p.year}-${p.month}-${p.day} ${p.hour}:${p.minute}:${p.second} ${name}`;
}

const MINUTE = 60_000;
const HOUR = 60 * MINUTE;
const DAY = 24 * HOUR;

const AXIS_DATE: Intl.DateTimeFormatOptions = { month: "short", day: "2-digit" };
const AXIS_TIME: Intl.DateTimeFormatOptions = {
  hour: "2-digit", minute: "2-digit", hour12: false,
};

/**
 * An axis label, at the coarseness the tick step implies: a clock time when
 * ticks are closer together than a day, a date when they are further apart.
 */
export function axisLabel(ms: number, stepMs: number,
                          zone: string = UTC): string {
  const date = () => {
    const p = parts(formatter(zone, AXIS_DATE, "axisDate"), ms);
    return `${p.month} ${p.day}`;
  };
  if (stepMs >= DAY) return date();

  const p = parts(formatter(zone, AXIS_TIME, "axisTime"), ms);
  if (p.hour === "00" && p.minute === "00") return date();
  return `${p.hour}:${p.minute}`;
}

/**
 * The display zone's offset from UTC at `ms`, in milliseconds.
 */
export function zoneOffsetMs(ms: number, zone: string = UTC): number {
  if (zone === UTC) return 0;
  const fmt = formatter(zone, {
    year: "numeric", month: "2-digit", day: "2-digit",
    hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false,
  }, "offset");
  const p = parts(fmt, ms);
  // The same wall-clock reading, interpreted as if it were UTC. The difference
  // between that and the real instant IS the offset.
  const asUtc = Date.UTC(
    Number(p.year), Number(p.month) - 1, Number(p.day),
    Number(p.hour === "24" ? "0" : p.hour), Number(p.minute), Number(p.second),
  );
  return asUtc - Math.floor(ms / 1000) * 1000;
}

/** A duration at one significant unit - "42s", "7m", "3d". Zone-free: a length
 *  of time is the same length everywhere. */
export function duration(ms: number): string {
  const abs = Math.abs(ms);
  if (abs < MINUTE) return `${Math.round(abs / 1000)}s`;
  if (abs < HOUR) return `${Math.round(abs / MINUTE)}m`;
  if (abs < DAY) return `${Math.round(abs / HOUR)}h`;
  return `${Math.round(abs / DAY)}d`;
}
