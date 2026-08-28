// Which fabric the app is looking at.
//
// Resolution order is URL -> localStorage -> the first fabric listed.

import { useCallback, useEffect, useState } from "react";

import { useFabrics } from "../api/queries";
import type { FabricRow } from "../api/types";
import { getLastFabric, setLastFabric } from "./store";
import { readFabric, withFabric } from "./url";

export interface FabricApi {
  /** The fabric to query, or null until the list has resolved. */
  fabric: string | null;
  fabrics: FabricRow[];
  select: (name: string) => void;
  loading: boolean;
  error: unknown;
}

export function useFabric(): FabricApi {
  const query = useFabrics();
  const fabrics = query.data ?? [];

  const [chosen, setChosen] = useState<string | null>(() =>
    typeof window === "undefined"
      ? null
      : readFabric(window.location.search) ?? getLastFabric(),
  );

  // Resolve against the list rather than trusting the name.
  const known = fabrics.some((f) => f.name === chosen);
  const fabric = known ? chosen : fabrics[0]?.name ?? null;

  useEffect(() => {
    if (!fabric) return;
    setLastFabric(fabric);

    // Only name the fabric in the URL when there is a choice to record: on a
    // single-fabric deployment the parameter is noise on every link.
    const name = fabrics.length > 1 ? fabric : null;
    const search = withFabric(window.location.search, name);
    window.history.replaceState(null, "", `${window.location.pathname}${search}`);
  }, [fabric, fabrics.length]);

  const select = useCallback((name: string) => setChosen(name), []);

  return { fabric, fabrics, select, loading: query.isPending, error: query.error };
}
