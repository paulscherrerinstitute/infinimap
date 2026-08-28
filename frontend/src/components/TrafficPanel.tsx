// The toolbar section for the traffic mask.
//
// Its own component rather than a branch inside CountersPanel, because almost
// nothing is shared.

import type { TrafficRates } from "../api/types";
import type { OverlayValues } from "../model/counters";
import { binUtilisation } from "../cy/overlay";
import { TRAFFIC_RAMP, UNTRUSTED_COLOR, UNTRUSTED_LABEL } from "../cy/palette";
import { TRAFFIC_BINS } from "../cy/overlay";
import type { MaskValue } from "../view/mask";

interface Props {
  values: OverlayValues;
  traffic: TrafficRates | undefined;
  loading: boolean;
  addMaskToSelection: (key: string, value: MaskValue) => void;
}

/** Derived from the thresholds rather than written out, so the labels cannot
 *  drift from the bins the graph is actually using. */
const BIN_LABEL: string[] = [
  "idle",
  ...TRAFFIC_BINS.map((hi, i) =>
    `${i === 0 ? 0 : TRAFFIC_BINS[i - 1]}–${hi}%`).slice(1),
  `${TRAFFIC_BINS[TRAFFIC_BINS.length - 1]}%+`,
];

/** Gbps at a readable magnitude. A fabric spans idle ports and 200 Gbps ones. */
function gbps(v: number): string {
  if (v >= 100) return v.toFixed(0);
  if (v >= 10) return v.toFixed(1);
  return v.toFixed(2);
}

export function TrafficPanel({ values, traffic, loading, addMaskToSelection }: Props) {
  const hist = new Array(TRAFFIC_RAMP.length).fill(0) as number[];
  for (const v of values.links.values()) {
    const bin = binUtilisation(v);
    if (bin !== null) hist[bin] += 1;
  }

  const cov = traffic?.coverage;
  // Summed over ports, then halved: every byte on a link is transmitted at one
  // end and received at the other, so adding both ends counts it twice.
  const total = (traffic?.ports ?? []).reduce(
    (a, p) => a + (p.tx ?? 0) + (p.rx ?? 0), 0) / 2;

  return (
    <>
      <div className="toolbar-row window-picker">
        <span className="muted">Utilisation</span>
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
              {TRAFFIC_RAMP.map((color, bin) => (
                <div
                  className="legend-item"
                  key={bin}
                  onClick={() => addMaskToSelection("bin", bin)}
                >
                  <span className="swatch" style={{ background: color }} />
                  {BIN_LABEL[bin]}
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
          {traffic?.cadence_s
            ? <> · collected every {Math.round(traffic.cadence_s)}s</>
            : null}
        </p>
      )}

      {/* Utilisation is relative to each link's own rate, and saying so is not
          decoration: the alternative reading -- that the colours are absolute
          Gbps -- makes a saturated 25G link and an idle 200G one look like the
          same fact. */}
      <p className="toolbar-note muted">
        A share of each link’s own line rate, not absolute Gbps. Ports on no
        link have no rate to divide by and stay uncoloured.
      </p>
    </>
  );
}
