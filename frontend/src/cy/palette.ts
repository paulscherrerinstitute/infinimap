// Single source of truth for element colors - shared by the Cytoscape stylesheet
// and the Legend so they never drift.
import type { Change, Health, NodeKind, CounterName } from "../api/types";

export const HEALTH_COLOR: Record<Health, string> = {
  ok: "#3fb950",
  degraded: "#d29922",
  down: "#f85149",
  unknown: "#8b949e",
};

export const HEALTH_LABEL: Record<Health, string> = {
  ok: "OK",
  degraded: "Degraded",
  down: "Down",
  unknown: "Unknown",
};

export const NODE_SHAPE: Record<NodeKind, string> = {
  switch: "round-rectangle",
  ca: "ellipse",
  router: "diamond",
};

export const NODE_LABEL: Record<NodeKind, string> = {
  switch: "Switch",
  ca: "HCA / compute",
  router: "Router",
};

// Compare mode colours by change rather than health.
export const CHANGE_COLOR: Record<Change, string> = {
  added: "#3fb950",
  removed: "#f85149",
  modified: "#d29922",
  unchanged: "#484f58",
};

export const CHANGE_LABEL: Record<Change, string> = {
  added: "Added",
  removed: "Removed",
  modified: "Modified",
  unchanged: "Unchanged",
};

// ---- overlay ramps --------------------------------------------------------

export const ERROR_RAMP: readonly string[] = [
  "#30363d", // 0        nothing
  "#2d4a6b", // 1-9
  "#3f6ea8", // 10-99
  "#e3a008", // 100-999
  "#e35d05", // 1k-99k
  "#d1242f", // 100k+
];

export const ERROR_BIN_LABEL: readonly string[] = [
  "0", "1–9", "10–99", "100–999", "1k–99k", "100k+",
];

export const TRAFFIC_RAMP: readonly string[] = [
  "#30363d", // idle
  "#1f4d3d", // <20%
  "#2f7d5c", // 20-40%
  "#4fae6c", // 40-60%
  "#c9a227", // 60-80%
  "#d1242f", // 80%+  -- saturation is a fault condition, not a success
];


export const UNTRUSTED_COLOR = "#6e40c9";
export const UNTRUSTED_LABEL = "Not measured";

/** Counter names as an operator reads them. */
export const COUNTER_LABEL: Partial<Record<CounterName, string>> = {
  symbol_error: "Symbol errors",
  link_error_recovery: "Link error recovery",
  link_downed: "Link downed",
  rcv_errors: "Rcv errors",
  rcv_remote_phys_errors: "Rcv remote phys",
  local_link_integrity: "Local link integrity",
  excessive_buffer_overrun: "Excessive buffer overrun",
  xmit_discards: "Xmit discards",
  vl15_dropped: "VL15 dropped",
  qp1_dropped: "QP1 dropped",
  xmit_constraint_errors: "Xmit constraint",
  rcv_constraint_errors: "Rcv constraint",
  rcv_switch_relay_errors: "Rcv switch relay",
  xmit_wait: "Xmit wait",
};
