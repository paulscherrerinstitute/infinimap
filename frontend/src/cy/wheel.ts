// Changing the zoom sensitivity of a live core.

import type { Core } from "cytoscape";

/** Set how far one wheel notch zooms, on an already-built core. */
export function setWheelSensitivity(cy: Core | null, value: number): void {
  if (!cy || !Number.isFinite(value) || value <= 0) return;
  const renderer = (cy as unknown as {
    _private?: { renderer?: { wheelSensitivity?: number } };
  })._private?.renderer;
  if (renderer) renderer.wheelSensitivity = value;
}
