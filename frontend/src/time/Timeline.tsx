// The time axis: where you are in history, and how you move.
//
// The arithmetic lives in ./window, ./changes and ./handles, which are pure. 
// What is left here is pointer handling and SVG.
//
// Two rules shape the whole thing:
//
//   * "Now" is the newest sweep, not the wall clock. Anchoring to Date.now()
//     would show days of emptiness on a fabric whose collector has stopped.
//   * Commit on release. Dragging updates local state only, so a sweep across
//     three weeks costs one /diff rather than one per frame.

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { useHead, useSnapshots } from "../api/queries";
import type { GraphModel } from "../model/graph";
import { useTime } from "./TimeContext";
import { targetable, toSweeps, stepTarget } from "./changes";
import { axisLabel, stamp } from "./format";
import { handlesOf, jumpTo, moveHandle, type Grip } from "./handles";
import { instantAt } from "./instant";
import { instantOf, type TimeState } from "./state";
import {
  atX, clampWin, COLUMN_PX, columns, defaultWindow, onHeadMove, pan, span,
  ticks, toX, zoom, type Win,
} from "./window";

/** Beyond this the response is the newest N rather than a sample of the window,
 *  so it cannot be drawn. See useSnapshots. */
const LIMIT = 2000;
const DEBOUNCE_MS = 300;

/**
 * How far past the window's right edge to ask.
 *
 * The edge is the head sweep itself, and `Win` is epoch milliseconds while sweep
 * stamps carry microseconds - so an `until` built from the edge lands a fraction
 * of a millisecond *before* the head, and `/snapshots` (`collected_at <= until`)
 * omits the one sweep the user is looking at. Without this there is no rightmost
 * density column and no head to snap to, which took live mode with it: with the
 * head absent from `targets`, nothing could ever land on it.
 */
const EDGE_PAD_MS = 1000;

const H = 64; // strip height
const AXIS_Y = 44; // baseline the columns sit on
const KNOB = 10; // drawn width of a handle
const HIT = 16; // its grab area, which is the only reason a 1px line is usable
const MINUTE = 60_000;

const PRESETS: { label: string; ms: number }[] = [
  { label: "1h", ms: 60 * MINUTE },
  { label: "6h", ms: 360 * MINUTE },
  { label: "24h", ms: 1440 * MINUTE },
  { label: "7d", ms: 7 * 1440 * MINUTE },
];

type Drag = { grip: Grip; t: number } | null;

/** Shown in the modes that are *about* an instant you chose, hidden in the one
 *  that is about following the head. */
const showsFor = (mode: TimeState["mode"]) => mode !== "live";

/** What the strip is currently covering, for the canvas chrome that has to keep
 *  out of its way. Open it is a full-width band and only its height matters;
 *  collapsed it is a single button in the bottom-right corner, and then it is
 *  the width that other corner chrome has to clear. */
export interface TimelineMetrics {
  h: number;
  w: number;
  open: boolean;
}

interface Props {
  fabric: string;
  model: GraphModel;
  /** See the measurement effect below for why this is reported, not assumed. */
  onMetrics?: (m: TimelineMetrics) => void;
}

