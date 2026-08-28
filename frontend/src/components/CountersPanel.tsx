// The toolbar section for a counter mask.
//
// More than a legend, unlike the health and change sections: health is already
// on every element and free, while errors and congestion need a window, a
// counter selection, and a coverage line.

import { useState } from "react";

import type { CounterName, ErrorDeltas } from "../api/types";
import type { OverlayValues } from "../model/counters";
import { COUNTER_GROUPS } from "../model/counters";
import { binCongestion, binErrors } from "../cy/overlay";
import {
  COUNTER_LABEL, ERROR_BIN_LABEL, ERROR_RAMP, TRAFFIC_RAMP, UNTRUSTED_COLOR,
  UNTRUSTED_LABEL,
} from "../cy/palette";
import {
  countersFor, WINDOWS, type MaskValue, type ViewState, type WindowKey,
} from "../view/mask";

interface Props {
  view: ViewState;
  setView: (v: ViewState) => void;
  values: OverlayValues;
  deltas: ErrorDeltas | undefined;
  loading: boolean;
  addMaskToSelection: (key: string, value: MaskValue) => void;
}

const GROUP_LABEL: Record<keyof typeof COUNTER_GROUPS, string> = {
  cable: "Cable / link",
  congestion: "Congestion",
  config: "Fabric config",
};

// What the counter accuses, which is what tells a reader whether to look at the
// edge or the node. The whole reason the list is grouped rather than alphabetical.
const GROUP_HINT: Record<keyof typeof COUNTER_GROUPS, string> = {
  cable: "Receive-side physical errors - these indict the cable, so read the edge.",
  congestion: "Buffers, not physics - read the node and what is downstream of it.",
  config: "Partition keys and routing - an operator mistake, not hardware.",
};


