// Query keys and the hooks built on them. Two conventions run throughout:
//
//   * `at` is part of every key. The same element at two instants is two
//     resources.
//
//   * Historical data never goes stale, so staleTime is Infinity when `at` is
//     set and 0 when it is null.

import { useQuery } from "@tanstack/react-query";

import {
  getDiff, getErrorDeltas, getEvents, getHead, getLinkDetail, getNodeDetail,
  getTraffic,
  getSnapshots, getTopology, listFabrics,
} from "./client";
import type { Instant } from "./types";

/** Milliseconds. A historical answer is fixed; a live one is refetched. */
const forever = (at: Instant) => (at === null ? 0 : Infinity);

// TODO: THESE SHOULD BE CONFIGURABLE (SOME SORT OF CONFIG FILE)
const POLL_MS = 30_000;
const COUNTER_POLL_MS = 60_000;
const TRAFFIC_POLL_MS = 5_000;

/**
 * How often an open card re-asks. A card carrying traffic follows the graph's
 * clock, not the topology's: it exists to explain the colour it sits beside,
 * so it must not lag it. Only expanded cards fetch at all.
 */
const detailPoll = (at: Instant, withTraffic: boolean) =>
  at !== null ? false : withTraffic ? TRAFFIC_POLL_MS : POLL_MS;

/**
 * How often to re-ask, or false for never.
 */
const live = (at: Instant) => (at === null ? POLL_MS : false);

// ---- keys -----------------------------------------------------------------

export const keys = {
  fabrics: () => ["fabrics"] as const,
  head: (fabric: string, at: Instant) => ["head", fabric, at] as const,
  topology: (fabric: string, at: Instant) => ["topology", fabric, at] as const,
  // The counter window is part of the key, not just the URL: two windows over
  // one snapshot are two different answers.
  node: (fabric: string, guid: string, at: Instant, win?: number, traffic?: boolean) =>
    ["node", fabric, guid, at, win ?? null, !!traffic] as const,
  link: (fabric: string, id: string, at: Instant, win?: number, traffic?: boolean) =>
    ["link", fabric, id, at, win ?? null, !!traffic] as const,
  diff: (fabric: string, since: string, until: Instant) =>
    ["diff", fabric, since, until] as const,
  events: (fabric: string, since?: string, until?: string) =>
    ["events", fabric, since ?? null, until ?? null] as const,
  snapshots: (fabric: string, since: string, until: string, limit: number) =>
    ["snapshots", fabric, since, until, limit] as const,
  errorDeltas: (fabric: string, since: string, until: Instant) =>
    ["counters", "errors", fabric, since, until] as const,
  traffic: (fabric: string, at: Instant) =>
    ["counters", "traffic", fabric, at] as const,
};

// ---- fabrics --------------------------------------------------------------

/**
 * The list of fabrics, fetched once per page load. Fabrics are created by an
 * operator rather than by a sweep, so there is nothing to poll for.
 */
export function useFabrics() {
  return useQuery({
    queryKey: keys.fabrics(),
    queryFn: ({ signal }) => listFabrics(signal),
    staleTime: Infinity,
  });
}

// ---- graph ----------------------------------------------------------------

// `enabled` is false in compare mode, where the graph comes from /diff. Both
// hooks are still called unconditionally, so this is what stops the unused one
// from fetching.
export function useTopology(fabric: string, at: Instant, enabled = true) {
  return useQuery({
    queryKey: keys.topology(fabric, at),
    queryFn: ({ signal }) => getTopology(fabric, at, signal),
    enabled,
    staleTime: forever(at),
    refetchInterval: live(at),
  });
}

export function useDiff(fabric: string, since: string | null, until: Instant) {
  return useQuery({
    queryKey: keys.diff(fabric, since ?? "", until),
    queryFn: ({ signal }) => getDiff(fabric, since!, until, signal),
    enabled: since !== null,
    staleTime: forever(until),
    // An open-ended range ("…to now") keeps moving; a closed one never does.
    refetchInterval: live(until),
  });
}

export function useHead(fabric: string, at: Instant, enabled = true) {
  return useQuery({
    queryKey: keys.head(fabric, at),
    queryFn: ({ signal }) => getHead(fabric, at, signal),
    enabled,
    staleTime: forever(at),
    refetchInterval: live(at),
  });
}

// ---- detail ---------------------------------------------------------------
//
// Detail polls on the same clock as the graph deliberately. These are stale
// the moment a new sweep lands, and without a poll of their own an open card
// would sit showing an older sweep than the topology drawn beside it.

export function useNodeDetail(fabric: string, guid: string | null, at: Instant,
                              enabled = true, countersWindow?: number,
                              withTraffic = false) {
  return useQuery({
    queryKey: keys.node(fabric, guid ?? "", at, countersWindow, withTraffic),
    queryFn: ({ signal }) =>
      getNodeDetail(fabric, guid!, at, countersWindow, withTraffic, signal),
    enabled: enabled && guid !== null,
    staleTime: forever(at),
    refetchInterval: detailPoll(at, withTraffic),
  });
}

export function useLinkDetail(fabric: string, id: string | null, at: Instant,
                              enabled = true, countersWindow?: number,
                              withTraffic = false) {
  return useQuery({
    queryKey: keys.link(fabric, id ?? "", at, countersWindow, withTraffic),
    queryFn: ({ signal }) =>
      getLinkDetail(fabric, id!, at, countersWindow, withTraffic, signal),
    enabled: enabled && id !== null,
    staleTime: forever(at),
    refetchInterval: detailPoll(at, withTraffic),
  });
}

// ---- history --------------------------------------------------------------

/**
 * The sweep index for one window of the timeline.
 *
 * `staleTime: Infinity` is correct rather than lazy here. The timeline's window
 * is clamped to "now", and "now" is the newest sweep rather than the wall clock
 * - so the key changes exactly when there is new data to see, and never
 * otherwise. Panning or zooming produces a different key of its own.
 */
export function useSnapshots(fabric: string, since: string, until: string,
                             limit: number, enabled = true) {
  return useQuery({
    queryKey: keys.snapshots(fabric, since, until, limit),
    queryFn: ({ signal }) => getSnapshots(fabric, since, until, limit, signal),
    enabled,
    staleTime: Infinity,
  });
}

export function useEvents(fabric: string, since?: string, until?: string,
                          enabled = true) {
  return useQuery({
    queryKey: keys.events(fabric, since, until),
    queryFn: ({ signal }) => getEvents(fabric, since, until, signal),
    enabled,
  });
}

// ---- counters -------------------------------------------------------------

// Live utilisation, or the rate at a pinned instant.
export function useTraffic(fabric: string, at: Instant, enabled = true) {
  return useQuery({
    queryKey: keys.traffic(fabric, at),
    queryFn: ({ signal }) => getTraffic(fabric, at, signal),
    enabled,
    staleTime: forever(at),
    refetchInterval: at === null ? TRAFFIC_POLL_MS : false,
  });
}

// Per-port error deltas over a window.
export function useErrorDeltas(fabric: string, since: string | null,
                               until: Instant, enabled = true) {
  return useQuery({
    queryKey: keys.errorDeltas(fabric, since ?? "", until),
    queryFn: ({ signal }) => getErrorDeltas(fabric, since!, until, signal),
    enabled: enabled && since !== null,
    staleTime: forever(until),
    refetchInterval: until === null ? COUNTER_POLL_MS : false,
  });
}
