// Persistence for the local settings.

import { coerce, DEFAULTS, type Settings } from "./defaults";

const KEY = "infinimap.settings.v1";

export function load(): Settings {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return DEFAULTS;
    return coerce(JSON.parse(raw));
  } catch {
    return DEFAULTS;
  }
}

export function save(settings: Settings): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(settings));
  } catch {
    /* quota, or storage disabled. The session still works, it just forgets. */
  }
}

export function clear(): void {
  try {
    localStorage.removeItem(KEY);
  } catch {
    /* nothing to do */
  }
}
