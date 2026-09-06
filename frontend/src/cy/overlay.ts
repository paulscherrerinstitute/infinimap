// The counter overlay: a side channel that writes bins onto element data,
// deliberately outside GraphModel so a 5 s traffic refresh does not rebuild
// the model and re-run the reconciler.
//
// Must run after the reconciler adds elements as well as on refresh - a new
// edge arrives with no overlay data. `overlay` is element data rather than a
// class so the stylesheet can gate on it: `edge[overlay="errors"][bin=4]`.

import type { Core } from "cytoscape";
import type { OverlayValues } from "../model/counters";

export type Overlay =
  | "none" | "errors" | "congestion" | "utilisation" | "throughput";

/** Fixed decade thresholds: a value < ERROR_BINS[i] lands in bin i. Fixed
 *  rather than quantile-relative, so a colour means the same thing twice. */
export const ERROR_BINS: readonly number[] = [1, 10, 100, 1_000, 100_000];

/**
 * `xmit_wait` ticks per second. Five thresholds, so bins 0..5 like the others.
 *
 * A rate rather than the raw count, so the scale is window-independent.
 * Empirical, and hardware-dependent: a tick is a port-clock period, so revisit
 * these on hardware that clocks differently.
 */
export const CONGESTION_BINS: readonly number[] = [1, 100, 10_000, 100_000, 1_000_000];

/** Utilisation percentages; the utilisation scale is linear and relative. */
export const UTILISATION_BINS: readonly number[] = [0.5, 20, 40, 60, 80];

/**
 * Absolute throughput, in Gbps. Fixed rather than quantile-relative for the
 * same reason as ERROR_BINS: a colour has to mean the same thing on a quiet
 * fabric as on a busy one.
 *
 * The boundaries sit BETWEEN the link generations rather than on round decades
 * -- FDR ~54, EDR ~100, HDR ~200, NDR ~400 -- so a link genuinely moving a
 * generation's worth of data lands in a band of its own.
 *
 * The first threshold is the server's own idle floor (IDLE_FLOOR_GBPS), so
 * "omitted from the payload" and "bin 0" agree by construction.
 */
export const THROUGHPUT_BINS: readonly number[] = [0.01, 1, 10, 50, 100];

export const BIN_COUNT = ERROR_BINS.length + 1; // 6, both scales

/** 0 for "measured, nothing happened". */
export function binErrors(value: number): number {
  if (!(value > 0)) return 0; // also catches NaN
  for (let i = 0; i < ERROR_BINS.length; i++) {
    if (value < ERROR_BINS[i]) return i;
  }
  return ERROR_BINS.length;
}

/** Congestion as a stall rate. Null when the span is unknown or zero: an
 *  unmeasurable rate is untrusted, not "not congested". */
export function binCongestion(ticks: number, spanSeconds: number | null | undefined): number | null {
  if (spanSeconds == null || !(spanSeconds > 0)) return null;
  if (!(ticks > 0)) return 0;
  const rate = ticks / spanSeconds;
  for (let i = 0; i < CONGESTION_BINS.length; i++) {
    if (rate < CONGESTION_BINS[i]) return i;
  }
  return CONGESTION_BINS.length;
}

/**
 * Utilisation, as a percentage of what the element can carry. Relative, not
 * absolute - 25 Gbps is saturation on one link and idle on another.
 *
 * Callers normalise upstream in `rollupTraffic`, because percent is the only
 * unit links and nodes share. Null means unknown, which is not idle.
 */
export function binUtilisation(pct: number | null | undefined): number | null {
  if (pct == null || Number.isNaN(pct)) return null;
  if (!(pct > 0)) return 0;
  for (let i = 0; i < UTILISATION_BINS.length; i++) {
    if (pct < UTILISATION_BINS[i]) return i;
  }
  return UTILISATION_BINS.length;
}

/**
 * Absolute flow rate, in Gbps.
 */
export function binThroughput(gbps: number | null | undefined): number | null {
  if (gbps == null || Number.isNaN(gbps)) return null;
  if (!(gbps > 0)) return 0;
  for (let i = 0; i < THROUGHPUT_BINS.length; i++) {
    if (gbps < THROUGHPUT_BINS[i]) return i;
  }
  return THROUGHPUT_BINS.length;
}

/** One link's Gbps against its own line rate. */
export function binTraffic(gbps: number, rateGbps: number | null | undefined): number | null {
  if (rateGbps == null || !(rateGbps > 0)) return null;
  return binUtilisation((gbps / rateGbps) * 100);
}

/**
 * How one element's value becomes a bin. Caller-supplied because each overlay
 * scales differently; binning `xmit_wait` on the error scale collapses almost
 * every link into the top bin.
 *
 * Null means "cannot be binned" - unknown rather than zero.
 */
export type BinOf = (value: number, id: string) => number | null;

/** What one element should be showing. */
function binFor(
  id: string, value: number | undefined, values: OverlayValues, binOf: BinOf,
): { bin: number; untrusted: boolean } {
  // Untrusted wins over any number: a reset delta is only a lower bound and an
  // unpolled port has none at all, so the ramp would state a measurement that
  // was never made.
  if (values.untrusted.has(id)) return { bin: 0, untrusted: true };
  const bin = binOf(value ?? 0, id);
  return bin === null
    ? { bin: 0, untrusted: true }
    : { bin, untrusted: false };
}

/**
 * Write the overlay onto the core. Idempotent, and cheap when nothing moved.
 *
 * `overlay: "none"` (or null values) clears, which must actually remove the
 * data rather than stop updating it.
 */
export function applyOverlay(
  cy: Core, overlay: Overlay, values: OverlayValues | null,
  binOf: BinOf = binErrors,
): void {
  // `data()` marks computed style dirty; `removeData()` does NOT, so a cleared
  // element keeps its last bin's colour forever. Hence the explicit style pass
  // below -- gated, so a 5 s refresh does not restyle the whole graph.
  let cleared = false;

  cy.batch(() => {
    if (overlay === "none" || values === null) {
      cy.elements().forEach((ele) => {
        if (ele.data("overlay") !== undefined) {
          ele.removeData("overlay bin untrusted");
          cleared = true;
        }
      });
      return;
    }

    cy.elements().forEach((ele) => {
      const id = ele.id();
      // Compound parents are in neither map; leaving them unset keeps the
      // box neutral behind its children.
      const isEdge = ele.isEdge();
      const value = isEdge ? values.links.get(id) : values.nodes.get(id);
      const known = value !== undefined || values.untrusted.has(id);
      if (!known) {
        if (ele.data("overlay") !== undefined) {
          ele.removeData("overlay bin untrusted");
          cleared = true;
        }
        return;
      }

      const { bin, untrusted } = binFor(id, value, values, binOf);
      // Only touch what changed: writing every element restyles every one.
      if (ele.data("overlay") === overlay
          && ele.data("bin") === bin
          && ele.data("untrusted") === untrusted) {
        return;
      }
      ele.data({ overlay, bin, untrusted });
    });
  });

  // Optional chain: a headless core without `styleEnabled` has no style
  // object, and nothing stale to invalidate.
  if (cleared) cy.style()?.update();
}
