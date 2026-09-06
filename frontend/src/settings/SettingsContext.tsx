// The settings provider

import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
} from "react";
import { setPolling } from "../api/queries";
import { axisLabel as rawAxisLabel, stamp as rawStamp, zoneOffsetMs } from "../time/format";
import { DEFAULTS, type Settings } from "./defaults";
import { load, save } from "./store";

interface Ctx {
  settings: Settings;
  /** A partial patch, merged over the current settings and persisted. */
  update: (patch: Partial<Settings>) => void;
  reset: () => void;
}

const SettingsCtx = createContext<Ctx>({
  settings: DEFAULTS,
  update: () => {},
  reset: () => {},
});

export function SettingsProvider({ children }: { children: React.ReactNode }) {
  // Read once, synchronously, in the initialiser.
  const [settings, setSettings] = useState<Settings>(() => load());

  useEffect(() => setPolling(settings.poll), [settings.poll]);

  const update = useCallback((patch: Partial<Settings>) => {
    setSettings((cur) => {
      const next = { ...cur, ...patch };
      save(next);
      return next;
    });
  }, []);

  const reset = useCallback(() => {
    save(DEFAULTS);
    setSettings(DEFAULTS);
  }, []);

  const value = useMemo(() => ({ settings, update, reset }),
                        [settings, update, reset]);
  return <SettingsCtx.Provider value={value}>{children}</SettingsCtx.Provider>;
}

export function useSettings(): Ctx {
  return useContext(SettingsCtx);
}

/** The display zone alone, for the many callers that need nothing else. */
export function useZone(): string {
  return useContext(SettingsCtx).settings.timezone;
}

/**
 * Formatters bound to the display zone.
 */
export function useFormat() {
  const zone = useZone();
  return useMemo(() => ({
    zone,
    stamp: (iso: string | number | null | undefined) => rawStamp(iso, zone),
    axisLabel: (ms: number, step: number) => rawAxisLabel(ms, step, zone),
    offsetAt: (ms: number) => zoneOffsetMs(ms, zone),
  }), [zone]);
}
