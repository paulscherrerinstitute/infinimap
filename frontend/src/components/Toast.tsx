import { useEffect, useRef, useState, useSyncExternalStore } from "react";

type ToastVariant = "ok" | "warn" | "error" | "info";

type Toast = {
  id: number;
  message: string;
  variant: ToastVariant;
  detail?: string;
  ms: number;
};

const VARIANT_COLOR: Record<ToastVariant, string> = {
  ok: "#3ddc84",
  warn: "#e8a33d",
  error: "#e5484d",
  info: "#8b96a5",
};

const DEFAULT_MS = 4000;
const EXIT_MS = 220;

// --- module-level store: lets `toast()` be called from anywhere, hooks or not ---

let toasts: Toast[] = [];
let nextId = 0;
const listeners = new Set<() => void>();

function emit() {
  toasts = [...toasts];
  listeners.forEach((l) => l());
}

function subscribe(listener: () => void) {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

function getSnapshot() {
  return toasts;
}

export function Toast(
  message: string,
  variant: ToastVariant = "info",
  detail?: string,
  ms: number = DEFAULT_MS
): number {
  const id = nextId++;
  toasts.push({ id, message, variant, detail, ms });
  emit();
  return id;
}

Toast.ok = (m: string, detail?: string, ms: number = DEFAULT_MS) => Toast(m, "ok", detail, ms);
Toast.warn = (m: string, detail?: string, ms: number = DEFAULT_MS) => Toast(m, "warn", detail, ms);
Toast.error = (m: string, detail?: string, ms: number = DEFAULT_MS) => Toast(m, "error", detail, ms);
Toast.info = (m: string, detail?: string, ms: number = DEFAULT_MS) => Toast(m, "info", detail, ms);

Toast.dismiss = (id: number) => {
  toasts = toasts.filter((t) => t.id !== id);
  emit();
};

Toast.clear = () => {
  toasts = [];
  emit();
};

// --- rendering ---

function ToastItem({ toast: t }: { toast: Toast }) {
  const [leaving, setLeaving] = useState(false);
  const paused = useRef(false);

  useEffect(() => {
    const timer = setTimeout(() => {
      if (!paused.current) setLeaving(true);
    }, t.ms);
    return () => clearTimeout(timer);
  }, [t.ms]);

  useEffect(() => {
    if (!leaving) return;
    const timer = setTimeout(() => Toast.dismiss(t.id), EXIT_MS);
    return () => clearTimeout(timer);
  }, [leaving, t.id]);

  return (
    <div
      role="status"
      aria-live="polite"
      onMouseEnter={() => { paused.current = true; }}
      onMouseLeave={() => { paused.current = false; }}
      onClick={() => setLeaving(true)}
      style={{
        display: "flex",
        alignItems: "flex-start",
        gap: 10,
        minWidth: 280,
        maxWidth: 420,
        padding: "10px 14px",
        borderRadius: 6,
        background: "#1c2128",
        border: "1px solid #30363d",
        borderLeft: `3px solid ${VARIANT_COLOR[t.variant]}`,
        boxShadow: "0 8px 24px rgba(0,0,0,0.5)",
        color: "#e6edf3",
        font: "13px/1.4 system-ui, sans-serif",
        cursor: "pointer",
        pointerEvents: "auto",
        animation: leaving
          ? `toast-out ${EXIT_MS}ms ease-in forwards`
          : "toast-in 260ms cubic-bezier(0.16, 1, 0.3, 1)",
      }}
    >
      <span
        style={{
          width: 8,
          height: 8,
          marginTop: 5,
          borderRadius: 2,
          flexShrink: 0,
          background: VARIANT_COLOR[t.variant],
        }}
      />
      <div style={{ minWidth: 0 }}>
        <div style={{ fontWeight: 600 }}>{t.message}</div>
        {t.detail && (
          <div style={{ marginTop: 2, color: "#8b96a5", fontSize: 12 }}>{t.detail}</div>
        )}
      </div>
    </div>
  );
}

export function ToastHost() {
  const items = useSyncExternalStore(subscribe, getSnapshot, getSnapshot);

  return (
    <>
      <style>{`
        @keyframes toast-in {
          from { opacity: 0; transform: translateY(-16px) scale(0.97); }
          to   { opacity: 1; transform: translateY(0) scale(1); }
        }
        @keyframes toast-out {
          from { opacity: 1; transform: translateY(0); }
          to   { opacity: 0; transform: translateY(-10px); }
        }
        @media (prefers-reduced-motion: reduce) {
          @keyframes toast-in  { from { opacity: 0; } to { opacity: 1; } }
          @keyframes toast-out { from { opacity: 1; } to { opacity: 0; } }
        }
      `}</style>
      <div
        style={{
          position: "fixed",
          top: 52,
          left: "50%",
          transform: "translateX(-50%)",
          zIndex: 1000,
          display: "flex",
          flexDirection: "column",
          gap: 8,
          pointerEvents: "none",
        }}
      >
        {items.map((t) => (
          <ToastItem key={t.id} toast={t} />
        ))}
      </div>
    </>
  );
}