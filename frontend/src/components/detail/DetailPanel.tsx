import { useEffect, useRef, useState } from "react";
import type { SelectedItem } from "../../api/types";
import type { GraphModel } from "../../model/graph";
import { CardsPanel } from "./CardsPanel";
import { SummaryPanel } from "./SummaryPanel";

// At or above this many selected items, show aggregates instead of cards.
const SUMMARY_THRESHOLD = 20;

interface Props {
  fabric: string;
  model: GraphModel;
  selection: SelectedItem[];
  onNavigate: (id: string) => void; // add the target to the selection (accumulate)
  onDeselect: (id: string) => void; // remove one item from the selection
  onSetSelection: (ids: string[]) => void; // replace the selection wholesale
  /** Seconds of counter history the cards should summarise -- the same window
   *  the overlay is using, so a card and the colour beside it agree. */
  countersWindow?: number;
  /** Ask for per-port throughput too — set when the graph is coloured by it. */
  withTraffic?: boolean;
}

export function DetailPanel({
  fabric, model, selection, onNavigate, onDeselect, onSetSelection,
  countersWindow, withTraffic,
}: Props) {
  const [forceCards, setForceCards] = useState(false); // Force individual cards regardless of count.
  const prevIds = useRef<string[]>([]);

  // Revoke the escape hatch when the selection *grows* - a new box-select
  // should summarize again. Shrinking keeps it, so closing a card from the
  // forced view doesn't snap the panel back to the summary.
  useEffect(() => {
    const ids = selection.map((s) => s.id);
    if (ids.some((id) => !prevIds.current.includes(id))) setForceCards(false);
    prevIds.current = ids;
  }, [selection]);

  const summarizing = selection.length >= SUMMARY_THRESHOLD && !forceCards;

  return (
    <aside className="panel">
      {selection.length === 0 ? (
        <p className="panel-hint">Select a node or link on the graph to inspect it.</p>
      ) : summarizing ? (
        <SummaryPanel
          model={model}
          selection={selection}
          onSetSelection={onSetSelection}
          onNavigate={onNavigate}
          onShowAll={() => setForceCards(true)}
        />
      ) : (
        <CardsPanel
          fabric={fabric}
          model={model}
          selection={selection}
          onNavigate={onNavigate}
          onDeselect={onDeselect}
          countersWindow={countersWindow}
          withTraffic={withTraffic}
        />
      )}
    </aside>
  );
}