export function Timeline({ fabric, model, onMetrics }: Props) {
  const { time, setTime } = useTime();
  const [open, setOpen] = useState(() => showsFor(time.mode));

  // The strip is an overlay, so nothing in the layout knows it is there.
  // Measured rather than hardcoded because the height moves: the note line comes
  // and goes, and collapsing swaps the whole thing for a single button.
  const rootRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    const el = rootRef.current;
    if (!el || !onMetrics) return;
    const report = () => onMetrics({ h: el.offsetHeight, w: el.offsetWidth, open });
    const ro = new ResizeObserver(report);
    ro.observe(el);
    report();
    return () => ro.disconnect();
  }, [open, onMetrics]);

  // The strip follows the mode. Live is "follow the newest sweep", where there
  // is nothing to scrub for and the header already reports the lag; point and
  // compare are both *about* an instant, so the thing that moves it belongs on
  // screen. A manual toggle sticks until the mode changes again.
  //
  // Keyed on the mode string rather than on `time`, or every committed drag
  // would re-open a strip the user had just collapsed. Collapsing also disables
  // `useSnapshots` below, so live mode costs no sweep index at all.
  useEffect(() => setOpen(showsFor(time.mode)), [time.mode]);

  // The head sweep, which is what this axis means by "now".
  //
  // `model.resolved` cannot answer it once a handle is pinned: db.resolve bounds
  // `latest_sweep_at` by the instant requested - it reports "is there anything
  // newer than what I gave you", not "what is the newest sweep" - so a pinned
  // point reports itself as the head. The axis then clamps to the pinned instant
  // and there is no way to pan, zoom or drag forward again; one wheel tick
  // teleports the view back days and strands the handle off-screen.
  //
  // So when pinned, ask for the head directly. In live mode (and a compare
  // ending in "now") the model already *is* the head, and the query is disabled
  // rather than paying for a second poll to learn what is on screen.
  const pinned = instantOf(time) !== null;
  const head = useHead(fabric, null, pinned);
  const resolved = (pinned ? head.data?.resolved : undefined) ?? model.resolved;
  const now = Date.parse(resolved.latest_sweep_at ?? resolved.collected_at);

  const [win, setWin] = useState<Win>(() => defaultWindow(time, now));
  const [drag, setDrag] = useState<Drag>(null);

  const { ref: stripRef, box: boxRef, width } = useStrip();

  // Keep the view unless the artifact would be off-screen, and let a window
  // parked at the head follow it rather than being re-derived. TimeState moves
  // from the Mode menu, a jump, and a pasted URL; each has to either preserve
  // where the user was looking or take them somewhere they can see the result.
  const prevNow = useRef(now);
  useEffect(() => {
    const prev = prevNow.current;
    prevNow.current = now;
    setWin((w) => onHeadMove(w, prev, now, time));
  }, [time, now]);

  // One request when a pan or zoom settles, rather than one per frame.
  const settled = useDebounced(win, DEBOUNCE_MS);
  const query = useSnapshots(
    fabric,
    instantAt(settled.from),
    instantAt(settled.to + EDGE_PAD_MS),
    LIMIT,
    open,
  );

  const rows = query.data?.snapshots;
  const truncated = (rows?.length ?? 0) >= LIMIT;
  // Memoised because a drag re-renders on every pointermove, and this is a map,
  // a filter and a sort over up to LIMIT rows.
  const sweeps = useMemo(() => (!rows || rows.length >= LIMIT ? [] : toSweeps(rows)), [rows]);
  // Everything the user can actually land on. Incomplete sweeps stay in
  // `sweeps` for the density columns - they are the honest record that
  // collection was struggling - but they are not places you can go.
  const targets = useMemo(() => targetable(sweeps), [sweeps]);

  const axis = useMemo(() => ticks(win, width), [win, width]);
  const cols = useMemo(() => columns(sweeps, win, width), [sweeps, win, width]);

  // ---- committing -------------------------------------------------------

  const commit = useCallback(
    (grip: Grip, t: number) => {
      const next = moveHandle(time, grip, t, targets, now);
      if (next) setTime(next);
    },
    [targets, now, time, setTime],
  );

  // Both directions up front: a jump that would not move is how the buttons
  // know to be disabled, which is the only honest way to say "there is no next
  // change among the sweeps we fetched".
  const jumps = useMemo(
    () => ({ 1: jumpTo(time, 1, targets, now), "-1": jumpTo(time, -1, targets, now) }),
    [time, targets, now],
  );

  const jump = (dir: 1 | -1) => {
    const next = jumps[dir];
    if (next) setTime(next);
  };

  const preset = useCallback(
    (ms: number) => {
      setWin(clampWin({ from: now - ms, to: now }, now));
      // "Last 24h" plainly means the comparison when you are comparing. In the
      // other modes there is no range, so it can only move the view.
      if (time.mode === "compare") {
        setTime({ mode: "compare", since: instantAt(now - ms), until: null });
      }
    },
    [now, time.mode, setTime],
  );

  // ---- pointer ----------------------------------------------------------

  const pointerT = (e: { clientX: number }) => {
    const box = boxRef.current?.getBoundingClientRect();
    if (!box || width <= 0) return now;
    return atX(e.clientX - box.left, win, width);
  };

  const onGripDown = (grip: Grip) => (e: React.PointerEvent) => {
    e.stopPropagation();
    (e.target as Element).setPointerCapture(e.pointerId);
    setDrag({ grip, t: pointerT(e) });
  };

  const onGripMove = (e: React.PointerEvent) => {
    if (!drag) return;
    setDrag({ ...drag, t: pointerT(e) });
  };

  const onGripUp = (e: React.PointerEvent) => {
    if (!drag) return;
    commit(drag.grip, pointerT(e));
    setDrag(null);
  };

  // Dragging empty track pans; a click without movement moves the nearest
  // handle there, which is the fast way to reposition without aiming.
  const panFrom = useRef<{ x: number; win: Win; moved: boolean } | null>(null);

  const onTrackDown = (e: React.PointerEvent) => {
    (e.currentTarget as Element).setPointerCapture(e.pointerId);
    panFrom.current = { x: e.clientX, win, moved: false };
  };

  const onTrackMove = (e: React.PointerEvent) => {
    const start = panFrom.current;
    if (!start || width <= 0) return;
    const dx = e.clientX - start.x;
    if (Math.abs(dx) > 3) start.moved = true;
    if (start.moved) setWin(clampWin(pan(start.win, -dx / width), now));
  };

  const onTrackUp = (e: React.PointerEvent) => {
    const start = panFrom.current;
    panFrom.current = null;
    if (!start || start.moved) return;
    const t = pointerT(e);
    const closest = handlesOf(time, now).reduce(
      (best, h) => (Math.abs(h.t - t) < Math.abs(best.t - t) ? h : best),
    );
    commit(closest.grip, t);
  };

  const onWheel = (e: React.WheelEvent) => {
    const box = boxRef.current?.getBoundingClientRect();
    if (!box || width <= 0) return;
    const fraction = (e.clientX - box.left) / width;
    setWin((w) => clampWin(zoom(w, e.deltaY > 0 ? 1.25 : 0.8, fraction), now));
  };

  const onGripKey = (grip: Grip, t: number) => (e: React.KeyboardEvent) => {
    const dir = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
    if (!dir) return;
    e.preventDefault();
    // Sweep by sweep where there are sweeps. Past the last one in either
    // direction there is nothing to step to, so nudge by a fraction of the span
    // - which at the right edge is the head, and so returns to live.
    const next = stepTarget(targets, t, dir);
    commit(grip, next?.at ?? t + dir * span(win) * 0.02);
  };

  // ---- render -----------------------------------------------------------

  if (!open) {
    return (
      <div className="timeline collapsed" ref={rootRef}>
        <button className="timeline-toggle" title="Show timeline" onClick={() => setOpen(true)}>
          ▴ Timeline
        </button>
      </div>
    );
  }

  const grips = handlesOf(time, now).map((h) =>
    drag?.grip === h.grip ? { ...h, t: drag.t } : h,
  );
  const band =
    grips.length === 2
      ? { x: toX(grips[0].t, win, width), w: toX(grips[1].t, win, width) - toX(grips[0].t, win, width) }
      : null;

  return (
    <div className="timeline" ref={rootRef}>
      <div className="timeline-bar">
        <button className="timeline-toggle" title="Hide timeline" onClick={() => setOpen(false)}>
          ▾
        </button>
        {PRESETS.map((p) => (
          <button key={p.label} onClick={() => preset(p.ms)}>
            {p.label}
          </button>
        ))}
        <span className="timeline-spacer" />
        <JumpButtons truncated={truncated} next={jumps[1]} prev={jumps["-1"]} onJump={jump} />
      </div>

      <svg
        ref={stripRef}
        className="timeline-svg"
        height={H}
        onPointerDown={onTrackDown}
        onPointerMove={onTrackMove}
        onPointerUp={onTrackUp}
        onWheel={onWheel}
      >
        {band && band.w > 0 && (
          <rect className="tl-band" x={band.x} y={4} width={band.w} height={AXIS_Y - 4} />
        )}

        {/* One element per 2px rather than per sweep: a week at a 5-minute
            cadence is ~2,000 sweeps against a strip 1,500px wide. */}
        {cols.map((c) => (
          <rect
            key={c.x}
            className={c.changed ? "tl-col changed" : "tl-col"}
            x={c.x}
            y={AXIS_Y - colHeight(c.count)}
            width={COLUMN_PX}
            height={colHeight(c.count)}
          />
        ))}

        <line className="tl-axis" x1={0} y1={AXIS_Y} x2={width} y2={AXIS_Y} />

        {axis.ticks.map((t) => (
          <g key={t.at}>
            <line className="tl-tick" x1={t.x} y1={AXIS_Y} x2={t.x} y2={AXIS_Y + 4} />
            <text className="tl-label" x={t.x + 3} y={AXIS_Y + 14}>
              {axisLabel(t.at, axis.step)}
            </text>
          </g>
        ))}

        {grips.map((h) => {
          const x = toX(h.t, win, width);
          // The head handle sits exactly on the right edge, where the SVG clips
          // half of it away and leaves a few pixels to grab. The line still
          // marks the true instant; only the knob and its hit area are held
          // inside the strip.
          const kx = Math.min(Math.max(x, HIT / 2), Math.max(width - HIT / 2, HIT / 2));
          return (
            <g
              key={h.grip}
              className={`tl-grip${drag?.grip === h.grip ? " dragging" : ""}`}
              tabIndex={0}
              role="slider"
              aria-label={h.grip}
              aria-valuemin={win.from}
              aria-valuemax={win.to}
              aria-valuenow={h.t}
              aria-valuetext={stamp(instantAt(h.t))}
              onPointerDown={onGripDown(h.grip)}
              onPointerMove={onGripMove}
              onPointerUp={onGripUp}
              onKeyDown={onGripKey(h.grip, h.t)}
            >
              <line x1={x} y1={0} x2={x} y2={AXIS_Y} />
              <rect x={kx - KNOB / 2} y={0} width={KNOB} height={12} rx={2} />
              {/* Invisible, and the only reason a 1px line is grabbable. */}
              <rect className="tl-hit" x={kx - HIT / 2} y={0} width={HIT} height={AXIS_Y} />
            </g>
          );
        })}
      </svg>

      <Note query={query} truncated={truncated} sweeps={sweeps.length} targets={targets.length}
            changed={sweeps.filter((s) => s.changed).length} />
    </div>
  );
}

