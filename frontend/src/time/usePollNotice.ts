// A toast per completed poll, so the live loop is visible rather than assumed.
//
// It distinguishes three kinds of quiet, which the header cannot:
//
//   * a new sweep landed and the hash did not move -> provably quiet
//   * no new sweep at all -> polled faster than swept, or a stopped collector
//   * the poll itself failed -> the API is unreachable, which is different
//
// Keys off `dataUpdatedAt`, which react-query bumps on every successful fetch
// even when structural sharing returns the identical object -- so a poll that
// changed nothing still counts as a poll.

import { useEffect, useRef } from "react";
import type { UseQueryResult } from "@tanstack/react-query";

import type { Topology } from "../api/types";
import { Toast } from "../components/Toast";
import { stamp } from "./format";

/** How long to display each toast, in milliseconds. */
const MS = 15000;

type Seen = { snapshotId: number; hash: string };

/**
 * `live` is passed rather than derived from the instant, because compare-to-now
 * also has a null instant and this is about the live topology specifically.
 */
export function usePollNotice(query: UseQueryResult<Topology>, live: boolean): void {
  const seen = useRef<Seen | null>(null);

  // Entering or leaving live mode restarts the sequence: the first fetch that
  // follows is a load, not a poll, and should pass silently.
  useEffect(() => {
    seen.current = null;
  }, [live]);

  const { data, dataUpdatedAt } = query;
  useEffect(() => {
    if (!live || !data) return;

    const { snapshot_id, topology_hash, collected_at } = data.resolved;
    const previous = seen.current;
    seen.current = { snapshotId: snapshot_id, hash: topology_hash };
    if (!previous) return; // first answer in this mode

    const sweep = stamp(collected_at);
    if (previous.hash !== topology_hash) {
      Toast.ok("Fabric changed", `sweep ${sweep}`, MS);
    } else if (previous.snapshotId !== snapshot_id) {
      Toast.info("New sweep · no change", `sweep ${sweep}`, MS);
    } else {
      Toast.info("Polled · no new sweep", `still on ${sweep}`, MS);
    }
    // `data` is read, not depended on: a poll that changes nothing leaves it
    // referentially identical, and that poll is exactly what this reports.
  }, [dataUpdatedAt, live]);

  const { error, errorUpdatedAt } = query;
  useEffect(() => {
    if (!live || !error) return;
    Toast.warn("Poll failed", error instanceof Error ? error.message : String(error), MS);
  }, [errorUpdatedAt, live]);
}
