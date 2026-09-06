import { useState } from "react";
import type { Core } from "cytoscape";
import { applyPositions, capturePositions } from "../cy/positions";
import type { SavedLayout } from "../layouts/store";
import type { useLayouts } from "../layouts/useLayouts";
import type { GraphModel } from "../model/graph";
import { useFormat } from "../settings/SettingsContext";
import { usePulse } from "../usePulse";
import { Toast } from "./Toast";

// Green wash confirming a write landed - the palette's "ok" green (#3fb950),
// inlined as rgba since it has to fade its alpha out.
const SAVED_PULSE: Keyframe[] = [
  { backgroundColor: "rgba(63, 185, 80, 0.45)" },
  { backgroundColor: "rgba(63, 185, 80, 0)" },
];

interface Props {
  layouts: ReturnType<typeof useLayouts>;
  model: GraphModel;
  cyRef: React.MutableRefObject<Core | null>;
}

// Every mode can capture, compare included. Positions are keyed by node id and
// replayed against the nodes present now, so an entry for an element that only
// exists inside some diff is never read -- and on revisiting that window it
// pins the ghosts rather than letting fcose re-solve them.
export function LayoutPanel({ layouts, model, cyRef }: Props) {
  const { layouts: saved, activeId, saveNew, update, remove, selectActive } = layouts;
  const authoredAt = model.resolved.collected_at;

  // Row to flash green once a write has actually landed. `n` makes each save a
  // distinct value, so saving the same row twice re-fires the pulse.
  const [justSaved, setJustSaved] = useState<{ id: string; n: number } | null>(null);
  const markSaved = (id: string) => setJustSaved((p) => ({ id, n: (p?.n ?? 0) + 1 }));

  const saveCurrent = async () => {
    const cy = cyRef.current;
    if (!cy) {
      console.error("No Cytoscape instance available");
      return;
    }
    const name = window.prompt("Name this layout:", `Layout ${saved.length + 1}`)?.trim();
    if (!name) return;
    const layout = await saveNew(name, capturePositions(cy), authoredAt);
    markSaved(layout.id);
  };

  const apply = (l: SavedLayout) => {
    const cy = cyRef.current;
    if (!cy) {
      console.error("No Cytoscape instance available");
      return;
    }
    applyPositions(cy, l.positions);
    selectActive(l.id);
  };

  // pulse + toast to confirm the overwrite landed
  const overwrite = async (l: SavedLayout) => {
    const cy = cyRef.current;
    if (!cy) {
      console.error("No Cytoscape instance available");
      return;
    }
    await update(l.id, { positions: capturePositions(cy), savedAt: authoredAt });
    markSaved(l.id);
    Toast.ok(`Updated layout "${l.name}"`, undefined, 1500);
  };

  const rename = async (l: SavedLayout) => {
    const name = window.prompt("Rename layout:", l.name)?.trim();
    if (!name || name === l.name) return;
    await update(l.id, { name });
    markSaved(l.id);
    Toast.ok(`Renamed layout`, `Layout "${l.name}" renamed to "${name}".`);
  };

  return (
    <div className="layouts">
      <div className="layouts-head">
        <span>Layouts</span>
        <button onClick={saveCurrent}>+ Save current</button>
      </div>

      {saved.length === 0 ? (
        <p className="layouts-empty muted">
          Drag nodes to arrange them, then save the arrangement.
        </p>
      ) : (
        <ul className="layouts-list">
          {saved.map((l) => (
            <LayoutRow
              key={l.id}
              layout={l}
              active={l.id === activeId}
              savedNonce={justSaved?.id === l.id ? justSaved.n : undefined}
              onApply={() => apply(l)}
              onOverwrite={() => overwrite(l)}
              onRename={() => rename(l)}
              onRemove={() => remove(l.id)}
            />
          ))}
        </ul>
      )}
    </div>
  );
}

function LayoutRow({
  layout,
  active,
  savedNonce,
  onApply,
  onOverwrite,
  onRename,
  onRemove,
}: {
  layout: SavedLayout;
  active: boolean;
  // Bumped each time this row is written to; undefined when it isn't the row
  // that was just saved.
  savedNonce?: number;
  onApply: () => void;
  onOverwrite: () => void;
  onRename: () => void;
  onRemove: () => void;
}) {
  const ref = usePulse<HTMLLIElement>(savedNonce, SAVED_PULSE);
  const { stamp } = useFormat();

  return (
    <li ref={ref} className={`layout-item${active ? " active" : ""}`}>
      <button className="layout-name" title="Apply" onClick={onApply}>
        {active && <span className="dot" />}
        {layout.name}
        {layout.savedAt && (
          <span className="layout-provenance muted">{stamp(layout.savedAt)}</span>
        )}
      </button>
      <div className="layout-actions">
        <button title="Overwrite with current positions" onClick={onOverwrite}>
          Update
        </button>
        <button title="Rename" onClick={onRename}>
          ✎
        </button>
        <button title="Delete" onClick={onRemove}>
          ✕
        </button>
      </div>
    </li>
  );
}
