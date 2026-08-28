import { useEffect } from "react";

export interface MenuItem {
  label: string;
  onClick: () => void;
  disabled?: boolean;
}

interface Props {
  x: number;
  y: number;
  items: MenuItem[];
  onClose: () => void;
}

// A small positioned menu. Placement is handled by the caller (x/y are relative
// to the graph-wrap); this owns its own outside-click / Escape dismissal.
export function ContextMenu({ x, y, items, onClose }: Props) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    // Any mousedown that isn't inside the menu closes it. Deferred a tick so the
    // opening right-click doesn't immediately dismiss it.
    const onDown = (e: MouseEvent) => {
      if (!(e.target as HTMLElement).closest(".context-menu")) onClose();
    };
    window.addEventListener("keydown", onKey);
    const id = window.setTimeout(() => window.addEventListener("mousedown", onDown), 0);
    return () => {
      window.removeEventListener("keydown", onKey);
      window.clearTimeout(id);
      window.removeEventListener("mousedown", onDown);
    };
  }, [onClose]);

  return (
    <div className="context-menu" style={{ left: x, top: y }} onContextMenu={(e) => e.preventDefault()}>
      {items.map((it) => (
        <button
          key={it.label}
          className="context-menu-item"
          disabled={it.disabled}
          onClick={() => {
            it.onClick();
            onClose();
          }}
        >
          {it.label}
        </button>
      ))}
    </div>
  );
}
