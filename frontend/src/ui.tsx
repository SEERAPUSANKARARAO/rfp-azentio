import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { AlertTriangle, CheckCircle2, Info, Loader2, X } from "lucide-react";
import type { ComplianceStatus } from "./api";

export const cx = (...parts: (string | false | null | undefined)[]) => parts.filter(Boolean).join(" ");

export function fmtDate(iso: string | null | undefined): string {
  if (!iso) return "undated";
  const d = new Date(iso.length === 10 ? iso + "T00:00:00" : iso);
  return isNaN(d.getTime()) ? iso : d.toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" });
}
export function fmtTime(iso: string): string {
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z");
  return isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

export function Spinner({ className }: { className?: string }) {
  return <Loader2 className={cx("animate-spin", className ?? "size-4")} aria-label="Loading" />;
}

export function Chip({ tone = "muted", children, title }: { tone?: "ok" | "warn" | "bad" | "info" | "muted"; children: ReactNode; title?: string }) {
  return (
    <span className={cx("chip", `chip-${tone}`)} title={title}>
      {children}
    </span>
  );
}

export function statusTone(s: ComplianceStatus | string | null): "ok" | "warn" | "bad" | "info" | "muted" {
  if (s === "Complied") return "ok";
  if (s === "Partially Complied") return "warn";
  if (s === "Not Complied") return "bad";
  if (s === "Cannot Confirm") return "info";
  return "muted";
}

export function StatusChip({ status }: { status: ComplianceStatus | string | null }) {
  return <Chip tone={statusTone(status)}>{status ?? "Not drafted"}</Chip>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 px-6 py-14 text-center">
      <p className="text-sm font-semibold">{title}</p>
      {children && <div className="max-w-md text-sm text-muted">{children}</div>}
    </div>
  );
}

export function ErrorNote({ error }: { error: unknown }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div role="alert" className="flex items-start gap-2 rounded-lg bg-bad-bg px-3 py-2 text-sm text-bad">
      <AlertTriangle className="mt-0.5 size-4 shrink-0" /> <span>{msg}</span>
    </div>
  );
}

/** Half-circle confidence gauge. Colour and the word both carry the level, never colour alone. */
export function Gauge({ value }: { value: number | null }) {
  const pct = value === null ? 0 : Math.round(value * 100);
  const level = value === null ? "No score" : pct >= 80 ? "High confidence" : pct >= 55 ? "Medium confidence" : "Low confidence";
  const color = value === null ? "var(--color-line)" : pct >= 80 ? "var(--color-ok)" : pct >= 55 ? "var(--color-warn)" : "var(--color-bad)";
  const r = 52, c = Math.PI * r;
  return (
    <div className="flex flex-col items-center" role="img" aria-label={`${level}: ${value === null ? "not scored" : pct + " percent"}`}>
      <svg viewBox="0 0 130 74" className="w-40">
        <path d="M13 65 A52 52 0 0 1 117 65" fill="none" stroke="var(--color-line)" strokeWidth="11" strokeLinecap="round" />
        <path d="M13 65 A52 52 0 0 1 117 65" fill="none" stroke={color} strokeWidth="11" strokeLinecap="round"
              strokeDasharray={`${(c * pct) / 100} ${c}`} />
        <text x="65" y="58" textAnchor="middle" fontSize="22" fontWeight="700" fill="var(--color-ink)">{value === null ? "-" : `${pct}%`}</text>
      </svg>
      <span className="-mt-1 text-xs font-medium text-muted">{level}</span>
    </div>
  );
}

export function ProgressBar({ done, total, label }: { done: number; total: number; label?: string }) {
  const pct = total ? Math.round((done / total) * 100) : 0;
  return (
    <div className="w-full" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100} aria-label={label ?? "Progress"}>
      <div className="h-1.5 overflow-hidden rounded-full bg-panel2">
        <div className="h-full rounded-full bg-accent transition-all" style={{ width: `${pct}%` }} />
      </div>
    </div>
  );
}

export function Modal({ title, onClose, children, wide }: { title: string; onClose: () => void; children: ReactNode; wide?: boolean }) {
  const ref = useRef<HTMLDivElement>(null);
  // Callers usually pass an inline `() => setOpen(false)`, a new function on every render. If the effect below
  // depended on it, each keystroke in a field inside the dialog would re-run it and pull focus back to the dialog.
  const closeRef = useRef(onClose);
  useEffect(() => { closeRef.current = onClose; });
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && closeRef.current();
    window.addEventListener("keydown", onKey);
    ref.current?.focus();
    return () => window.removeEventListener("keydown", onKey);
  }, []);
  return (
    <div className="fixed inset-0 z-50 flex items-start justify-center overflow-y-auto bg-black/50 p-4 pt-[8vh]" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label={title}
           className={cx("card w-full shadow-2xl outline-none", wide ? "max-w-3xl" : "max-w-lg")}>
        <div className="flex items-center justify-between border-b border-line px-5 py-3">
          <h2 className="text-base font-semibold">{title}</h2>
          <button className="btn btn-ghost btn-sm" onClick={onClose} aria-label="Close"><X className="size-4" /></button>
        </div>
        <div className="p-5">{children}</div>
      </div>
    </div>
  );
}

// ------------------------------------------------------------ toasts
type Toast = { id: number; tone: "ok" | "bad" | "info"; text: string };
const ToastCtx = createContext<(tone: Toast["tone"], text: string) => void>(() => undefined);
export const useToast = () => useContext(ToastCtx);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = useCallback((tone: Toast["tone"], text: string) => {
    const id = Date.now() + Math.random();
    setItems((x) => [...x, { id, tone, text }]);
    setTimeout(() => setItems((x) => x.filter((t) => t.id !== id)), tone === "bad" ? 7000 : 3500);
  }, []);
  return (
    <ToastCtx.Provider value={push}>
      {children}
      <div className="pointer-events-none fixed top-16 right-3 z-[60] flex w-[min(20rem,calc(100vw-1.5rem))] flex-col gap-2" aria-live="polite">
        {items.map((t) => (
          <div key={t.id} role="status" className={cx("card pointer-events-auto flex items-start gap-2 px-3 py-2 text-sm shadow-lg",
            t.tone === "bad" && "border-bad", t.tone === "ok" && "border-ok")}>
            {t.tone === "ok" ? <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-ok" /> :
             t.tone === "bad" ? <AlertTriangle className="mt-0.5 size-4 shrink-0 text-bad" /> : <Info className="mt-0.5 size-4 shrink-0 text-info" />}
            <span>{t.text}</span>
          </div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
