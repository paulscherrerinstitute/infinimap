import type { Filters } from "./GraphView";
import {
  CHANGE_COLOR, CHANGE_LABEL, HEALTH_COLOR, HEALTH_LABEL, NODE_LABEL, NODE_SHAPE,
} from "../cy/palette";
import type { Change, Health, NodeKind } from "../api/types";
import { tally, type GraphModel, type Tally } from "../model/graph";
import type { OverlayValues } from "../model/counters";
import type { ErrorDeltas, TrafficRates } from "../api/types";
import { CountersPanel } from "./CountersPanel";
import { TrafficPanel } from "./TrafficPanel";
import { masksFor, type MaskValue, type ViewState } from "../view/mask";

interface Props {
  filters: Filters;
  model: GraphModel;
  setFilters: (f: Filters) => void;
  addMaskToSelection: (key: string, value: MaskValue) => void;
  view: ViewState;
  setView: (v: ViewState) => void;
  values: OverlayValues;
  deltas: ErrorDeltas | undefined;
  traffic: TrafficRates | undefined;
  countersLoading: boolean;
}

// Change rows are ordered by what you look for first, not alphabetically or by
// count: "unchanged" is the background against which the rest is read.
const CHANGE_ORDER: Change[] = ["added", "removed", "modified", "unchanged"];

export function Toolbar({
  filters, model, setFilters, addMaskToSelection,
  view, setView, values, deltas, traffic, countersLoading,
}: Props) {
  const counts = tally(model);
  // The mask decides which legend is shown, not the mode. Coerced through the
  // mode's own list so a ?mask=errors link opened in compare shows changes --
  // the same reconciliation the header picker does, from the same function.
  const mode = model.mode === "compare" ? "compare" : "live";
  const masks = masksFor(mode);
  const mask = masks.includes(view.mask) ? view.mask : masks[0];

  return (
    <div className="toolbar">
      <div className="legend">
        <div className="legend-group">
          {(Object.keys(NODE_LABEL) as NodeKind[]).map((k) => (
            <div className="legend-item" key={k} onClick={() => addMaskToSelection("type", k)}>
              <span className={`shape shape-${NODE_SHAPE[k]}`} />
              {NODE_LABEL[k]}
              <span className="stat-value">{counts.kinds[k] ?? 0}</span>
            </div>
          ))}
        </div>
      </div>

      {mask === "change" && (
        <CompareSection
          model={model}
          counts={counts}
          filters={filters}
          setFilters={setFilters}
          addMaskToSelection={addMaskToSelection}
        />
      )}
      {mask === "health" && (
        <HealthSection
          counts={counts}
          filters={filters}
          setFilters={setFilters}
          addMaskToSelection={addMaskToSelection}
        />
      )}
      {(mask === "errors" || mask === "congestion") && (
        <CountersPanel
          view={view}
          setView={setView}
          values={values}
          deltas={deltas}
          loading={countersLoading}
          addMaskToSelection={addMaskToSelection}
        />
      )}
      {mask === "traffic" && (
        <TrafficPanel
          values={values}
          traffic={traffic}
          loading={countersLoading}
          addMaskToSelection={addMaskToSelection}
        />
      )}
    </div>
  );
}

// ---- live / point ---------------------------------------------------------

function HealthSection({
  counts,
  filters,
  setFilters,
  addMaskToSelection,
}: {
  counts: Tally;
  filters: Filters;
  setFilters: (f: Filters) => void;
  addMaskToSelection: (key: string, value: MaskValue) => void;
}) {
  return (
    <>
      <div className="legend">
        <div className="legend-group">
          {(Object.keys(HEALTH_LABEL) as Health[]).map((k) =>
            counts.health[k] === undefined ? null : (
              <div className="legend-item" key={k} onClick={() => addMaskToSelection("health", k)}>
                <span className="swatch" style={{ background: HEALTH_COLOR[k] }} />
                {HEALTH_LABEL[k]}
                <span className="stat-value" style={{ color: HEALTH_COLOR[k] }}>
                  {counts.health[k]}
                </span>
              </div>
            ),
          )}
        </div>
      </div>
      <label className="toolbar-row">
        <input
          type="checkbox"
          checked={filters.dimHealthy}
          onChange={(e) => setFilters({ ...filters, dimHealthy: e.target.checked })}
        />
        Dim healthy
      </label>
    </>
  );
}

// ---- diff -----------------------------------------------------------------

function CompareSection({
  model,
  counts,
  filters,
  setFilters,
  addMaskToSelection,
}: {
  model: GraphModel;
  counts: Tally;
  filters: Filters;
  setFilters: (f: Filters) => void;
  addMaskToSelection: (key: string, value: MaskValue) => void;
}) {
  // The server proved the window empty and skipped the second topology fetch
  // entirely, so before and after are the same object. Saying so matters: an
  // all-neutral graph is indistinguishable from a bug.
  if (model.unchangedGuaranteed) {
    return (
      <p className="toolbar-note muted">
        No changes in this window. Every sweep between the two endpoints reported
        the fabric unmoved.
      </p>
    );
  }

  return (
    <>
      <div className="legend">
        <div className="legend-group">
          {CHANGE_ORDER.map((k) =>
            counts.change[k] === undefined ? null : (
              <div className="legend-item" key={k} onClick={() => addMaskToSelection("change", k)}>
                <span className="swatch" style={{ background: CHANGE_COLOR[k] }} />
                {CHANGE_LABEL[k]}
                <span className="stat-value" style={{ color: CHANGE_COLOR[k] }}>
                  {counts.change[k]}
                </span>
              </div>
            ),
          )}
        </div>
      </div>

      {/* Its own row, not a change bucket: churn is a second dimension. An
          element that flapped and came back is `unchanged`, so a reader
          scanning the buckets above would never find it. */}
      {counts.churned > 0 && (
        <p className="toolbar-note">
          <span className="stat-value">{counts.churned}</span> unchanged but flapping
        </p>
      )}

      <label className="toolbar-row">
        <input
          type="checkbox"
          checked={filters.highlightChanges}
          onChange={(e) => setFilters({ ...filters, highlightChanges: e.target.checked })}
        />
        Highlight changes
      </label>
    </>
  );
}
