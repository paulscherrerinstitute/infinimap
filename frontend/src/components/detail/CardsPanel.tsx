import { useEffect, useRef, useState } from "react";
import type { LinkDetail, NodeDetail, Rate, SelectedItem, PortDetail } from "../../api/types";
import { ApiError } from "../../api/client";
import { useLinkDetail, useNodeDetail } from "../../api/queries";
import { useInstant } from "../../time/TimeContext";
import type { GraphModel } from "../../model/graph";
import { usePulse } from "../../usePulse";
import { CHANGE_LABEL } from "../../cy/palette";
import { Delta } from "./Delta";
import { HealthPill } from "./HealthPill";
import { COUNTER_LABEL, countersMoved, errorCount, waitTicks } from "./Summarize";

// Accent ring, expanding outward. Safe to overflow the card: .panel's 12px of
// padding gives it room. Pulses box-shadow so .sel-card.expanded keeps its
// border-color.
const FOCUS_PULSE: Keyframe[] = [
  { boxShadow: "0 0 0 0 rgba(88, 166, 255, 0.55)" },
  { boxShadow: "0 0 0 6px rgba(88, 166, 255, 0)" },
];

interface Props {
  fabric: string;
  model: GraphModel;
  selection: SelectedItem[];
  onNavigate: (id: string) => void;
  onDeselect: (id: string) => void;
  /** Seconds of counter history, matching the overlay's window. */
  countersWindow?: number;
  withTraffic?: boolean;
}

export function CardsPanel({
  fabric, model, selection, onNavigate, onDeselect, countersWindow, withTraffic,
}: Props) {
  // Per-card expand state -- several cards may be expanded at once. Appending
  // one expands it and collapses only the previously-last card; a batch add
  // leaves existing state untouched; a traversal collapses the card it started
  // from (see navigate). Manual header clicks toggle freely.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const prevIds = useRef<string[]>([]);

  // Traversal target to scroll to and pulse. `n` makes each click a distinct
  // value, so navigating to the same card twice still re-fires the card's
  // focus effect.
  const [focus, setFocus] = useState<{ id: string; n: number } | null>(null);
  // Set when a selection add originates from navigate() below rather than from
  // a graph tap; consumed by the diff effect.
  const pendingNavigate = useRef<string | null>(null);

  // card expand/contract mechanics
  useEffect(() => {
    const prev = prevIds.current;
    const ids = selection.map((s) => s.id);
    const added = ids.filter((id) => !prev.includes(id));
    const removed = prev.filter((id) => !ids.includes(id));

    // Consume unconditionally: any run of this effect invalidates a pending id,
    // so a stale one can never be matched on a later add.
    const pending = pendingNavigate.current;
    pendingNavigate.current = null;
    const ownedByNavigate = pending !== null && added.length === 1 && added[0] === pending;

    if (removed.length > 0 || added.length === 1) {
      const prevLast = prev[prev.length - 1];
      setExpanded((cur) => {
        const next = new Set(cur);
        removed.forEach((id) => next.delete(id));
        if (added.length === 1) {
          // A plain graph tap replaces the selection, so `removed` already
          // collapsed the old cards.
          if (!ownedByNavigate && prevLast !== undefined) next.delete(prevLast);
          next.add(added[0]); // expand the new newest
        }
        return next;
      });
    }
    // added.length > 1 (batch): leave existing expand state as-is, new cards stay collapsed.

    prevIds.current = ids;
  }, [selection]);

  const toggle = (id: string) =>
    setExpanded((cur) => {
      const next = new Set(cur);
      next.has(id) ? next.delete(id) : next.add(id);
      return next;
    });

  // Traverse from one card to another: collapse the source, expand the target,
  // then scroll/pulse it. Expand synchronously rather than in an effect so all
  // three updates (including the selection add, which cytoscape emits
  // synchronously) batch into one commit -- the target then renders expanded
  // and focused together, and the scroll runs against the final layout.
  const navigate = (from: string, to: string) => {
    pendingNavigate.current = to;
    onNavigate(to); // select in cy; silently no-ops if already selected
    setExpanded((cur) => {
      const next = new Set(cur);
      if (from !== to) next.delete(from);
      next.add(to);
      return next;
    });
    setFocus((f) => ({ id: to, n: (f?.n ?? 0) + 1 }));
  };

  return (
    <>
      {selection.map((item) => (
        <SelectionCard
          key={item.id}
          item={item}
          fabric={fabric}
          model={model}
          expanded={expanded.has(item.id)}
          focusNonce={focus?.id === item.id ? focus.n : undefined}
          onToggle={() => toggle(item.id)}
          onClose={() => onDeselect(item.id)}
          onNavigate={(target) => navigate(item.id, target)}
          countersWindow={countersWindow}
          withTraffic={withTraffic}
        />
      ))}
    </>
  );
}


