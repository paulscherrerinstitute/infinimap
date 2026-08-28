// Persistence for user-arranged graph layouts.
//
// A layout is a `guid -> {x, y}` map. Node ids are stable GUIDs, so saved
// positions survive reloads and new snapshots.
//
// Storage sits behind the async `LayoutStore` interface, so localStorage can be
// swapped for a server-backed store without touching the UI.

export interface Position {
  x: number;
  y: number;
}

export interface SavedLayout {
  id: string;
  name: string;
  createdAt: number;
  updatedAt: number;
  positions: Record<string, Position>;
  /** Which fabric the positions belong to.
   *  Optional, and absence means "any", so older layouts need no migration. */
  fabric?: string;
  /** `collected_at` of the sweep this arrangement was authored against.
   *
   *  A layout is only as current as the topology it was drawn from: a node
   *  recabled afterwards keeps its old coordinates and ends up far from its new
   *  neighbour. Without this, a stale layout looks identical to a fresh one.
   *  Optional, so older layouts keep loading. */
  savedAt?: string;
}

export interface LayoutStore {
  list(): Promise<SavedLayout[]>;
  get(id: string): Promise<SavedLayout | null>;
  save(layout: SavedLayout): Promise<void>;
  remove(id: string): Promise<void>;
}

const LAYOUTS_KEY = "ibfabric.layouts.v1";
const ACTIVE_KEY = "ibfabric.activeLayout.v1";

export function forFabric(all: SavedLayout[], fabric: string): SavedLayout[] {
  return all.filter((l) => l.fabric === undefined || l.fabric === fabric);
}

function readAll(): Record<string, SavedLayout> {
  try {
    const raw = localStorage.getItem(LAYOUTS_KEY);
    return raw ? (JSON.parse(raw) as Record<string, SavedLayout>) : {};
  } catch {
    return {};
  }
}

function writeAll(map: Record<string, SavedLayout>): void {
  localStorage.setItem(LAYOUTS_KEY, JSON.stringify(map));
}

// The concrete localStorage-backed store. Async signatures are intentional so a
// networked implementation is a drop-in replacement.
export const localStorageStore: LayoutStore = {
  async list() {
    return Object.values(readAll()).sort((a, b) => b.updatedAt - a.updatedAt);
  },
  async get(id) {
    return readAll()[id] ?? null;
  },
  async save(layout) {
    const all = readAll();
    all[layout.id] = layout;
    writeAll(all);
  },
  async remove(id) {
    const all = readAll();
    delete all[id];
    writeAll(all);
  },
};

// Which layout is currently applied. Per-browser (user-specific), unlike the
// shared layout files a backend would hold, so this stays in localStorage.
const activeKey = (fabric: string) => `${ACTIVE_KEY}:${fabric}`;

export function getActiveId(fabric: string): string | null {
  return localStorage.getItem(activeKey(fabric));
}

export function setActiveId(fabric: string, id: string | null): void {
  if (id) localStorage.setItem(activeKey(fabric), id);
  else localStorage.removeItem(activeKey(fabric));
}
