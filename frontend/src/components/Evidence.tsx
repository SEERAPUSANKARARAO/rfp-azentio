import { AlertTriangle, BookMarked, GitCompareArrows, ShieldAlert, ShieldCheck, UserRoundCheck } from "lucide-react";
import { Link } from "react-router-dom";
import { FLAG_LABEL, type Answer } from "../api";
import { useAudit } from "../hooks";
import { Chip, Gauge, cx, fmtDate, fmtTime } from "../ui";

function Banner({ tone, icon, title, children }: { tone: "bad" | "warn" | "info"; icon: React.ReactNode; title: string; children?: React.ReactNode }) {
  const t = { bad: "bg-bad-bg text-bad", warn: "bg-warn-bg text-warn", info: "bg-info-bg text-info" }[tone];
  return (
    <div className={cx("rounded-lg px-3 py-2 text-xs", t)}>
      <p className="flex items-center gap-1.5 font-semibold">{icon}{title}</p>
      {children && <div className="mt-1 space-y-1 text-ink/80">{children}</div>}
    </div>
  );
}

function Flags({ a }: { a: Answer }) {
  const d = a.flag_details ?? {};
  const f = new Set(a.flags);
  const out: React.ReactNode[] = [];
  if (f.has("conflicting")) {
    const idx = d.conflicts?.indexed ?? [];
    out.push(
      <Banner key="c" tone="bad" icon={<GitCompareArrows className="size-3.5" />} title="Sources contradict each other">
        {idx.map((c: { id: number; topic: string; detail: string }) => <p key={c.id}><b>{c.topic}:</b> {c.detail}</p>)}
        <p>An SME can settle this on the <Link className="underline" to="/knowledge">Knowledge Base</Link> page.</p>
      </Banner>);
  }
  if (f.has("stale")) {
    out.push(
      <Banner key="s" tone="warn" icon={<AlertTriangle className="size-3.5" />} title="Relies on a stale source">
        {(d.stale ?? []).map((s: { doc: string; date: string | null; reasons: string[] }) => <p key={s.doc}><b>{s.doc}</b>: {s.reasons.join("; ")}</p>)}
        <p>Ask an SME to confirm it still holds.</p>
      </Banner>);
  }
  if (f.has("missing")) {
    const anchors: string[] = d.missing_anchors ?? [];
    const items: string[] = (d.missing_info ?? []).filter((m: string) => !(anchors.length && /^No source mentions /.test(m)));
    out.push(
      <Banner key="m" tone="info" icon={<AlertTriangle className="size-3.5" />} title="Evidence is missing">
        {d.missing_anchors?.length > 0 && <p>No source mentions: <b>{d.missing_anchors.join(", ")}</b></p>}
        {items.length > 0 && <ul className="list-disc pl-4">{items.map((m) => <li key={m}>{m}</li>)}</ul>}
        <p>Nothing was assumed. Add a fact, or send it to the clarification list.</p>
      </Banner>);
  }
  if (f.has("unsupported")) {
    out.push(
      <Banner key="u" tone="warn" icon={<ShieldAlert className="size-3.5" />} title="The checker removed unsupported statements">
        <ul className="list-disc pl-4">
          {(d.removed_sentences ?? []).map((r: { sentence: string; reason: string }) => <li key={r.sentence}>&ldquo;{r.sentence}&rdquo;: {r.reason}</li>)}
        </ul>
      </Banner>);
  }
  if (f.has("injection")) {
    out.push(
      <Banner key="i" tone="bad" icon={<ShieldAlert className="size-3.5" />} title="An instruction hidden in this requirement was ignored">
        {(d.injection ?? []).map((s: string) => <p key={s} className="italic">&ldquo;{s}&rdquo;</p>)}
      </Banner>);
  }
  if (f.has("se_provided")) {
    out.push(
      <Banner key="p" tone="info" icon={<UserRoundCheck className="size-3.5" />} title="Contains facts supplied by an engineer">
        {(d.se_provided ?? []).map((s: { doc: string }) => <p key={s.doc}>{s.doc}</p>)}
        {d.se_edit_figures?.length > 0 && <p>Figures in the edited text that no cited source states: <b>{d.se_edit_figures.join(", ")}</b></p>}
      </Banner>);
  }
  if (a.triage === "owner") {
    out.push(<Banner key="o" tone="warn" icon={<UserRoundCheck className="size-3.5" />} title={`Routed to ${d.owner ?? a.assigned_to}`}><p>{d.reason}. Never drafted automatically.</p></Banner>);
  }
  if (d.status_adjusted) out.push(<Banner key="a" tone="info" icon={<ShieldCheck className="size-3.5" />} title="Status adjusted by the checker"><p>{d.status_adjusted}</p></Banner>);
  if (d.handoff_note && a.assigned_to) out.push(<Banner key="h" tone="info" icon={<UserRoundCheck className="size-3.5" />} title={`Handed to ${a.assigned_to}`}><p>{d.handoff_note}</p></Banner>);
  return <>{out}</>;
}