// ---- card -----------------------------------------------------------------

function SelectionCard({
  item,
  fabric,
  model,
  expanded,
  focusNonce,
  onToggle,
  onClose,
  onNavigate,
  countersWindow,
  withTraffic,
}: {
  item: SelectedItem;
  fabric: string;
  model: GraphModel;
  expanded: boolean;
  countersWindow?: number;
  withTraffic?: boolean;
  // Bumped each time this card is the target of a traversal; undefined when it
  // isn't the focused card.
  focusNonce?: number;
  onToggle: () => void;
  onClose: () => void;
  onNavigate: (id: string) => void;
}) {
  const ref = usePulse<HTMLElement>(focusNonce, FOCUS_PULSE);
  const at = useInstant();

  const isNode = item.kind === "node";
  const element = isNode
    ? model.nodes.find((n) => n.el.id === item.id)
    : model.links.find((l) => l.el.id === item.id);

  // A removed element does not exist at `until` - that is what removed means -
  // so asking for its detail there 404s. It has to be read at the other end of
  // the window instead, or every card opened on a removal shows an error.
  const detailAt =
    element?.change === "removed" && model.since ? model.since.collected_at : at;

  // Only an expanded card fetches. Both hooks are called unconditionally (rules
  // of hooks); `enabled` decides which one actually runs.
  const nodeQ = useNodeDetail(fabric, isNode ? item.id : null, detailAt,
                              expanded, countersWindow, withTraffic);
  const linkQ = useLinkDetail(fabric, isNode ? null : item.id, detailAt,
                              expanded, countersWindow, withTraffic);
  const query = isNode ? nodeQ : linkQ;

  // Bring a freshly-expanded card into view (e.g. a traversal card appended at
  // the bottom of a long list).
  useEffect(() => {
    if (expanded) ref.current?.scrollIntoView({ block: "nearest" });
  }, [expanded, ref]);

  // Traversal target: scroll to it (usePulse flashes it).
  useEffect(() => {
    if (focusNonce === undefined) return;
    ref.current?.scrollIntoView({ block: "nearest" });
  }, [focusNonce, ref]);

  // The header renders from the lean model, so a collapsed card needs no
  // request at all - which is what makes a large selection cheap.
  const lean = element?.el;
  const compare = model.mode === "compare";

  const title = isNode
    ? (lean && "label" in lean ? lean.label : item.id)
    : "Link";

  // In compare mode the change is the headline - health is not the channel
  // being read there, and a collapsed card should still say what happened.
  const badge = compare
    ? element
      ? (
        <div>
          <span className={`tag change-${element.change}`}>{CHANGE_LABEL[element.change]}</span>
          {element.churn > 0 ? <span className="tag">{element.churn} events</span> : null}
        </div>
      )
      : null
    : isNode
      ? lean && "type" in lean
        ? (
          <div>
            <span className="tag">{lean.type}</span>
            {lean.sm_role ? <span className="tag">SM {lean.sm_role}</span> : null}
          </div>
        )
        : null
      : lean
        ? <HealthPill health={lean.health} />
        : null;

  return (
    <section ref={ref} className={`sel-card${expanded ? " expanded" : ""}`}>
      <header className="sel-card-head" onClick={onToggle}>
        <span className="chevron">{expanded ? "▾" : "▸"}</span>
        <span className="sel-card-title">{title}</span>
        {badge}
        <button
          className="sel-card-close"
          title="Deselect"
          aria-label="Deselect"
          onClick={(e) => {
            e.stopPropagation();
            onClose();
          }}
        >
          ✕
        </button>
      </header>
      {expanded && (
        <div className="sel-card-body">
          {compare && element && <Delta el={element} />}
          {query.isPending ? (
            <p className="panel-hint">Loading…</p>
          ) : query.isError ? (
            <Failed id={item.id} error={query.error} />
          ) : isNode ? (
            <NodeBody node={nodeQ.data as NodeDetail} onNavigate={onNavigate} />
          ) : (
            <LinkBody link={linkQ.data as LinkDetail} onNavigate={onNavigate} />
          )}
        </div>
      )}
    </section>
  );
}

