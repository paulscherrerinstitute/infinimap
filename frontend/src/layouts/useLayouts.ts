import { useCallback, useEffect, useState } from "react";
import {
  forFabric,
  getActiveId,
  localStorageStore,
  setActiveId as persistActiveId,
  type Position,
  type SavedLayout,
} from "./store";

function uuid(): string {
  if (typeof crypto !== "undefined" && crypto.randomUUID) {
    return crypto.randomUUID();
  }
  // fallback for non-secure contexts / older environments
  return "xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx".replace(/[xy]/g, (c) => {
    const r = (Math.random() * 16) | 0;
    const v = c === "x" ? r : (r & 0x3) | 0x8;
    return v.toString(16);
  });
}

// Owns the saved-layout collection and the per-browser "active layout" pointer.
// The store is injected implicitly (localStorage); swapping it for a
// networked store only changes ./store.
//
// Scoped to one fabric: positions are keyed by node GUID. `fabric` is null 
// until the fabric list resolves, and nothing loads until it does.
export function useLayouts(fabric: string | null) {
  const [layouts, setLayouts] = useState<SavedLayout[]>([]);
  const [activeId, setActiveIdState] = useState<string | null>(null);
  const [loaded, setLoaded] = useState(false);

  useEffect(() => {
    if (!fabric) return;
    let live = true;
    setLoaded(false);
    localStorageStore.list().then((all) => {
      if (!live) return;
      const ls = forFabric(all, fabric);
      setLayouts(ls);
      const active = getActiveId(fabric);
      // Drop a stale pointer to a layout that was removed in another tab.
      setActiveIdState(active && ls.some((l) => l.id === active) ? active : null);
      setLoaded(true);
    });
    return () => {
      live = false;
    };
  }, [fabric]);

  const refresh = useCallback(async () => {
    if (!fabric) return;
    setLayouts(forFabric(await localStorageStore.list(), fabric));
  }, [fabric]);

  const selectActive = useCallback(
    (id: string | null) => {
      if (!fabric) return;
      persistActiveId(fabric, id);
      setActiveIdState(id);
    },
    [fabric],
  );

  const saveNew = useCallback(
    async (name: string, positions: Record<string, Position>, savedAt?: string) => {
      const now = Date.now();
      const layout: SavedLayout = {
        id: uuid(),
        name,
        createdAt: now,
        updatedAt: now,
        positions,
        savedAt,
        fabric: fabric ?? undefined,
      };
      await localStorageStore.save(layout);
      await refresh();
      selectActive(layout.id);
      return layout;
    },
    [fabric, refresh, selectActive],
  );

  const update = useCallback(
    async (
      id: string,
      patch: Partial<Pick<SavedLayout, "name" | "positions" | "savedAt">>,
    ) => {
      const existing = await localStorageStore.get(id);
      if (!existing) return;
      await localStorageStore.save({ ...existing, ...patch, updatedAt: Date.now() });
      await refresh();
    },
    [refresh],
  );

  const remove = useCallback(
    async (id: string) => {
      if (fabric && getActiveId(fabric) === id) selectActive(null);
      await localStorageStore.remove(id);
      await refresh();
    },
    [fabric, refresh, selectActive],
  );

  const activeLayout = layouts.find((l) => l.id === activeId) ?? null;

  return { layouts, activeId, activeLayout, loaded, saveNew, update, remove, selectActive };
}
