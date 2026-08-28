// What changed about one element, from the model alone.

import type { LinkElement, NodeElement } from "../../api/types";
import type { GraphElement } from "../../model/graph";

export interface FieldDelta {
  key: string;
  before: unknown;
  after: unknown;
}

/** Identity, not shape: these cannot change without the element becoming a
 *  different element, since the ids are derived from them. */
const SKIP = new Set(["id", "source", "target"]);

const LABEL: Record<string, string> = {
  health: "Health",
  label: "Name",
  type: "Type",
  sm_role: "SM role",
  num_ports: "Ports",
  system_image_guid: "Chassis",
  source_port: "Source port",
  target_port: "Target port",
  speed: "Speed",
  width: "Width",
  rate_gbps: "Rate (Gb/s)",
};

function equal(a: unknown, b: unknown): boolean {
  if (a === b) return true;
  if (a == null || b == null) return a == null && b == null;
  if (typeof a !== "object" || typeof b !== "object") return false;
  const ak = Object.keys(a as object);
  const bk = Object.keys(b as object);
  if (ak.length !== bk.length) return false;
  return ak.every((k) =>
    equal((a as Record<string, unknown>)[k], (b as Record<string, unknown>)[k]),
  );
}

/** Empty when either side is absent - an added or removed element has no
 *  "before and after" to show, only a state. */
export function changedFields(
  before: Record<string, unknown> | undefined,
  after: Record<string, unknown> | undefined,
): FieldDelta[] {
  if (!before || !after) return [];
  const keys = new Set([...Object.keys(before), ...Object.keys(after)]);
  const out: FieldDelta[] = [];
  for (const key of keys) {
    if (SKIP.has(key)) continue;
    if (!equal(before[key], after[key])) {
      out.push({ key, before: before[key], after: after[key] });
    }
  }
  return out.sort((a, b) => a.key.localeCompare(b.key));
}

/** Rates arrive as {mask, label}; everything else is a scalar. */
function show(v: unknown): string {
  if (v == null) return "—";
  if (typeof v === "object" && "label" in (v as object)) {
    return String((v as { label: unknown }).label ?? "—");
  }
  return String(v);
}

export function Delta({ el }: { el: GraphElement<NodeElement | LinkElement> }) {
  const fields = changedFields(
    el.before as Record<string, unknown> | undefined,
    el.after as Record<string, unknown> | undefined,
  );

  return (
    <div className="delta">
      <div className={`delta-head change-${el.change}`}>
        {el.change === "added" && "Appeared in this window"}
        {el.change === "removed" && "Gone by the end of this window"}
        {el.change === "modified" && "Changed in this window"}
        {el.change === "unchanged" &&
          (el.churn > 0 ? "Back where it started" : "Unchanged in this window")}
      </div>

      {/* An element can flap and return byte-identical, which is the case a net
          diff cannot show and is usually the one worth looking at. */}
      {el.churn > 0 && (
        <div className="delta-churn">
          <span className="stat-value">{el.churn}</span> events touched this
          {el.change === "unchanged" ? " while it ended up unchanged" : ""}
        </div>
      )}

      {fields.length > 0 && (
        <table className="delta-table">
          <tbody>
            {fields.map((f) => (
              <tr key={f.key}>
                <th>{LABEL[f.key] ?? f.key}</th>
                <td className="delta-before">{show(f.before)}</td>
                <td className="delta-arrow">→</td>
                <td className="delta-after">{show(f.after)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {el.change === "modified" && fields.length === 0 && (
        <p className="muted">
          Classified as modified, but no lean field differs — the change is in
          detail this view does not carry.
        </p>
      )}
    </div>
  );
}
