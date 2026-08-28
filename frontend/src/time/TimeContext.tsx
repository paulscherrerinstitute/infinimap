// The instant the app is looking at, delivered to the tree.
//
// A context because cards need `at` and sit four levels below App; threading it
// through three components that do not care about it would be worse. The state
// itself lives in ./state and its URL encoding in ./url.

import { createContext, useContext, useEffect, useMemo, useState } from "react";
import type { ReactNode } from "react";

import type { Instant } from "../api/types";
import { instantOf, LIVE, type TimeState } from "./state";
import { parseTime, toSearch } from "./url";

interface TimeApi {
  time: TimeState;
  setTime: (t: TimeState) => void;
  /** Back to following the newest sweep. The exit from both point and compare. */
  goLive: () => void;
}

const Ctx = createContext<TimeApi | null>(null);

export function TimeProvider({ children }: { children: ReactNode }) {
  // Read once, at startup: after that the URL follows the state rather than
  // the other way round.
  const [time, setTime] = useState<TimeState>(() =>
    typeof window === "undefined" ? LIVE : parseTime(window.location.search),
  );

  // replaceState rather than push: the scrubber commits a new range on every
  // release, and one history entry per drag would make the back button
  // useless.
  useEffect(() => {
    const search = toSearch(time, window.location.search);
    window.history.replaceState(null, "", `${window.location.pathname}${search}`);
  }, [time]);

  const value = useMemo<TimeApi>(
    () => ({ time, setTime, goLive: () => setTime(LIVE) }),
    [time],
  );
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useTime(): TimeApi {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("useTime must be used within a TimeProvider");
  return ctx;
}

/** Just the instant, for the common case of building a query key. */
export const useInstant = (): Instant => instantOf(useTime().time);
