// One thin function per endpoint.
//
// No parsing step and no runtime validation: the JSON is the model.
//
// Caching is the browser's job. Every response carries an ETag with either
// `Cache-Control: no-cache` or `immutable`, and the browser revalidates and
// resolves fetch() with the cached body on a 304 by itself. The rule that
// follows: never pass `cache: "no-store"`, which opts out of it.

import type {
  Diff, ErrorDeltas, Events, FabricRow, Head, Instant, LinkDetail,
  NodeDetail, SelectionRequest, SelectionSummary, Snapshots, Topology,
  TrafficRates,
} from "./types";

const BASE = "/api/v1";

/** A non-2xx response. Carries the status so callers can tell 404 from 500. */
export class ApiError extends Error {
  constructor(readonly status: number, readonly url: string, message: string) {
    super(message);
    this.name = "ApiError";
  }

  /** The element does not exist at the requested instant - an empty state in
   *  the UI rather than a failure. */
  get isNotFound(): boolean {
    return this.status === 404;
  }
}

async function get<T>(path: string, params?: Record<string, string | null | undefined>,
                      signal?: AbortSignal): Promise<T> {
  const qs = new URLSearchParams();
  for (const [k, v] of Object.entries(params ?? {})) {
    if (v != null) qs.set(k, v);
  }
  const url = `${BASE}${path}${qs.size ? `?${qs}` : ""}`;

  const res = await fetch(url, { signal, headers: { Accept: "application/json" } });
  if (!res.ok) {
    // FastAPI puts the reason in `detail`; fall back to the status text when
    // the body is empty or not JSON (a proxy error, say).
    let detail = res.statusText;
    try {
      const body = await res.json();
      if (typeof body?.detail === "string") detail = body.detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, url, detail);
  }
  return res.json() as Promise<T>;
}

/**
 * The one POST in this client.
 */
async function post<T>(path: string, body: unknown,
                       signal?: AbortSignal): Promise<T> {
  const url = `${BASE}${path}`;
  const res = await fetch(url, {
    method: "POST",
    signal,
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const parsed = await res.json();
      if (typeof parsed?.detail === "string") detail = parsed.detail;
    } catch {
      /* keep statusText */
    }
    throw new ApiError(res.status, url, detail);
  }
  return res.json() as Promise<T>;
}

// ---- endpoints ------------------------------------------------------------

export const listFabrics = (signal?: AbortSignal) =>
  get<FabricRow[]>("/fabrics", undefined, signal);

export const getHead = (fabric: string, at: Instant, signal?: AbortSignal) =>
  get<Head>(`/fabrics/${fabric}/head`, { at }, signal);

export const getTopology = (fabric: string, at: Instant, signal?: AbortSignal) =>
  get<Topology>(`/fabrics/${fabric}/topology`, { at }, signal);

// `countersWindow` is seconds, and the caller passes the window its OVERLAY is
// using -- a server-side default would have the card report a different span
// than the colour beside it.
export const getNodeDetail = (fabric: string, guid: string, at: Instant,
                              countersWindow?: number, withTraffic?: boolean,
                              signal?: AbortSignal) =>
  get<NodeDetail>(`/fabrics/${fabric}/nodes/${guid}`,
                  { at, counters_window: countersWindow?.toString(),
                    with_traffic: withTraffic ? "true" : undefined }, signal);

// The link id contains ':' and '-'. Neither is special in a path segment, but
// encode anyway so the id stays opaque to this layer.
export const getLinkDetail = (fabric: string, linkId: string, at: Instant,
                              countersWindow?: number, withTraffic?: boolean,
                              signal?: AbortSignal) =>
  get<LinkDetail>(`/fabrics/${fabric}/links/${encodeURIComponent(linkId)}`,
                  { at, counters_window: countersWindow?.toString(),
                    with_traffic: withTraffic ? "true" : undefined }, signal);

export const getDiff = (fabric: string, since: string, until: Instant,
                        signal?: AbortSignal) =>
  get<Diff>(`/fabrics/${fabric}/diff`, { since, until }, signal);

export const getEvents = (fabric: string, since?: string, until?: string,
                          signal?: AbortSignal) =>
  get<Events>(`/fabrics/${fabric}/events`, { since, until }, signal);

// `limit` is explicit because the caller must recognise its own ceiling: rows
// come back newest-first, so a truncated answer covers only the recent end of
// the window.
export const getSnapshots = (fabric: string, since?: string, until?: string,
                             limit?: number, signal?: AbortSignal) =>
  get<Snapshots>(`/fabrics/${fabric}/snapshots`,
                 { since, until, limit: limit?.toString() }, signal);

// `until` follows the `at` convention: null means "to now", and the server
// fills it in. `since` never can - a delta with no start is not a delta.
export const getErrorDeltas = (fabric: string, since: string, until: Instant,
                               signal?: AbortSignal) =>
  get<ErrorDeltas>(`/fabrics/${fabric}/counters/errors`, { since, until }, signal);

// `at` on the same convention, and NO window: the collector's traffic interval
// is configurable, so the server sizes the window from the cadence it observes.
export const getTraffic = (fabric: string, at: Instant, signal?: AbortSignal) =>
  get<TrafficRates>(`/fabrics/${fabric}/counters/traffic`, { at }, signal);

// Aggregates a whole selection in one request. The server answers only what
// the browser cannot compute from the graph model it already holds -- ports,
// firmware, counters, and a degraded link's reason.
export const getSelectionSummary = (fabric: string, req: SelectionRequest,
                                    signal?: AbortSignal) =>
  post<SelectionSummary>(`/fabrics/${fabric}/selection`, req, signal);
