import { useMemo } from "react";
import type {
  PortNote, SelectedItem, SelectionSummary, Tallied,
} from "../../api/types";
import { useSelectionSummary } from "../../api/queries";
import { useInstant } from "../../time/TimeContext";
import type { GraphModel } from "../../model/graph";
import { COUNTER_LABEL } from "../../cy/palette";
import type { CounterName } from "../../api/types";
import { HealthPill } from "./HealthPill";
import { summarize, type Bucket } from "./Summarize";

interface Props {
  fabric: string;
  model: GraphModel;
  selection: SelectedItem[];
  onSetSelection: (ids: string[]) => void;
  onNavigate: (id: string) => void; // Add item to selection
  onShowAll: () => void; // Drop below the threshold and render cards anyway.
  /** The window the overlay is using, so the summary and the colours agree. */
  countersWindow?: number;
  withTraffic?: boolean;
}

export function SummaryPanel({
  fabric, model, selection, onSetSelection, onNavigate, onShowAll,
  countersWindow, withTraffic,
}: Props) {
  const s = useMemo(() => summarize(model, selection), [model, selection]);
  const at = useInstant();

  const query = useSelectionSummary(
    fabric, s.nodeIds, s.linkIds, at, countersWindow, withTraffic,
  );
  const server = query.data;

  return (
    <div className="summary">
      <header className="summary-head">
        <strong>{s.total} selected</strong>
        <span className="muted">
          {s.nodeIds.length} nodes · {s.linkIds.length} links
          {s.unresolvedIds.length > 0 && ` · ${s.unresolvedIds.length} unresolved`}
          {query.isFetching && " · updating…"}
        </span>
      </header>

      <Section title="Link health" buckets={s.linkHealth} onPick={onSetSelection} health />
      <Section title="Node health" buckets={s.nodeHealth} onPick={onSetSelection} health />

      {server?.links.degraded.length ? (
        <section className="summary-section">
          <h3>Needs attention</h3>
          <ul className="worst-list">
            {server.links.degraded.map((l) => (
              <li key={l.id}>
                <button className="endpoint-link" title="Open this link" onClick={() => onNavigate(l.id)}>
                  {l.label}
                </button>
                <HealthPill health={l.health} />
                {l.reason.length > 0 && (
                  <div className="worst-reason muted">{l.reason.join(" · ")}</div>
                )}
              </li>
            ))}
          </ul>
        </section>
      ) : s.worstLinks.length > 0 ? (
        <section className="summary-section">
          <h3>Needs attention</h3>
          <ul className="worst-list">
            {s.worstLinks.map((l) => (
              <li key={l.id}>
                <button className="endpoint-link" title="Open this link" onClick={() => onNavigate(l.id)}>
                  {l.label}
                </button>
                <HealthPill health={l.health} />
              </li>
            ))}
          </ul>
        </section>
      ) : null}

      <Section title="Node type" buckets={s.nodeTypes} onPick={onSetSelection} />
      {s.smRoles.length > 0 && <Section title="Subnet manager" buckets={s.smRoles} onPick={onSetSelection} />}

      <ServerSections
        summary={server}
        loading={query.isLoading}
        error={query.error}
      />

      <footer className="summary-actions">
        <button onClick={onShowAll}>Show all {s.total} cards</button>
      </footer>
    </div>
  );
}

// ---- the server's half ----------------------------------------------------

