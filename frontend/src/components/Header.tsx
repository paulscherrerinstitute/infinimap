import { useState } from "react";
import type { FabricRow } from "../api/types";
import type { GraphModel } from "../model/graph";
import { useTime } from "../time/TimeContext";
import { duration } from "../time/format";
import { useFormat } from "../settings/SettingsContext";
import { SettingsPanel } from "../settings/SettingsPanel";
import { freshness } from "../time/freshness";
import { defaultFor, MODE_LABEL, type Mode } from "../time/modes";
import { MASKS, masksFor, type Mask, type ViewState } from "../view/mask";

interface Props {
  model: GraphModel;
  fabric: string;
  fabrics: FabricRow[];
  onSelectFabric: (name: string) => void;
  view: ViewState;
  setView: (v: ViewState) => void;
}

export function Header({ model, fabric, fabrics, onSelectFabric, view, setView }: Props) {
  const { resolved } = model;
  const { time, setTime } = useTime();
  const { stamp } = useFormat();
  const [settingsOpen, setSettingsOpen] = useState(false);
  const { lagMs, incomplete } = freshness(resolved);

  // Sweeps are discrete, so the answer is almost never stamped exactly when it
  // was asked for. Live, that gap is how stale the graph is; pinned, it is how
  // far back the sweep that answered sits from the instant requested.
  const lag = lagMs >= 1000 ? duration(lagMs) : null;
  const isLive = time.mode === "live";
  const masks = masksFor(time.mode);
  // Read through the mode rather than trusted from state: a ?mask=errors link
  // opened in compare must render changes, and the picker must agree with what
  // the graph is actually doing.
  const mask = masks.includes(view.mask) ? view.mask : masks[0];

  return (
    <header className="app-header">
      <div className="app-brand">
        <img className="brand-icon" src="/icons/infinimap-icon.svg" alt="" />
        <img className="brand-wordmark" src="/icons/infinimap-wordmark.svg" alt="infinimap" />
      </div>

      {/* A view of time.mode rather than state of its own: when the scrubber
          lands, dragging a handle moves this, because both write TimeState. */}
      <label className="mode-picker">
        <span className="muted">Mode</span>
        <select
          value={time.mode}
          onChange={(e) => setTime(defaultFor(e.target.value as Mode, resolved))}
        >
          {(Object.keys(MODE_LABEL) as Mode[]).map((m) => (
            <option key={m} value={m}>
              {MODE_LABEL[m]}
            </option>
          ))}
        </select>
      </label>

      {masks.length > 1 && (
        <label className="mask-picker">
          <span className="muted">Mask</span>
          <select
            value={mask}
            onChange={(e) => setView({ ...view, mask: e.target.value as Mask })}
          >
            {masks.map((m) => (
              <option key={m} value={m}>
                {MASKS[m].label}
              </option>
            ))}
          </select>
        </label>
      )}

      <div className="provenance muted">
        {incomplete && (
          <span
            className="warn"
            title={
              resolved.latest_sweep_at
                ? `sweep ${stamp(resolved.latest_sweep_at)} did not complete`
                : "the newest sweep did not complete"
            }
          >
            ⚠ last sweep incomplete ·{" "}
          </span>
        )}

        <span className={`mode-badge mode-${time.mode}`}>
          <span className="mode-dot" />
          {MODE_LABEL[time.mode]}
        </span>

        {fabrics.length > 1 ? (
          <select
            className="fabric-picker"
            value={fabric}
            title="Switch fabric"
            onChange={(e) => onSelectFabric(e.target.value)}
          >
            {fabrics.map((f) => (
              <option key={f.fabric_id} value={f.name}>
                {f.name}
              </option>
            ))}
          </select>
        ) : (
          <span className="fabric-name">{fabric}</span>
        )}
        <span> · </span>

        <span title={`requested ${stamp(resolved.requested_at)}`}>
          {stamp(resolved.collected_at)}
        </span>
        {lag && (
          <span
            title={
              isLive
                ? "age of the sweep being shown"
                : "how far before the requested instant this sweep was taken"
            }
          >
            {" "}
            · {lag} {isLive ? "old" : "earlier"}
          </span>
        )}
      </div>
      <div className="settings-anchor">
        <button
          className="settings-gear"
          title="Settings"
          aria-label="Settings"
          aria-expanded={settingsOpen}
          onClick={() => setSettingsOpen((v) => !v)}
        >
          ⚙
        </button>
        {settingsOpen && <SettingsPanel onClose={() => setSettingsOpen(false)} />}
      </div>
    </header>
  );
}
