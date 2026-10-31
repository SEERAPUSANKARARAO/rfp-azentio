import { useMemo } from "react";
import { CheckCircle2, CircleDashed, Flag, HelpCircle, UserCheck } from "lucide-react";
import type { Answer } from "../api";
import { Chip, cx } from "../ui";

export type Filter = "all" | "needs_review" | "approved" | "clarify" | "flagged";

export const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "All" },
  { key: "needs_review", label: "Needs review" },
  { key: "approved", label: "Approved" },
  { key: "clarify", label: "Clarify" },
  { key: "flagged", label: "Flagged" },
];

export function matches(filter: Filter, a: Answer): boolean {
  switch (filter) {
    case "all": return true;
    case "approved": return a.review_status === "accepted";
    case "needs_review": return a.review_status === "drafted" && a.needs_review;
    case "clarify": return a.triage === "clarify";
    case "flagged": return !!a.assigned_to;
  }
}

function ConfidenceChip({ a }: { a: Answer }) {
  if (a.review_status === "accepted") return <Chip tone="ok"><CheckCircle2 className="size-3" /> Approved</Chip>;
  if (a.review_status === "queued") return <Chip><CircleDashed className="size-3" /> Not drafted</Chip>;
  if (a.triage === "owner") return <Chip tone="warn"><UserCheck className="size-3" /> {a.assigned_to}</Chip>;
  if (a.triage === "clarify") return <Chip tone="info"><HelpCircle className="size-3" /> Clarify</Chip>;
  const pct = Math.round((a.confidence ?? 0) * 100);
  return <Chip tone={pct >= 80 ? "ok" : pct >= 55 ? "warn" : "bad"}>{pct}% match{a.needs_review ? " · review" : ""}</Chip>;
}

export default function Queue({ answers, filter, onFilter, selected, onSelect }: {
  answers: Answer[]; filter: Filter; onFilter: (f: Filter) => void; selected: number | null; onSelect: (id: number) => void;
}) {
  const counts = useMemo(() => Object.fromEntries(FILTERS.map((f) => [f.key, answers.filter((a) => matches(f.key, a)).length])) as Record<Filter, number>, [answers]);
  const groups = useMemo(() => {
    const m = new Map<string, { title: string; items: Answer[] }>();
    for (const a of answers.filter((x) => matches(filter, x))) {
      const key = `${a.section_code}|${a.section_title}`;
      if (!m.has(key)) m.set(key, { title: a.section_code ? `${a.section_code}. ${a.section_title}` : a.section_title, items: [] });
      m.get(key)!.items.push(a);
    }
    return [...m.values()];
  }, [answers, filter]);

  return (
    <section aria-label="Question queue" className="card flex h-full min-h-0 flex-col">
      <div className="border-b border-line px-3 pt-3 pb-2">
        <h2 className="mb-2 text-sm font-semibold">Question queue &amp; triage</h2>
        <div className="flex flex-wrap gap-1" role="tablist" aria-label="Filter questions">
          {FILTERS.map((f) => (
            <button key={f.key} role="tab" aria-selected={filter === f.key} onClick={() => onFilter(f.key)}
              className={cx("rounded-full px-2.5 py-1 text-xs font-medium transition",
                filter === f.key ? "bg-accent text-on-solid" : "bg-panel2 text-muted hover:text-ink")}>
              {f.label} <span className="opacity-75">{counts[f.key]}</span>
            </button>
          ))}
        </div>
      </div>
      <div className="min-h-0 flex-1 overflow-y-auto p-2">
        {groups.length === 0 && <p className="px-3 py-8 text-center text-sm text-muted">Nothing in this view.</p>}
        {groups.map((g) => (
          <div key={g.title} className="mb-3">
            <h3 className="h-section sticky top-0 bg-panel px-2 py-1">{g.title}</h3>
            <ul>
              {g.items.map((a) => (
                <li key={a.requirement_id}>
                  <button onClick={() => onSelect(a.requirement_id)} aria-current={selected === a.requirement_id}
                    className={cx("mb-1 w-full rounded-lg border px-2.5 py-2 text-left transition",
                      selected === a.requirement_id ? "border-accent bg-accent-soft" : "border-transparent hover:bg-panel2")}>
                    <div className="flex items-center justify-between gap-2">
                      <span className="flex items-center gap-1.5 text-sm font-semibold">
                        {a.req_id}
                        {a.mandatory === false && <span className="text-[10px] font-medium text-muted">optional</span>}
                        {a.assigned_to && a.triage !== "owner" && <Flag className="size-3 text-warn" aria-label={`Flagged for ${a.assigned_to}`} />}
                      </span>{" "}
                      <ConfidenceChip a={a} />
                    </div>
                    <p className="mt-1 line-clamp-2 text-xs text-muted">{a.requirement}</p>
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </section>
  );
}