// ---- helpers --------------------------------------------------------------

/** Log-ish, so a column holding one sweep is still visible next to one holding
 *  forty. */
const colHeight = (count: number) => Math.min(6 + Math.log2(count + 1) * 6, AXIS_Y - 6);

/**
 * What the strip is showing, or why it is showing nothing.
 *
 * A failed request and an empty window must never read the same way. The whole
 * value of /snapshots is that it tells a collector outage apart from a fabric
 * that did not move, and rendering an unreachable API as "no sweeps collected"
 * would throw that distinction away at the last step.
 */
function Note({
  query, truncated, sweeps, targets, changed,
}: {
  query: { isError: boolean; isLoading: boolean };
  truncated: boolean;
  sweeps: number;
  targets: number;
  changed: number;
}) {
  return (
    <div className="timeline-note muted">
      {query.isError ? (
        <>Could not load the sweep index — the API may be unreachable.</>
      ) : truncated ? (
        <>Too many sweeps to chart this span — zoom in for sweep detail.</>
      ) : query.isLoading ? (
        <>Loading sweeps…</>
      ) : sweeps === 0 ? (
        <>No sweeps collected in this range.</>
      ) : (
        <>
          {/* Counted over every sweep, matching the columns: a change recorded
              on an incomplete sweep is still a change that happened, and the
              incomplete count below is what explains why it has no handle stop. */}
          {sweeps} sweeps · {changed} with changes
          {sweeps > targets && <> · {sweeps - targets} incomplete</>}
        </>
      )}
    </div>
  );
}

