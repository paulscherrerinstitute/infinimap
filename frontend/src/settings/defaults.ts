// Per-browser preferences

export interface NodeSizes {
  ca: number;
  switch: number;
  router: number;
  smMaster: number;
  smStandby: number;
  edge: number;
}

export interface PollIntervals {
  topologyMs: number;
  countersMs: number;
  trafficMs: number;
}

export interface Settings {
  timezone: string;
  /** Cytoscape's `wheelSensitivity`: how far one wheel notch zooms. */
  scrollWeight: number;
  size: NodeSizes;
  /**
   * Hide a node label once it would render smaller than this many pixels.
   * 0 shows labels at every zoom.
   */
  labelMinPx: number;
  /** Selected elements at which the detail panel aggregates instead of
   *  drawing one card each. */
  summaryThreshold: number;
  poll: PollIntervals;
}

export const UTC = "UTC";

export const DEFAULTS: Settings = {
  timezone: UTC,
  scrollWeight: 2,
  size: {
    ca: 11,
    switch: 26,
    router: 22,
    smMaster: 26,
    smStandby: 26,
    edge: 1.5,
  },
  labelMinPx: 8,
  summaryThreshold: 20,
  poll: {
    topologyMs: 30_000,
    countersMs: 60_000,
    trafficMs: 5_000,
  },
};

/** Bounds, so a hand-edited localStorage value cannot make the app unusable. */
export const LIMITS = {
  scrollWeight: { min: 0.1, max: 5, step: 0.1 },
  size: { min: 4, max: 80, step: 1 },
  edge: { min: 0.5, max: 12, step: 0.5 },
  labelMinPx: { min: 0, max: 40, step: 1 },
  summaryThreshold: { min: 2, max: 500, step: 1 },
  pollMs: { min: 1_000, max: 600_000 },
} as const;

const clamp = (v: number, lo: number, hi: number): number =>
  Math.min(Math.max(v, lo), hi);

function num(raw: unknown, fallback: number, lo: number, hi: number): number {
  return typeof raw === "number" && Number.isFinite(raw)
    ? clamp(raw, lo, hi)
    : fallback;
}

/** Is this a zone `Intl` will actually accept? */
export function isUsableZone(zone: string): boolean {
  try {
    new Intl.DateTimeFormat("en", { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}


export function coerce(raw: unknown): Settings {
  if (typeof raw !== "object" || raw === null) return DEFAULTS;
  const r = raw as Record<string, unknown>;

  const zone = typeof r.timezone === "string" && isUsableZone(r.timezone)
    ? r.timezone
    : DEFAULTS.timezone;

  const size = (r.size ?? {}) as Record<string, unknown>;
  const poll = (r.poll ?? {}) as Record<string, unknown>;
  const S = LIMITS.size;
  const P = LIMITS.pollMs;

  return {
    timezone: zone,
    scrollWeight: num(r.scrollWeight, DEFAULTS.scrollWeight,
                      LIMITS.scrollWeight.min, LIMITS.scrollWeight.max),
    size: {
      ca: num(size.ca, DEFAULTS.size.ca, S.min, S.max),
      switch: num(size.switch, DEFAULTS.size.switch, S.min, S.max),
      router: num(size.router, DEFAULTS.size.router, S.min, S.max),
      smMaster: num(size.smMaster, DEFAULTS.size.smMaster, S.min, S.max),
      smStandby: num(size.smStandby, DEFAULTS.size.smStandby, S.min, S.max),
      edge: num(size.edge, DEFAULTS.size.edge,
                LIMITS.edge.min, LIMITS.edge.max),
    },
    labelMinPx: num(r.labelMinPx, DEFAULTS.labelMinPx,
                    LIMITS.labelMinPx.min, LIMITS.labelMinPx.max),
    summaryThreshold: num(r.summaryThreshold, DEFAULTS.summaryThreshold,
                          LIMITS.summaryThreshold.min,
                          LIMITS.summaryThreshold.max),
    poll: {
      topologyMs: num(poll.topologyMs, DEFAULTS.poll.topologyMs, P.min, P.max),
      countersMs: num(poll.countersMs, DEFAULTS.poll.countersMs, P.min, P.max),
      trafficMs: num(poll.trafficMs, DEFAULTS.poll.trafficMs, P.min, P.max),
    },
  };
}

/**
 * The zones to offer, newest browsers first.
 */
export function zoneOptions(): string[] {
  const browser = Intl.DateTimeFormat().resolvedOptions().timeZone;
  let all: string[] = [];
  try {
    // ES2022; absent on older Safari.
    all = (Intl as unknown as {
      supportedValuesOf?: (k: string) => string[];
    }).supportedValuesOf?.("timeZone") ?? [];
  } catch {
    all = [];
  }
  if (all.length === 0) {
    all = [
      "Europe/Zurich", "Europe/London", "Europe/Berlin", "Europe/Paris",
      "America/New_York", "America/Chicago", "America/Los_Angeles",
      "Asia/Tokyo", "Asia/Shanghai", "Asia/Kolkata", "Australia/Sydney",
    ];
  }
  // UTC first because it is the default and the one everything else is stored
  // in; the browser's own zone second because it is the likeliest choice.
  const head = [UTC, ...(browser && browser !== UTC ? [browser] : [])];
  return [...head, ...all.filter((z) => !head.includes(z))];
}
