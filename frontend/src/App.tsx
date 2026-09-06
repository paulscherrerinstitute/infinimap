import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { Core } from "cytoscape";
import type { SelectedItem } from "./api/types";
import { ApiError } from "./api/client";
import { useDiff, useErrorDeltas, useTopology, useTraffic } from "./api/queries";
import { useFabric } from "./fabric/useFabric";
import { useInstant, useTime } from "./time/TimeContext";
import { usePollNotice } from "./time/usePollNotice";
import { Timeline, type TimelineMetrics } from "./time/Timeline";
import { fromDiff, fromTopology, type GraphModel } from "./model/graph";
import { GraphView, type Filters } from "./components/GraphView";
import { DetailPanel } from "./components/detail/DetailPanel";
import { Header } from "./components/Header";
import { Toolbar } from "./components/Toolbar";
import { LayoutPanel } from "./components/LayoutPanel";
import { useLayouts } from "./layouts/useLayouts";
import { ToastHost } from "./components/Toast";
import { rollup, rollupThroughput, rollupTraffic } from "./model/counters";
import {
  applyOverlay, binCongestion, binErrors, binThroughput, binUtilisation,
  type BinOf,
} from "./cy/overlay";
import {
  MASKS, countersFor, lookbackOf, maskFor, type MaskValue, type ViewState,
} from "./view/mask";
import { parseView, toSearch as viewSearch } from "./view/url";

export type Selection = SelectedItem[]; // ordered oldest→newest

