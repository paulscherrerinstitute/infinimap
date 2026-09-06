// The gear popover.

import { useEffect, useMemo, useRef } from "react";
import { LIMITS, zoneOptions, type NodeSizes, type Settings } from "./defaults";
import { useSettings } from "./SettingsContext";
import { stamp } from "../time/format";

interface Props {
  onClose: () => void;
}

const SIZE_LABEL: Record<keyof NodeSizes, string> = {
  ca: "HCA / compute",
  switch: "Switch",
  router: "Router",
  smMaster: "SM master",
  smStandby: "SM standby",
  edge: "Link thickness",
};

const SIZE_ORDER: (keyof NodeSizes)[] =
  ["switch", "ca", "router", "smMaster", "smStandby", "edge"];

export function SettingsPanel({ onClose }: Props) {
  const { settings, update, reset } = useSettings();
  const ref = useRef<HTMLDivElement>(null);

  // Dismiss on outside click and on Escape.
  useEffect(() => {
    const onDown = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) onClose();
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    // Deferred: the click that OPENED this would otherwise close it in the
    // same tick, since it lands outside the element that does not exist yet.
    const id = window.setTimeout(() => document.addEventListener("mousedown", onDown), 0);
    document.addEventListener("keydown", onKey);
    return () => {
      window.clearTimeout(id);
      document.removeEventListener("mousedown", onDown);
      document.removeEventListener("keydown", onKey);
    };
  }, [onClose]);

  const zones = useMemo(() => zoneOptions(), []);

  const sample = stamp(new Date().toISOString(), settings.timezone);

  const setSize = (k: keyof NodeSizes, v: number) =>
    update({ size: { ...settings.size, [k]: v } });
  const setPoll = (k: keyof Settings["poll"], seconds: number) =>
    update({ poll: { ...settings.poll, [k]: Math.round(seconds * 1000) } });

  return (
    <div className="settings-pop" ref={ref} role="dialog" aria-label="Settings">
      <header className="settings-head">
        <strong>Settings</strong>
        <span className="muted">this browser only</span>
        <button className="settings-close" onClick={onClose} aria-label="Close">
          ✕
        </button>
      </header>

      <section className="settings-section">
        <h3>Time zone</h3>
        <select
          value={settings.timezone}
          onChange={(e) => update({ timezone: e.target.value })}
        >
          {zones.map((z) => (
            <option key={z} value={z}>{z}</option>
          ))}
        </select>
        <p className="settings-note muted">{sample}</p>
        <p className="settings-note muted">
          Display only. Links, queries and the stored history stay in UTC.
        </p>
      </section>

      <section className="settings-section">
        <h3>Navigation</h3>
        <Slider
          label="Scroll weight"
          value={settings.scrollWeight}
          {...LIMITS.scrollWeight}
          onChange={(v) => update({ scrollWeight: v })}
          hint="How far one wheel notch zooms."
        />
      </section>

      <section className="settings-section">
        <h3>Element size</h3>
        {SIZE_ORDER.map((k) => (
          <Slider
            key={k}
            label={SIZE_LABEL[k]}
            value={settings.size[k]}
            {...(k === "edge" ? LIMITS.edge : LIMITS.size)}
            onChange={(v) => setSize(k, v)}
          />
        ))}
        <p className="settings-note muted">
          Sets each element’s width; rectangular shapes keep their proportions.
        </p>
      </section>

      <section className="settings-section">
        <h3>Labels &amp; panels</h3>
        <Slider
          label="Hide labels below"
          value={settings.labelMinPx}
          {...LIMITS.labelMinPx}
          unit="px"
          onChange={(v) => update({ labelMinPx: v })}
          hint="0 always shows labels. Switches and SMs are always labelled."
        />
        <Slider
          label="Summarise at"
          value={settings.summaryThreshold}
          {...LIMITS.summaryThreshold}
          unit=" selected"
          onChange={(v) => update({ summaryThreshold: v })}
          hint="Above this many selected elements the panel aggregates instead of listing cards."
        />
      </section>

      <section className="settings-section">
        <h3>Refresh</h3>
        <Slider
          label="Topology"
          value={settings.poll.topologyMs / 1000}
          min={5} max={600} step={5} unit="s"
          onChange={(v) => setPoll("topologyMs", v)}
        />
        <Slider
          label="Counters"
          value={settings.poll.countersMs / 1000}
          min={10} max={600} step={5} unit="s"
          onChange={(v) => setPoll("countersMs", v)}
        />
        <Slider
          label="Traffic"
          value={settings.poll.trafficMs / 1000}
          min={2} max={300} step={1} unit="s"
          onChange={(v) => setPoll("trafficMs", v)}
        />
        <p className="settings-note muted">
          A change takes effect on the next poll. Historical views never poll
          at all.
        </p>
      </section>

      <footer className="settings-actions">
        <button onClick={reset}>Reset to defaults</button>
      </footer>
    </div>
  );
}

function Slider({
  label, value, min, max, step, unit = "", hint, onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit?: string;
  hint?: string;
  onChange: (v: number) => void;
}) {
  return (
    <label className="settings-slider" title={hint}>
      <span className="settings-slider-head">
        {label}
        <span className="stat-value">
          {Number.isInteger(value) ? value : value.toFixed(1)}{unit}
        </span>
      </span>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
      />
      {hint && <span className="settings-note muted">{hint}</span>}
    </label>
  );
}
