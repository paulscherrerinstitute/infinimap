// The last fabric this browser looked at. Per-browser rather than per-link:
// this only decides where a fresh tab with no `?fabric=` starts.

const LAST_KEY = "ibfabric.fabric.v1";

export function getLastFabric(): string | null {
  try {
    return localStorage.getItem(LAST_KEY);
  } catch {
    return null; // private mode with storage disabled: fall through to default
  }
}

export function setLastFabric(name: string): void {
  try {
    localStorage.setItem(LAST_KEY, name);
  } catch {
    /* not worth failing a render over */
  }
}
