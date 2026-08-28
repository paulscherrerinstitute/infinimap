// Readable aliases over the generated schema.
//
// `schema.d.ts` is produced by `npm run gen:api` from ../openapi.json and is
// never edited by hand. This is the only file that knows that generated shape;
// everywhere else imports the names below.

import type { components } from "./schema";

type S = components["schemas"];

// ---- closed sets ----------------------------------------------------------

export type Health = S["NodeElement"]["health"];
export type NodeKind = S["NodeElement"]["type"];
export type SmRole = NonNullable<S["NodeElement"]["sm_role"]>;
export type Change = S["DiffNode"]["change"];

// ---- lean elements --------------------------------------------------------

export type NodeElement = S["NodeElement"];
export type LinkElement = S["LinkElement"];
export type SystemGroup = S["SystemGroup"];
export type Rate = S["Rate"];
export type Counts = S["Counts"];
export type Resolved = S["Resolved"];

// The generated type keys this by `string`, since JSON object keys always are.
export type HealthCounts = Partial<Record<Health, number>>;

// ---- detail ---------------------------------------------------------------

export type NodeDetail = S["NodeDetail"];
export type LinkDetail = S["LinkDetail"];
export type PortDetail = S["PortDetail"];
export type LinkEnd = S["LinkEnd"];
export type Peer = S["Peer"];
export type PortRate = S["PortRate"];

// ---- counters -------------------------------------------------------------

export type ErrorDeltas = Omit<S["ErrorDeltas"], "ports"> & { ports: PortErrorDelta[] };

/**
 * One port's deltas, with the flag fields corrected to optional.
 *
 * The counters route serialises with `exclude_defaults`, so a false flag is
 * absent from the JSON -- but openapi-typescript emits anything carrying a
 * schema `default` as non-optional. Absent means false.
 */
type Flags = "no_data" | "reset" | "partial";
export type PortErrorDelta =
  Omit<S["PortErrorDelta"], Flags> & Partial<Pick<S["PortErrorDelta"], Flags>>;
export type CounterWindow = S["CounterWindow"];
export type CounterCoverage = S["CounterCoverage"];

/** The counter columns, read off the `counters` field rather than declared
 *  here, so adding one breaks every switch on it at build time. */
export type CounterName = S["ErrorDeltas"]["counters"][number];

export type TrafficRates = Omit<S["TrafficRates"], "ports"> & { ports: PortTraffic[] };

/**
 * One port's rates, with every defaulted field corrected to optional.
 *
 * The same `exclude_defaults` fix as `PortErrorDelta`, but wider: the traffic
 * route omits the rates too, so a row on a quiet fabric is `{node, port}` and
 * nothing else.
 */
type TrafficOptional = "tx" | "rx" | "txp" | "rxp" | "no_data" | "reset";
export type PortTraffic =
  Omit<S["PortTraffic"], TrafficOptional>
  & Partial<Pick<S["PortTraffic"], TrafficOptional>>;
export type TrafficCoverage = S["TrafficCoverage"];

// ---- top level ------------------------------------------------------------

export type Topology = S["Topology"];
export type Diff = S["Diff"];
export type DiffNode = S["DiffNode"];
export type DiffLink = S["DiffLink"];
export type Head = S["Head"];
export type Events = S["Events"];
export type Event = S["Event"];
export type FabricRow = S["FabricRow"];
export type Snapshots = S["Snapshots"];
export type SnapshotRow = S["SnapshotRow"];

// ---- app-level ------------------------------------------------------------

// What the graph reports as selected. Not part of the API contract.
export type SelectedItem = { kind: "node" | "link"; id: string };

// An instant to view the fabric at, as an ISO-8601 string, or null for "now".
export type Instant = string | null;