export default function Evidence({ rfpId, answer, activeMarker, onMarker }: {
  rfpId: number; answer: Answer | null; activeMarker: number | null; onMarker: (n: number | null) => void;
}) {
  const audit = useAudit(rfpId, 300);
  if (!answer) return <aside className="card h-full p-4 text-sm text-muted">Select a question.</aside>;
  const b = answer.confidence_breakdown ?? {};
  const trail = (audit.data ?? []).filter((e) => e.payload?.req_id === answer.req_id).slice(0, 6);
  const penalties = Object.entries((b.penalties ?? {}) as Record<string, number>);

  return (
    <aside aria-label="Evidence and audit" className="card flex h-full min-h-0 flex-col overflow-y-auto">
      <div className="space-y-4 p-4">
        <h2 className="text-sm font-semibold">Evidence &amp; audit</h2>

        {answer.review_status !== "queued" && answer.triage !== "owner" && (
          <div>
            <Gauge value={answer.confidence} />
            <dl className="mt-2 grid grid-cols-3 gap-2 text-center text-[11px] text-muted">
              <div><dt>Coverage</dt><dd className="font-semibold text-ink">{b.retrieval !== undefined ? Math.round(b.retrieval * 100) + "%" : "-"}</dd></div>
              <div><dt>Support</dt><dd className="font-semibold text-ink">{b.support !== undefined ? Math.round(b.support * 100) + "%" : "-"}</dd></div>
              <div><dt>Freshness</dt><dd className="font-semibold text-ink">{b.freshness !== undefined ? Math.round(b.freshness * 100) + "%" : "-"}</dd></div>
            </dl>
            {penalties.length > 0 && (
              <p className="mt-2 text-[11px] text-muted">Reduced by: {penalties.map(([k, v]) => `${k} (×${v})`).join(", ")}</p>
            )}
            <p className="mt-1 text-center text-[11px] text-muted">Computed from checks, not self-reported{b.model ? ` · ${b.model}` : ""}</p>
          </div>
        )}

        {answer.reasoning && answer.triage !== "owner" && (
          <div>
            <h3 className="h-section mb-1">Why this status</h3>
            <p className="text-sm">{answer.reasoning}</p>
          </div>
        )}

        <div className="space-y-2"><Flags a={answer} /></div>

        <div>
          <h3 className="h-section mb-2 flex items-center gap-1.5"><BookMarked className="size-3.5" /> Cited sources</h3>
          {answer.citations.length === 0 ? (
            <p className="text-sm text-muted">{answer.review_status === "queued" ? "Not drafted yet." : "No source is cited. Nothing here was assumed."}</p>
          ) : (
            <ol className="space-y-2">
              {answer.citations.map((c) => (
                <li key={c.marker} id={`cite-${c.marker}`}>
                  <button onClick={() => onMarker(activeMarker === c.marker ? null : c.marker)} aria-expanded={activeMarker === c.marker}
                    className={cx("w-full rounded-lg border p-2.5 text-left transition", activeMarker === c.marker ? "border-accent bg-accent-soft" : "border-line hover:bg-panel2")}>
                    <p className="flex items-start justify-between gap-2 text-sm font-medium">
                      <span><span className="chip chip-info mr-1.5">{c.marker}</span>{c.doc_title}</span>
                    </p>
                    <p className="mt-1 text-xs text-muted">{c.location} · {fmtDate(c.doc_date)}</p>
                    <blockquote className={cx("mt-2 rounded-md bg-warn-bg px-2 py-1.5 text-xs text-ink", activeMarker === c.marker ? "" : "line-clamp-3")}>
                      {c.quote}
                    </blockquote>
                  </button>
                </li>
              ))}
            </ol>
          )}
          {answer.citations.length > 0 && !answer.flags.includes("conflicting") && !answer.flags.includes("stale") && (
            <p className="mt-2 flex items-center gap-1.5 rounded-lg bg-ok-bg px-2.5 py-1.5 text-xs text-ok">
              <ShieldCheck className="size-3.5" /> No conflicts or stale sources among the cited evidence
            </p>
          )}
        </div>

        <div>
          <h3 className="h-section mb-1">Audit trail</h3>
          {trail.length === 0 ? <p className="text-xs text-muted">No activity yet.</p> : (
            <ul className="space-y-1.5 text-xs">
              {trail.map((e) => (
                <li key={e.id} className="flex items-baseline justify-between gap-2">
                  <span><Chip tone={e.action === "accepted" ? "ok" : "muted"}>{e.action.replace(/_/g, " ")}</Chip> <span className="text-muted">by {e.actor}</span></span>
                  <time className="shrink-0 text-muted">{fmtTime(e.ts)}</time>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>
    </aside>
  );
}

export { FLAG_LABEL };
