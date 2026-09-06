// The toolbar section for the two traffic masks.
//
// Its own component rather than a branch inside CountersPanel, because almost
// nothing is shared.
//
// One component for BOTH traffic masks, though, because they read one payload:
//
//   utilisation  a share of each link's own line rate. "How full is it."
//   throughput   absolute Gbps. "How much is moving."
//
// Everything except the legend and the closing footnote is identical between
// them.

import type { TrafficRates } from "../api/types";
import type { OverlayValues } from "../model/counters";
import { binThroughput, binUtilisation, UTILISATION_BINS } from "../cy/overlay";
import {
  LOAD_RAMP, THROUGHPUT_BIN_LABEL, THROUGHPUT_RAMP, UNTRUSTED_COLOR,
  UNTRUSTED_LABEL,
} from "../cy/palette";
import type { Mask, MaskValue } from "../view/mask";

interface Props {
  mask: Mask;
  values: OverlayValues;
  traffic: TrafficRates | undefined;
  loading: boolean;
  addMaskToSelection: (key: string, value: MaskValue) => void;
}

/** Derived from the thresholds rather than written out, so the labels cannot
 *  drift from the bins the graph is actually using. */
const UTILISATION_BIN_LABEL: string[] = [
  "idle",
  ...UTILISATION_BINS.map((hi, i) =>
    `${i === 0 ? 0 : UTILISATION_BINS[i - 1]}–${hi}%`).slice(1),
  `${UTILISATION_BINS[UTILISATION_BINS.length - 1]}%+`,
];

/** Everything that differs between the two masks, in one place. */
const SPEC = {
  utilisation: {
    heading: "Utilisation",
    unit: "% of line rate",
    ramp: LOAD_RAMP,
    labels: UTILISATION_BIN_LABEL,
    binOf: binUtilisation,
    note: (
      <>
        A share of each link’s own line rate, not absolute Gbps. 
        Switch to <strong>Throughput</strong> to see those.
      </>
    ),
  },
  throughput: {
    heading: "Throughput",
    unit: "Gbps",
    ramp: THROUGHPUT_RAMP,
    labels: THROUGHPUT_BIN_LABEL,
    binOf: binThroughput,
    note: (
      <>
        Absolute Gbps. Switch to{" "}
        <strong>Utilisation</strong> to see how full each link is.
      </>
    ),
  },
} as const;

/** Gbps at a readable magnitude. A fabric spans idle ports and 200 Gbps ones. */
function gbps(v: number): string {
  if (v >= 100) return v.toFixed(0);
  if (v >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

export function TrafficPanel({
  mask, values, traffic, loading, addMaskToSelection,
}: Props) {
  const spec = SPEC[mask as keyof typeof SPEC] ?? SPEC.utilisation;

  const hist = new Array(spec.ramp.length).fill(0) as number[];
  for (const v of values.links.values()) {
    const bin = spec.binOf(v);
    if (bin !== null) hist[bin] += 1;
  }

  const cov = traffic?.coverage;
  // Summed over ports, then halved: every byte on a link is transmitted at one
  // end and received at the other, so adding both ends counts it twice.
  const total = (traffic?.ports ?? []).reduce(
    (a, p) => a + (p.tx ?? 0) + (p.rx ?? 0), 0) / 2;

  // The busiest single element on screen. Only stated for throughput, where it
  // is a quantity; the utilisation equivalent is already the top legend row.
  const peak = values.peak;

  return (
    <>
      <div className="toolbar-row window-picker">
        <span className="muted">{spec.heading}</span>
        <span className="muted"> · {spec.unit}</span>
        {loading && <span className="muted"> · updating…</span>}
      </div>

      <div className="legend">
        <div className="legend-group">
          {values.unmeasured ? (
            <div className="legend-item static">
              <span className="swatch" style={{ background: UNTRUSTED_COLOR }} />
              {UNTRUSTED_LABEL}
            </div>
          ) : (
            <>
              {spec.ramp.map((color, bin) => (
                <div
                  className="legend-item"
                  key={bin}
                  onClick={() => addMaskToSelection("bin", bin)}
                >
                  <span className="swatch" style={{ background: color }} />
                  {spec.labels[bin]}
                  <span className="stat-value">{hist[bin]}</span>
                </div>
              ))}
              {values.untrusted.size > 0 && (
                <div
                  className="legend-item"
                  title="Unpolled, or a byte counter that wrapped. Not a slow link — an unmeasured one."
                  onClick={() => addMaskToSelection("untrusted", true)}
                >
                  <span className="swatch" style={{ background: UNTRUSTED_COLOR }} />
                  {UNTRUSTED_LABEL}
                  <span className="stat-value">{values.untrusted.size}</span>
                </div>
              )}
            </>
          )}
        </div>
      </div>

      {!cov ? (
        <p className="toolbar-note muted">
          {loading ? <>Loading traffic…</>
            : <span className="warn">No traffic data for this view.</span>}
        </p>
      ) : (
        <p className="toolbar-note muted">
          {cov.ports_measured === 0 ? (
            <span className="warn">No traffic collected in this window.</span>
          ) : (
            <>
              {cov.ports_measured} of {cov.ports_expected} ports over{" "}
              {traffic?.span_s ? `${Math.round(traffic.span_s)}s` : "an unknown span"}
              {cov.ports_reset > 0 && <> · {cov.ports_reset} wrapped</>}
            </>
          )}
        </p>
      )}

      {cov && cov.ports_measured > 0 && (
        <p className="toolbar-note muted">
          <span className="stat-value">{gbps(total)}</span> Gbps across the fabric
          {mask === "throughput" && peak > 0 && (
            <> · busiest <span className="stat-value">{gbps(peak)}</span></>
          )}
          {traffic?.cadence_s
            ? <> · collected every {Math.round(traffic.cadence_s)}s</>
            : null}
        </p>
      )}

      <p className="toolbar-note muted">{spec.note}</p>
    </>
  );
}