function JumpButtons({
  truncated,
  next,
  prev,
  onJump,
}: {
  truncated: boolean;
  next: TimeState | null;
  prev: TimeState | null;
  onJump: (dir: 1 | -1) => void;
}) {
  // Only the sweeps fetched for this window can be searched, so say so rather
  // than appearing to have looked further than we did. Each button is disabled
  // when its jump would not move - past the last change, or in live mode, where
  // the anchor is the head and nothing follows it.
  const why = truncated ? "Zoom in to search for changes" : "No further change among the sweeps in view";
  return (
    <>
      <button
        disabled={truncated || !prev}
        title={truncated || !prev ? why : "Previous change"}
        onClick={() => onJump(-1)}
      >
        ‹ change
      </button>
      <button
        disabled={truncated || !next}
        title={truncated || !next ? why : "Next change"}
        onClick={() => onJump(1)}
      >
        change ›
      </button>
    </>
  );
}

/**
 * The strip element and its width, tracked so the time-to-pixel mapping stays
 * correct through a window resize or the detail panel opening.
 */
function useStrip() {
  const box = useRef<SVGSVGElement | null>(null);
  const observer = useRef<ResizeObserver | null>(null);
  const [width, setWidth] = useState(0);

  const ref = useCallback((node: SVGSVGElement | null) => {
    observer.current?.disconnect();
    observer.current = null;
    box.current = node;
    if (!node) return; // unmounted: keep the last width, nothing is drawing anyway

    // Measure immediately rather than waiting for the observer's first callback,
    // so the strip draws at the right width on the frame it appears.
    setWidth(node.getBoundingClientRect().width);
    const ro = new ResizeObserver(([entry]) => setWidth(entry.contentRect.width));
    ro.observe(node);
    observer.current = ro;
  }, []);

  return { ref, box, width };
}

function useDebounced<T>(value: T, ms: number): T {
  const [settled, setSettled] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setSettled(value), ms);
    return () => clearTimeout(timer);
  }, [value, ms]);
  return settled;
}