export function CountersPanel({
  view, setView, values, deltas, loading, addMaskToSelection,
}: Props) {
  const congestion = view.mask === "congestion";
  const ramp = congestion ? TRAFFIC_RAMP : ERROR_RAMP;
  const span = deltas?.window.span_s ?? null;

  // Histogram over links, which is the unit these counters are about.
  const hist = new Array(ramp.length).fill(0) as number[];
  for (const v of values.links.values()) {
    const bin = congestion ? binCongestion(v, span) : binErrors(v);
    if (bin !== null) hist[bin] += 1;
  }

  const cov = deltas?.coverage;
  const unpolled = cov ? cov.ports_expected - cov.ports_measured : 0;
  const selected = countersFor(view);

  return (
    <>
      <div className="toolbar-row window-picker">
        <span className="muted">Window</span>
        <select
          value={view.window}
          onChange={(e) => setView({ ...view, window: e.target.value as WindowKey })}
        >
          {(Object.keys(WINDOWS) as WindowKey[]).map((w) => (
            <option key={w} value={w}>{w}</option>
          ))}
        </select>
        {loading && <span className="muted"> · updating…</span>}
      </div>

      {/* Binned legend. Clicking a swatch selects that bucket, which is the
          actual workflow: see them, select them, open the cards. It works
          because a mask names a data key and the bins are that key's values.

          With nothing measured the ramp is not shown at all. Six buckets each
          reading zero beside a graph that is entirely grey invites the reader
          to conclude the fabric is clean, which is the opposite of what the
          grey means -- so the legend says the one true thing instead. */}
      <div className="legend">
        <div className="legend-group">
          {values.unmeasured ? (
            <div className="legend-item static">
              <span className="swatch" style={{ background: UNTRUSTED_COLOR }} />
              {UNTRUSTED_LABEL}
            </div>
          ) : (
            <>
              {ramp.map((color, bin) => (
                <div
                  className="legend-item"
                  key={bin}
                  title={congestion ? `${ERROR_BIN_LABEL[bin]} (stall rate band)` : undefined}
                  onClick={() => addMaskToSelection("bin", bin)}
                >
                  <span className="swatch" style={{ background: color }} />
                  {congestion ? CONGESTION_BIN_LABEL[bin] : ERROR_BIN_LABEL[bin]}
                  <span className="stat-value">{hist[bin]}</span>
                </div>
              ))}
              {values.untrusted.size > 0 && (
                <div
                  className="legend-item"
                  title="Reset, unpolled, or a counter this port cannot report. Not a small number — an unknown one."
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

      {/* Coverage, before the checkboxes: an empty graph because nothing was
          measured and an empty graph because nothing is wrong must not look
          alike, and this is the line that tells them apart.

          `cov` is absent as well as empty -- in flight, or errored -- and that
          case needs its own sentence rather than no sentence, because the graph
          is grey either way and a legend with no explanation beside it is how
          "unknown" gets read as "broken". */}
      {!cov ? (
        <p className="toolbar-note muted">
          {loading ? (
            <>Loading counters…</>
          ) : selected.length === 0 ? (
            <>No counters selected - tick one below.</>
          ) : (
            <span className="warn">No counter data for this view.</span>
          )}
        </p>
      ) : (
        <p className="toolbar-note muted">
          {cov.ports_measured === 0 ? (
            <span className="warn">No counters collected in this window.</span>
          ) : (
            <>
              {cov.ports_measured} of {cov.ports_expected} ports measured over{" "}
              {cov.sweeps} {cov.sweeps === 1 ? "sweep" : "sweeps"}
              {unpolled > 0 && <> · {unpolled} unpolled</>}
              {cov.ports_reset > 0 && <> · {cov.ports_reset} reset</>}
            </>
          )}
        </p>
      )}

      {values.omitted.size > 0 && (
        <p className="toolbar-note muted">
          Excluded - no port in this fabric reports{" "}
          {[...values.omitted].map((c) => COUNTER_LABEL[c] ?? c).join(", ")}.
        </p>
      )}

      {/* Congestion is xmit_wait alone by definition, so it gets no checkboxes. */}
      {congestion ? (
        <p className="toolbar-note muted">
          PortXmitWait, as a stall rate.
        </p>
      ) : (
        <CounterChecks view={view} setView={setView} />
      )}
    </>
  );
}

const CONGESTION_BIN_LABEL = ["none", "low", "moderate", "high", "severe", "stalled"];

const COUNTER_TOTAL = Object.values(COUNTER_GROUPS)
  .reduce((n, g) => n + g.length, 0);

function CounterChecks({ view, setView }: Pick<Props, "view" | "setView">) {
  const selected = new Set(view.counters);

  // The tallest block in the panel column and the one you touch least, so it
  // collapses to a single line that still says what is selected.
  //
  // Open when nothing is ticked, though: that is the state where the coverage
  // note says "tick one below", and a collapsed list hides the below.
  const [open, setOpen] = useState(selected.size === 0);

  const toggle = (c: CounterName) => {
    const next = new Set(selected);
    if (next.has(c)) next.delete(c);
    else next.add(c);
    setView({ ...view, counters: [...next] });
  };
  const setGroup = (names: readonly CounterName[], on: boolean) => {
    const next = new Set(selected);
    for (const c of names) (on ? next.add(c) : next.delete(c));
    setView({ ...view, counters: [...next] });
  };

  return (
    <div className="counter-checks">
      <button
        className="counter-checks-head"
        aria-expanded={open}
        title={open ? "Hide the counter list" : "Show the counter list"}
        onClick={() => setOpen((v) => !v)}
      >
        <span className="disclosure">{open ? "▾" : "▸"}</span>
        Counters
        <span className="stat-value">{selected.size}</span>
        <span className="muted">of {COUNTER_TOTAL}</span>
      </button>

      {open && (Object.keys(COUNTER_GROUPS) as (keyof typeof COUNTER_GROUPS)[]).map((g) => {
        const names = COUNTER_GROUPS[g];
        const all = names.every((c) => selected.has(c));
        return (
          <div className="counter-group" key={g}>
            <label className="counter-group-head" title={GROUP_HINT[g]}>
              <input
                type="checkbox"
                checked={all}
                ref={(el) => {
                  if (el) el.indeterminate = !all && names.some((c) => selected.has(c));
                }}
                onChange={(e) => setGroup(names, e.target.checked)}
              />
              {GROUP_LABEL[g]}
            </label>
            {names.map((c) => (
              <label className="counter-check" key={c}>
                <input
                  type="checkbox"
                  checked={selected.has(c)}
                  onChange={() => toggle(c)}
                />
                {COUNTER_LABEL[c] ?? c}
              </label>
            ))}
          </div>
        );
      })}
    </div>
  );
}