function ServerSections({
  summary, loading, error,
}: {
  summary: SelectionSummary | undefined;
  loading: boolean;
  error: unknown;
}) {
  if (loading) {
    return (
      <section className="summary-section">
        <h3>Ports, firmware, counters</h3>
        <p className="panel-hint">Summarising…</p>
      </section>
    );
  }

  // A failed aggregate must not read as "nothing to report".
  if (error || !summary) {
    return (
      <section className="summary-section">
        <h3>Ports, firmware, counters</h3>
        <p className="panel-hint warn">
          Could not summarise this selection
          {error instanceof Error ? `: ${error.message}` : "."}
        </p>
      </section>
    );
  }

  const { requested, nodes, links, counters, traffic } = summary;
  const missing =
    requested.nodes_requested - requested.nodes_found +
    (requested.links_requested - requested.links_found);

  return (
    <>
      {missing > 0 && (
        <p className="toolbar-note warn">
          {missing} selected {missing === 1 ? "element does" : "elements do"} not
          exist at this instant and {missing === 1 ? "is" : "are"} not counted below.
        </p>
      )}

      {nodes.total > 0 && (
        <section className="summary-section">
          <h3>Ports</h3>
          <dl className="kv">
            <KV k="Recorded" v={nodes.ports_total} />
            <KV k="Active" v={nodes.ports_active} />
            {/* Not `recorded - active`. */}
            <KV k="Not active" v={nodes.ports_inactive} warn={nodes.ports_inactive > 0} />
            <KV k="Cabled" v={nodes.ports_linked} />
          </dl>
        </section>
      )}

      {nodes.firmware.length > 0 && (
        <Histogram
          title="Firmware"
          rows={nodes.firmware}
          // The whole reason this row exists: one version is a fleet, several
          // is a maintenance window somebody did not finish.
          note={nodes.firmware.length > 1
            ? `${nodes.firmware.length} versions across this selection`
            : undefined}
        />
      )}
      {nodes.models.length > 0 && <Histogram title="Model" rows={nodes.models} />}

      {links.total > 0 && (
        <>
          <Histogram title="Link speed" rows={links.speeds} />
          <Histogram title="Link width" rows={links.widths} />
          <section className="summary-section">
            <h3>Capacity</h3>
            <dl className="kv">
              <KV k="Selected links" v={links.total} />
              <KV k="Line rate" v={`${round(links.capacity_gbps)} Gbps`} />
              {links.unrated > 0 && (
                <KV k="No decodable rate" v={links.unrated} warn />
              )}
            </dl>
          </section>
        </>
      )}

      {traffic && (
        <section className="summary-section">
          <h3>Traffic</h3>
          <dl className="kv">
            <KV k="Transmit" v={`${round(traffic.tx_gbps)} Gbps`} />
            <KV k="Receive" v={`${round(traffic.rx_gbps)} Gbps`} />
            {traffic.peak_utilisation_pct !== null &&
              traffic.peak_utilisation_pct !== undefined && (
                <KV k="Busiest port" v={`${traffic.peak_utilisation_pct}% of its link`} />
              )}
            <KV k="Ports measured" v={`${traffic.ports_measured} of ${traffic.ports_measured + traffic.ports_no_data}`} />
          </dl>
          {/* Sums over the selected ports, not over links: a selection may
              hold one end of a link, the other, or both. */}
          <p className="panel-hint">
            Summed over the selected ports. A selection holding both ends of a
            link counts its bytes at each end.
          </p>
          <PortList rows={traffic.top_ports} unit="Gbps" />
        </section>
      )}

      {counters && (
        <section className="summary-section">
          <h3>Counters</h3>
          {counters.coverage.ports_measured === 0 ? (
            <p className="panel-hint warn">
              Nothing measurable over this window on the selected ports.
            </p>
          ) : (
            <>
              <p className="panel-hint">
                {counters.coverage.ports_measured} of{" "}
                {counters.coverage.ports_expected} ports over{" "}
                {counters.window.span_s
                  ? `${Math.round(counters.window.span_s)}s`
                  : "an unknown span"}
                {counters.coverage.ports_unusable > 0 &&
                  ` · ${counters.coverage.ports_unusable} without a usable delta`}
              </p>
              {Object.keys(counters.totals).length === 0 ? (
                <p className="panel-hint">No counter moved on this selection.</p>
              ) : (
                <dl className="kv">
                  {(Object.entries(counters.totals) as [CounterName, number][])
                    .sort((a, b) => b[1] - a[1])
                    .map(([name, value]) => (
                      <KV key={name} k={COUNTER_LABEL[name] ?? name} v={group(value)} warn />
                    ))}
                </dl>
              )}
              <PortList rows={counters.top_ports} unit="" />
            </>
          )}
        </section>
      )}
    </>
  );
}

// ---- shared bits ----------------------------------------------------------

/** Digit grouping that cannot be misread. */
const group = (n: number): string =>
  String(Math.round(n)).replace(/\B(?=(\d{3})+(?!\d))/g, " ");

const round = (n: number): string => (n >= 100 ? n.toFixed(0) : n.toFixed(2));

function KV({ k, v, warn }: { k: string; v: string | number; warn?: boolean }) {
  return (
    <div className="kv-row">
      <dt>{k}</dt>
      <dd className={warn ? "warn" : undefined}>{v}</dd>
    </div>
  );
}

/** A count histogram with a proportional bar, so the shape reads before the
 *  numbers do. */
function Histogram({
  title, rows, note,
}: {
  title: string;
  rows: Tallied[];
  note?: string;
}) {
  if (rows.length === 0) return null;
  const top = Math.max(...rows.map((r) => r.count), 1);
  return (
    <section className="summary-section">
      <h3>{title}</h3>
      {note && <p className="panel-hint">{note}</p>}
      <ul className="hist">
        {rows.map((r) => (
          <li className="hist-row" key={r.key}>
            <span className="hist-label" title={r.key}>{r.key}</span>
            <span className="hist-bar">
              <span className="hist-fill" style={{ width: `${(r.count / top) * 100}%` }} />
            </span>
            <span className="hist-count">{r.count}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

/** The ports that contributed most. */
function PortList({ rows, unit }: { rows: PortNote[]; unit: string }) {
  if (rows.length === 0) return null;
  return (
    <ul className="port-notes">
      {rows.map((p) => (
        <li key={`${p.node}:${p.port}`}>
          <span className="port-note-name" title={p.node}>
            {p.label}:{p.port}
          </span>
          <span className="stat-value">
            {unit === "Gbps" ? round(p.value) : group(p.value)}
            {unit && ` ${unit}`}
          </span>
        </li>
      ))}
    </ul>
  );
}

function Section({
  title,
  buckets,
  onPick,
  health,
  warn,
}: {
  title: string;
  buckets: Bucket[];
  onPick: (ids: string[]) => void;
  health?: boolean;
  warn?: boolean;
}) {
  if (buckets.length === 0) return null;
  return (
    <section className={`summary-section${warn ? " warn" : ""}`}>
      <h3>{title}</h3>
      <dl className="kv">
        {buckets.map((b) => (
          <div className="kv-row" key={b.key}>
            <dt>{health ? <HealthPill health={b.key} /> : b.key}</dt>
            <dd>
              <button className="bucket-btn" title="Select only these" onClick={() => onPick(b.ids)}>
                {b.ids.length}
              </button>
            </dd>
          </div>
        ))}
      </dl>
    </section>
  );
}
