import { useMemo } from "react";
import type { SelectedItem } from "../../api/types";
import type { GraphModel } from "../../model/graph";
import { HealthPill } from "./HealthPill";
import { summarize, type Bucket } from "./Summarize";

interface Props {
  model: GraphModel;
  selection: SelectedItem[];
  onSetSelection: (ids: string[]) => void;
  onNavigate: (id: string) => void; // Add item to selection
  onShowAll: () => void; // Drop below the threshold and render cards anyway.
}

export function SummaryPanel({ model, selection, onSetSelection, onNavigate, onShowAll }: Props) {
  const s = useMemo(() => summarize(model, selection), [model, selection]);

  return (
    <div className="summary">
      <header className="summary-head">
        <strong>{s.total} selected</strong>
        <span className="muted">
          {s.nodeIds.length} nodes · {s.linkIds.length} links
          {s.unresolvedIds.length > 0 && ` · ${s.unresolvedIds.length} unresolved`}
        </span>
      </header>

      <Section title="Link health" buckets={s.linkHealth} onPick={onSetSelection} health />
      <Section title="Node health" buckets={s.nodeHealth} onPick={onSetSelection} health />

      {s.worstLinks.length > 0 && (
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
      )}

      <Section title="Node type" buckets={s.nodeTypes} onPick={onSetSelection} />
      {s.smRoles.length > 0 && <Section title="Subnet manager" buckets={s.smRoles} onPick={onSetSelection} />}

      <section className="summary-section">
        <h3>Ports, firmware, counters</h3>
        <p className="panel-hint">
          Needs a selection-summary endpoint to aggregate server-side. Open a
          single element to see its detail.
        </p>
      </section>

      <footer className="summary-actions">
        <button onClick={onShowAll}>Show all {s.total} cards</button>
      </footer>
    </div>
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