// ---- node ----------------------------------------------------------------

function NodeBody({ node, onNavigate }: { node: NodeDetail; onNavigate: (id: string) => void }) {
  const ports = [...(node.ports ?? [])].sort((a, b) => a.port_num - b.port_num);
  // The column only exists when traffic was actually requested.
  const anyTraffic = ports.some((p) => p.traffic);
  return (
    <>
      <dl className="kv">
        <Row k="GUID" v={node.guid} mono />
        <Row k="Firmware" v={node.fw_version ?? null} />
        <Row k="Ports" v={`${ports.filter(p => p.state === "active").length}${node.num_ports ? ` / ${node.num_ports}` : ""}`} />
        <Row k="Device | Vendor" v={`${node.model ?? "?"} | ${node.vendor ?? "?"}`} />
      </dl>

      <h3>Ports</h3>
      <table className="ports">
        <thead>
          <tr>
            <th>#</th>
            <th>State</th>
            <th>Sp/W</th>
            <th className="errors">Errors</th>
            {anyTraffic && <th className="errors">Gb/s</th>}
            <th>Peer</th>
          </tr>
        </thead>
        <tbody>
          {ports.map((p) => (
            <tr
              key={p.port_num}
              className={p.peer ? "port-row linked" : "port-row"}
              title={p.peer ? "Go to link" : undefined}
              onClick={p.peer ? () => onNavigate(p.peer!.link_id) : undefined}
            >
              <td>{p.port_num}</td>
              <td className={p.state === "active" ? "" : "warn"}>{p.state ?? "—"}</td>
              <td>
                {rate(p.speed_active)}/{rate(p.width_active)}
              </td>
              <ErrorCell port={p} />
              {anyTraffic && <TrafficCell port={p} />}
              <td className="muted">{p.peer?.name ?? "-"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </>
  );
}


// ---- counters --------------------------------------------------------------

/**
 * Digit grouping that cannot be misread. NOT `toLocaleString()`: in a locale
 * that groups with dots, a delta of 1234 renders as "1.234" and reads as
 * "about one". The SI separator means the same thing in every locale.
 */
const group = (n: number): string =>
  String(n).replace(/\B(?=(\d{3})+(?!\d))/g, " ");

/**
 * A rate, without rounding a small one away to nothing: `Math.round` turns a
 * fraction of a tick per second into "0/s", which reads as "never waited" --
 * the opposite of what was measured. Below one per second says so.
 */
const perSecond = (r: number): string =>
  r >= 1 ? `${group(Math.round(r))}/s` : r > 0 ? "<1/s" : "0/s";

/**
 * One port's error total, with the counters behind it on hover.
 *
 * Three states, and keeping them apart is the whole point of the cell:
 *
 *   no delta   the window could not be measured -- no baseline, or a counter
 *              went backwards. Renders as "?" and NOT as 0.
 *   zero       measured, nothing moved.
 *   nonzero    the number, plus which counters made it, since "14 errors" is
 *              not actionable and "14 symbol errors" is.
 */
function ErrorCell({ port }: { port: PortDetail }) {
  const moved = countersMoved(port);

  if (moved === null) {
    return (
      <td className="errors muted" title="No delta for this window — the port was not polled, or a counter was cleared.">
        ?
      </td>
    );
  }

  const total = errorCount(port);
  const wait = waitTicks(port);
  const detail = moved.length
    ? moved.map(([c, v]) => `${COUNTER_LABEL[c] ?? c}: ${group(v)}`).join("\n")
    : "Measured over the window; nothing moved.";

  return (
    <td className={`errors ${total ? "warn" : "muted"}`} title={detail}>
      {total ? group(total) : "0"}
      {/* show xmit_wait, even if not explicitly in the 'errors' mask */}
      {!total && wait > 0 && <span className="muted"> · wait</span>}
    </td>
  );
}

/** One port's throughput, with the direction split out on hover. */
function TrafficCell({ port }: { port: PortDetail }) {
  const t = port.traffic;
  if (!t) {
    // Requested and not returned: no pair of readings, or a wrapped counter.
    // Not idle -- unknown, and the dash has to say so on hover.
    return (
      <td className="errors muted" title="No rate over this window — unpolled, or a byte counter wrapped.">
        —
      </td>
    );
  }
  const busiest = Math.max(t.tx, t.rx);
  const pct = port.rate_gbps ? (busiest / port.rate_gbps) * 100 : null;
  return (
    <td
      className={pct != null && pct >= 60 ? "errors warn" : "errors muted"}
      title={`tx ${t.tx.toFixed(2)} · rx ${t.rx.toFixed(2)} Gb/s`
             + (pct != null ? ` · ${pct.toFixed(1)}% of line rate` : "")}
    >
      {busiest.toFixed(2)}
    </td>
  );
}

// ---- link ----------------------------------------------------------------

function LinkBody({ link, onNavigate }: { link: LinkDetail; onNavigate: (id: string) => void }) {
  return (
    <>
      {link.reason && link.reason.length > 0 && (
        <div className="reason">
          <strong>Checks:</strong> {link.reason.join(", ")}
        </div>
      )}

      <Endpoint label="A" end={link.a} onClick={() => onNavigate(link.a.guid)} />
      <Endpoint label="B" end={link.b} onClick={() => onNavigate(link.b.guid)} />
    </>
  );
}

// Both ends arrive expanded in the link response, so a link card needs no
// follow-up fetch and no cross-reference into node detail.
function Endpoint({
  label,
  end,
  onClick,
}: {
  label: string;
  end: LinkDetail["a"];
  onClick: () => void;
}) {
  const port = end.port;
  return (
    <section className="endpoint">
      <h3>
        {label}:{" "}
        <button className="endpoint-link" title="Go to node" onClick={onClick}>
          {end.label ?? end.guid}
        </button>
      </h3>
      <dl className="kv">
        <Row k="Port" v={String(port.port_num)} />
        <Row k="State" v={port.state ?? null} />
        <Row k="Speed | Width" v={`${rate(port.speed_active)} | ${rate(port.width_active)}`} />
        <Row k="Rate" v={port.rate_gbps != null ? `${Math.round(port.rate_gbps)} Gb/s` : null} />
        <Row k="MTU" v={port.mtu_bytes != null ? String(port.mtu_bytes) : null} />
      </dl>
      <PortTraffic port={port} />
      <PortCounters port={port} />
    </section>
  );
}

/**
 * What this end of the link is actually carrying: the graph says which link is
 * busy, this says how busy and in which direction. Per end, because tx at one
 * end is rx at the other.
 *
 * Utilisation is recomputed here from `rate_gbps` rather than sent down, so the
 * percentage and the graph's bin come from the same two numbers.
 */
function PortTraffic({ port }: { port: PortDetail }) {
  const t = port.traffic;
  if (!t) return null;

  const pct = (v: number) =>
    port.rate_gbps ? `${((v / port.rate_gbps) * 100).toFixed(1)}%` : "—";
  // Mean packet size is a workload fingerprint no other counter gives,
  // and it is free here: bytes over packets, both already on the wire.
  const mean = (gbps: number, pps: number) =>
    pps > 0 ? `${Math.round((gbps * 1e9) / 8 / pps)} B` : "—";

  return (
    <dl className="kv counters">
      <div className="kv-row">
        <dt>Tx</dt>
        <dd>{t.tx.toFixed(2)} Gb/s <span className="muted">({pct(t.tx)})</span></dd>
      </div>
      <div className="kv-row">
        <dt>Rx</dt>
        <dd>{t.rx.toFixed(2)} Gb/s <span className="muted">({pct(t.rx)})</span></dd>
      </div>
      <div className="kv-row">
        <dt>Mean packet</dt>
        <dd className="muted">
          {mean(t.tx, t.txp)} tx · {mean(t.rx, t.rxp)} rx
        </dd>
      </div>
      <div className="kv-row">
        <dt>Averaged over</dt>
        <dd className="muted">{Math.round(t.span_s)}s</dd>
      </div>
    </dl>
  );
}

/**
 * What this end of the link accumulated, per counter: the graph says which link
 * is red, this says which counter made it red.
 */
function PortCounters({ port }: { port: PortDetail }) {
  const moved = countersMoved(port);
  if (moved === null) {
    return port.counters_at ? (
      <p className="toolbar-note muted">
        No delta over this window — unpolled, or a counter was cleared.
      </p>
    ) : null;
  }
  if (moved.length === 0) {
    return <p className="toolbar-note muted">No counters moved over this window.</p>;
  }
  const span = port.counters_span_s ?? null;
  return (
    <dl className="kv counters">
      {moved.map(([c, v]) => (
        <div className="kv-row" key={c}>
          <dt>{COUNTER_LABEL[c] ?? c}</dt>
          <dd className={c === "xmit_wait" ? "muted" : "warn"}>
            {group(v)}
            {/* xmit_wait alone gets a rate beside it, and it is not a nicety.
                Twenty-seven billion ticks is either routine or alarming purely
                as a function of how long it took, and the raw number reads as
                alarming either way. The error counters need no such thing --
                two link-downs is two link-downs however long you watched. */}
            {c === "xmit_wait" && span
              ? <span className="muted"> · {perSecond(v / span)}</span>
              : null}
          </dd>
        </div>
      ))}
      <div className="kv-row">
        <dt>Accumulated over</dt>
        <dd className="muted">
          {span ? `${Math.round(span)}s` : "an unknown span"}
        </dd>
      </div>
    </dl>
  );
}

// ---- shared ----------------------------------------------------------------

/** A speed or width reads as its label; the raw mask stays available on hover
 *  because a decode table can be wrong and the mask is the ground truth. */
function rate(r: Rate | undefined): string {
  return r?.label ?? (r?.mask != null ? `0x${r.mask.toString(16)}` : "—");
}

function Row({ k, v, mono }: { k: string; v: string | null; mono?: boolean }) {
  return (
    <div className="kv-row">
      <dt>{k}</dt>
      <dd className={mono ? "mono" : ""}>{v ?? "—"}</dd>
    </div>
  );
}

/** A 404 means the element does not exist at this instant - ordinary when
 *  looking at the past - so it reads differently from a real failure. */
function Failed({ id, error }: { id: string; error: unknown }) {
  if (error instanceof ApiError && error.isNotFound) {
    return <p className="panel-hint">No {id} at this point in time.</p>;
  }
  const msg = error instanceof Error ? error.message : String(error);
  return <p className="panel-hint warn">Could not load {id}: {msg}</p>;
}