export default function App() {
  const { time } = useTime();
  const at = useInstant();
  const compare = time.mode === "compare";

  // Null until /fabrics resolves. Everything below keys on it, so nothing may
  // fetch before it is known.
  const { fabric, fabrics, select, loading: fabricLoading, error: fabricError } = useFabric();
  const ready = fabric !== null;

  // One graph, two producers. Both hooks run unconditionally and `enabled` 
  // decides which one actually fetches.
  const topology = useTopology(fabric ?? "", at, ready && !compare);
  const diff = useDiff(
    fabric ?? "",
    ready && compare ? time.since : null,
    compare ? time.until : null,
  );
  const active = compare ? diff : topology;
  usePollNotice(topology, ready && !compare && at === null);

  // What the colour means, plus the counter window and selection behind it.
  // Read from the URL once at startup, exactly like TimeState: after that the
  // URL follows the state rather than the other way round.
  const [view, setView] = useState<ViewState>(() =>
    typeof window === "undefined" ? parseView("") : parseView(window.location.search));

  useEffect(() => {
    const search = viewSearch(view, window.location.search);
    window.history.replaceState(null, "", `${window.location.pathname}${search}`);
  }, [view]);

  // The mask the graph is actually drawing, which is not always the one in
  // state: a ?mask=errors link opened in compare has to render changes.
  const mask = maskFor(time.mode, view.mask);
  const overlay = MASKS[mask].overlay;
  const counters = countersFor({ ...view, mask });

  // The counter window is a trailing lookback off whatever instant is on
  // screen, not a second pair of drag handles. In live mode `at` is null and
  // the server fills `until` in as now; pinned, it ends at the pinned instant.
  const until = at;
  const since = useMemo(() => {
    const end = at ? Date.parse(at) : Date.now();
    return new Date(end - lookbackOf(view) * 1000).toISOString();
  }, [at, view]);

  const deltas = useErrorDeltas(
    fabric ?? "", since, until,
    ready && (overlay === "errors" || overlay === "congestion"),
  );

  // Its own query on its own clock. `until` is the pinned instant or null, so
  // point mode reads the rate as it was then and stops polling.
  //
  // One query for both traffic masks: utilisation and throughput are two
  // readings of the same per-port rates, so switching between them is a
  // re-rollup in the browser and not a refetch.
  const showsTraffic = overlay === "utilisation" || overlay === "throughput";
  const traffic = useTraffic(fabric ?? "", until, ready && showsTraffic);

  const [selection, setSelection] = useState<SelectedItem[]>([]);
  const [filters, setFilters] = useState<Filters>({
    dimHealthy: false,
    hideCAs: false,
    highlightChanges: false,
  });
  const [panelsOpen, setPanelsOpen] = useState(true);
  // What the timeline strip is covering.
  const [timeline, setTimeline] = useState<TimelineMetrics>({ h: 0, w: 0, open: false });

  const onTimelineMetrics = useCallback((m: TimelineMetrics) => {
    setTimeline((prev) =>
      prev.h === m.h && prev.w === m.w && prev.open === m.open ? prev : m);
  }, []);
  const cyRef = useRef<Core | null>(null);
  // Bumped by GraphView every time elements land in the core.
  const [graphGen, setGraphGen] = useState(0);
  // Layouts are keyed by node GUID, which says nothing about which fabric those
  // GUIDs belong to - so the panel is scoped to the fabric rather than global.
  const layouts = useLayouts(fabric);

  // App owns what the graph draws; cards own what they display. Memoised on the
  // query results, which react-query keeps referentially stable.
  //
  // A mode switch is an ordinary model change and goes through reconcile like
  // any other, so positions, selection and collapsed groups survive it.
  const fresh = useMemo(() => {
    if (compare) return diff.data ? fromDiff(diff.data) : null;
    return topology.data ? fromTopology(topology.data) : null;
  }, [compare, diff.data, topology.data]);

  // Keep drawing the last model while the next one loads. Switching mode swaps
  // which query feeds the graph, and without this the app falls through to the
  // loading screen for one render -- unmounting GraphView and destroying the
  // Cytoscape core, taking positions and selection with it.
  //
  // Held per fabric: carrying one fabric's graph across a switch would draw it
  // under the other fabric's name until the new data landed.
  const lastModel = useRef<{ fabric: string; model: GraphModel } | null>(null);
  if (fresh && fabric) lastModel.current = { fabric, model: fresh };
  const model =
    fresh ?? (lastModel.current?.fabric === fabric ? lastModel.current.model : null);

  // Per-port rows to per-element values: a 5 s traffic refresh would otherwise 
  // rebuild the model and re-run the reconciler to deliver four numbers per element.
  //
  // Traffic normalises to utilisation inside its rollup rather than binning on
  // Gbps, because the divisor belongs to a link -- see model/counters.ts.
  const values = useMemo(
    () => (overlay === "utilisation"
      ? rollupTraffic(traffic.data, model, view.rollup)
      : overlay === "throughput"
      ? rollupThroughput(traffic.data, model, view.rollup)
      : rollup(deltas.data, model, new Set(counters), view.rollup)),
    [overlay, traffic.data, deltas.data, model, counters, view.rollup],
  );

  // The scale belongs to the mask, not to the overlay machinery: xmit_wait
  // spans eleven decades and has to bin on a span-normalised rate, or nearly
  // every link lands in the top bucket and the gradient says nothing.
  const span = deltas.data?.window.span_s ?? null;
  const binOf = useMemo<BinOf>(
    () => (overlay === "congestion" ? (v) => binCongestion(v, span)
      : overlay === "utilisation" ? binUtilisation
      : overlay === "throughput" ? binThroughput
      : binErrors),
    [overlay, span],
  );

  // Re-applied after every reconcile as well as on every value change: elements
  // the reconciler just added carry no overlay data, and a rebuilt core carries
  // none at all. `graphGen` is that signal.
  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    applyOverlay(cy, overlay ?? "none", overlay ? values : null, binOf);
  }, [overlay, values, binOf, graphGen]);

  // Native Cytoscape selection is the single source of truth; the panel mutates
  // it (never React state directly) and the ordered list follows via the
  // select / unselect events wired in GraphView. Traversal adds; a card's ✕ removes.
  const focusInGraph = (id: string) => {
    const cy = cyRef.current;
    if (!cy) return;
    const el = cy.getElementById(id);
    if (el.empty()) return;
    el.select();
    // Pan the target to the middle of the canvas, keeping the user's zoom.
    // Skipped when it isn't rendered (hidden CA, collapsed leaf, or an edge to
    // either) - the card still opens, but there'd be nothing to pan to.
    if (el.visible()) {
      cy.stop(); // drop an in-flight pan from a rapid previous traversal
      cy.animate({ center: { eles: el }, duration: 250, easing: "ease-in-out" });
    }
  };
  const deselectInGraph = (id: string) => cyRef.current?.getElementById(id).unselect();

  const addMaskToSelection = (key: string, value: MaskValue) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().forEach((el) => {
      const data = el.data();

      if (data.hasOwnProperty(key) && data[key] === value) {
        el.select();
      }
    });
  };

  const setSelectionToIds = (ids: string[]) => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.batch(() => {
      cy.elements().unselect();
      ids.forEach((id) => cy.getElementById(id).select());
    });
  };

  // A selection can outlive the elements it names when the model changes.
  useEffect(() => {
    if (model) return;
    setSelection([]);
  }, [model]);

  // Only surface a failure when there is nothing to show. React-query keeps the
  // last good data through a failed background refetch, and unmounting here
  // would destroy the Cytoscape core - so a single bad poll would blank the
  // graph and throw away every position, selection and collapsed group.
  if (fabricError && !ready) return <LoadFailed error={fabricError} />;
  if (!fabricLoading && !fabricError && fabrics.length === 0) return <NoFabricYet />;
  if (active.isError && !model) return <LoadFailed error={active.error} />;

  // Wait for saved layouts too, so the graph builds with the active one already
  // resolved instead of flashing an fcose pass first.
  if (!fabric || !model || !layouts.loaded) {
    return <div className="loading">Loading fabric…</div>;
  }

  return (
    <div className="app">
      <ToastHost />
      <Header
            model={model}
            fabric={fabric}
            fabrics={fabrics}
            onSelectFabric={select}
            view={view}
            setView={setView}
          />
      <div className="main">
        <div
          className={`graph-wrap${timeline.open ? "" : " timeline-collapsed"}`}
          style={{
            "--timeline-h": `${timeline.open ? timeline.h : 0}px`,
            "--timeline-w": `${timeline.open ? 0 : timeline.w}px`,
          } as React.CSSProperties}
        >
          <div className={`overlay-panels${panelsOpen ? "" : " collapsed"}`}>
            <button
              className="panels-toggle"
              title={panelsOpen ? "Hide panels" : "Show panels"}
              aria-label={panelsOpen ? "Hide panels" : "Show panels"}
              aria-expanded={panelsOpen}
              onClick={() => setPanelsOpen((v) => !v)}
            >
              {panelsOpen ? "‹" : "›"}
            </button>
            <div className="overlay-panels-scroll">
              <Toolbar
                filters={filters}
                model={model}
                setFilters={setFilters}
                addMaskToSelection={addMaskToSelection}
                view={{ ...view, mask }}
                setView={setView}
                values={values}
                deltas={deltas.data}
                traffic={traffic.data}
                countersLoading={
                  showsTraffic ? traffic.isFetching : deltas.isFetching
                }
              />
              <LayoutPanel layouts={layouts} model={model} cyRef={cyRef} />
            </div>
          </div>
          <GraphView
            onReconciled={() => setGraphGen((g) => g + 1)}
            model={model}
            filters={filters}
            setSelection={setSelection}
            cyRef={cyRef}
            savedPositions={layouts.activeLayout?.positions}
          />
          <Timeline fabric={fabric} model={model} onMetrics={onTimelineMetrics} />
        </div>
        {selection.length > 0 && (
          <DetailPanel
            fabric={fabric}
            model={model}
            countersWindow={lookbackOf(view)}
            // Only when the graph is coloured by it. The card's job here is to
            // explain the colour, and under any other mask it would be two
            // extra queries per open card for a number nobody is looking at.
            withTraffic={showsTraffic}
            selection={selection}
            onNavigate={focusInGraph}
            onDeselect={deselectInGraph}
            onSetSelection={setSelectionToIds}
          />
        )}
      </div>
    </div>
  );
}

/** A fabric with no snapshot at the requested instant is an empty state, not a
 *  failure  */
function LoadFailed({ error }: { error: unknown }) {
  const notFound = error instanceof ApiError && error.isNotFound;
  return (
    <div className="loading">
      {notFound ? (
        <>
          No fabric data at this point in time.
          <br />
          <span className="muted">{(error as ApiError).message}</span>
        </>
      ) : (
        <>
          Could not reach the API: {error instanceof Error ? error.message : String(error)}
          <br />
          <span className="muted">Start it with: infinimap-api</span>
        </>
      )}
    </div>
  );
}

/** `/fabrics` answered with an empty list: API and database are fine, but no
 *  collector has completed a sweep, so there is no fabric to draw. The fabrics
 *  query polls while the list is empty, so this screen replaces itself with
 *  the map once the first sweep lands. */
function NoFabricYet() {
  return (
    <div className="loading">
      No fabric recorded yet.
      <br />
      <span className="muted">
        The API and database are up, but no collector has completed a sweep.
        Run infinimap-collector on a fabric-attached node - this page will pick
        it up by itself.
      </span>
    </div>
  );
}
