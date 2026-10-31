import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowRight, BookOpenCheck, Check, GitBranch, RefreshCw, ScanSearch, Users } from "lucide-react";
import {
  CAP_LABEL, CAP_TONE, GAP_HELP, GAP_LABEL, KIND_LABEL, http, type Issue, type MatrixRow, type ChecklistItem,
} from "../api";
import { refreshAnalysis, useChecklist, useGaps, useMatrix, useReadiness, useReviewer, useRfp, useWorkflow } from "../hooks";
import { Chip, Empty, ErrorNote, Modal, Spinner, StatusChip, cx, useToast } from "../ui";

type Tab = "matrix" | "gaps" | "gate" | "workflow";
const TABS: { key: Tab; label: string }[] = [
  { key: "matrix", label: "Compliance matrix" }, { key: "gaps", label: "Gap analysis" },
  { key: "gate", label: "Review gate and checklist" }, { key: "workflow", label: "Workflow" },
];

function Tile({ label, value, tone }: { label: string; value: string | number; tone?: "bad" | "warn" | "ok" }) {
  return (
    <div className="card px-4 py-3">
      <p className="text-xs text-muted">{label}</p>
      <p className={cx("text-2xl font-bold", tone === "bad" && "text-bad", tone === "warn" && "text-warn", tone === "ok" && "text-ok")}>{value}</p>
    </div>
  );
}

function Reqs({ ids, rfpId }: { ids: string[]; rfpId: number }) {
  if (!ids.length) return null;
  return <span className="inline-flex flex-wrap gap-1">{ids.slice(0, 12).map((r) => <Link key={r} to={`/rfps/${rfpId}`} className="chip chip-info">{r}</Link>)}{ids.length > 12 && <span className="text-xs text-muted">+{ids.length - 12}</span>}</span>;
}

// ------------------------------------------------------------------ matrix
const FILTERS = [
  { key: "all", label: "All", test: () => true },
  { key: "validate", label: "Needs validation", test: (r: MatrixRow) => r.needs_validation && !r.confirmed },
  { key: "gaps", label: "Has a gap", test: (r: MatrixRow) => r.gaps.length > 0 },
  { key: "unclear", label: "Unclear wording", test: (r: MatrixRow) => (r.interpretation?.findings ?? []).length > 0 },
  { key: "spans", label: "Spans components", test: (r: MatrixRow) => !!r.interpretation?.spans_multiple },
];

function MatrixTab({ rfpId }: { rfpId: number }) {
  const { data, isLoading, error } = useMatrix(rfpId);
  const [filter, setFilter] = useState("all");
  const [open, setOpen] = useState<number | null>(null);
  const rows = useMemo(() => (data?.rows ?? []).filter(FILTERS.find((f) => f.key === filter)!.test), [data, filter]);
  if (isLoading) return <Spinner />;
  if (error) return <ErrorNote error={error} />;
  if (!data || data.rows.length === 0) return <Empty title="No requirements">Import an RFP first.</Empty>;
  const scheme = data.scheme;
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted">
        {scheme?.kind === "capability" ? <>The RFP asks bidders to classify each capability. Codes: {scheme.codes.map((c) => <span key={c.code} className="mr-2"><b className="font-mono">{c.code}</b> {c.label}</span>)}</>
          : scheme?.kind === "compliance" ? "The RFP asks for compliance wording only, so no capability code is suggested." : "No response codes were detected in the RFP. Capability is still assessed."}
        {" "}A code is only written into the export once the evidence is complete or a person has validated it.
      </p>
      <div className="flex flex-wrap gap-1" role="tablist" aria-label="Filter requirements">
        {FILTERS.map((f) => (
          <button key={f.key} role="tab" aria-selected={filter === f.key} onClick={() => setFilter(f.key)}
            className={cx("rounded-lg px-3 py-1 text-sm", filter === f.key ? "bg-accent text-on-solid" : "bg-panel2 text-muted")}>
            {f.label} <span className="opacity-70">({data.rows.filter(f.test).length})</span></button>
        ))}
      </div>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[820px] text-left text-sm">
          <thead className="border-b border-line text-xs text-muted">
            <tr><th className="px-3 py-2">Ref</th><th className="px-3 py-2">Requirement</th><th className="px-3 py-2">Answer</th><th className="px-3 py-2">Capability</th>
              <th className="px-3 py-2">Code</th><th className="px-3 py-2">Validation</th><th className="px-3 py-2">Suggested owner</th></tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <Fragment key={r.requirement_id}>
                <tr className="cursor-pointer border-b border-line/60 align-top hover:bg-panel2" onClick={() => setOpen(open === r.requirement_id ? null : r.requirement_id)}>
                  <td className="px-3 py-2 font-medium whitespace-nowrap">{r.req_id}</td>
                  <td className="max-w-md px-3 py-2"><p className="line-clamp-2">{r.text}</p>{r.mandatory === false && <span className="text-xs text-muted">optional</span>}</td>
                  <td className="px-3 py-2"><StatusChip status={r.status} /></td>
                  <td className="px-3 py-2"><Chip tone={CAP_TONE[r.capability] ?? "muted"}>{CAP_LABEL[r.capability] ?? r.capability}</Chip></td>
                  <td className="px-3 py-2 font-mono">{r.code || <span className="text-muted">-</span>}</td>
                  <td className="px-3 py-2">{!r.assessed ? <span className="text-muted">-</span> : r.confirmed ? <Chip tone="ok">validated</Chip> : r.needs_validation ? <Chip tone="warn">needs a person</Chip> : r.code ? <Chip tone="ok">complete</Chip> : null}</td>
                  <td className="px-3 py-2 text-xs">{r.gap_owner || <span className="text-muted">-</span>}</td>
                </tr>
                {open === r.requirement_id && (
                  <tr className="border-b border-line/60 bg-panel2/50">
                    <td colSpan={7} className="space-y-2 px-4 py-3 text-xs">
                      {r.validation_reasons.map((x) => <p key={x} className="text-warn">{x}</p>)}
                      {r.gaps.map((g) => <p key={g.type}><b>{GAP_LABEL[g.type]}.</b> {g.detail} <span className="text-muted">Owner: {g.owner}.</span></p>)}
                      {(r.interpretation?.findings ?? []).map((f) => <p key={f.detail}><b>{KIND_LABEL[f.kind]}.</b> {f.detail}</p>)}
                      {r.validation_reasons.length + r.gaps.length + (r.interpretation?.findings ?? []).length === 0 && <p className="text-muted">Nothing outstanding.</p>}
                      <Link className="underline" to={`/rfps/${rfpId}`}>Open in the Response Studio to validate or correct it <ArrowRight className="inline size-3" /></Link>
                    </td>
                  </tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ gaps
function GapsTab({ rfpId }: { rfpId: number }) {
  const { data, isLoading, error } = useGaps(rfpId);
  if (isLoading) return <Spinner />;
  if (error) return <ErrorNote error={error} />;
  if (!data) return null;
  if (data.items.length === 0) return <Empty title="No gaps found">Run the assessment once answers are drafted.</Empty>;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-3 lg:grid-cols-6">
        {Object.entries(data.counts).map(([k, n]) => <Tile key={k} label={GAP_LABEL[k] ?? k} value={n} tone={n && (k === "confirmed_gap" || k === "security_concern") ? "bad" : n ? "warn" : undefined} />)}
      </div>
      <p className="text-sm text-muted"><b>{GAP_LABEL.confirmed_gap}</b>: {GAP_HELP.confirmed_gap} <b>{GAP_LABEL.knowledge_gap}</b>: {GAP_HELP.knowledge_gap}</p>
      <section aria-label="By owner" className="card p-4">
        <h2 className="h-section mb-2 flex items-center gap-1.5"><Users className="size-3.5" /> Who should look</h2>
        <ul className="space-y-1.5 text-sm">{Object.entries(data.by_owner).map(([o, ids]) => <li key={o} className="flex flex-wrap items-center gap-2"><b className="w-48">{o}</b><Reqs ids={ids} rfpId={rfpId} /></li>)}</ul>
      </section>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[720px] text-left text-sm">
          <thead className="border-b border-line text-xs text-muted"><tr><th className="px-3 py-2">Ref</th><th className="px-3 py-2">Gap</th><th className="px-3 py-2">What is missing</th><th className="px-3 py-2">Owner</th></tr></thead>
          <tbody>{data.items.map((g, n) => (
            <tr key={n} className="border-b border-line/60 align-top">
              <td className="px-3 py-2 font-medium">{g.req_id}{g.critical && <span className="ml-1 text-xs text-bad" title="Affects a mandatory requirement">●</span>}</td>
              <td className="px-3 py-2"><Chip tone={g.type === "confirmed_gap" || g.type === "security_concern" ? "bad" : "warn"}>{GAP_LABEL[g.type]}</Chip></td>
              <td className="px-3 py-2">{g.detail}</td><td className="px-3 py-2">{g.owner}</td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ gate + checklist
function IssueList({ title, issues, tone, rfpId }: { title: string; issues: Issue[]; tone: "bad" | "warn" | "info"; rfpId: number }) {
  if (!issues.length) return null;
  const cls = { bad: "border-bad", warn: "border-warn", info: "border-line" }[tone];
  return (
    <section className={cx("card border-l-4 p-4", cls)} aria-label={title}>
      <h2 className="h-section mb-2">{title}</h2>
      <ul className="space-y-2 text-sm">{issues.map((i, n) => <li key={n}><p>{i.message}</p><Reqs ids={i.requirements} rfpId={rfpId} /></li>)}</ul>
    </section>
  );
}

function ChecklistRow({ rfpId, item }: { rfpId: number; item: ChecklistItem }) {
  const qc = useQueryClient();
  const [reviewer] = useReviewer();
  const done = item.status === "done";
  const [checked, setChecked] = useState(done);  // responds at once; the server confirms in the background
  useEffect(() => setChecked(done), [done]);
  const tick = useMutation({
    mutationFn: (value: boolean) => http.put(`/rfps/${rfpId}/checklist/${item.key}`, { done: value, reviewer }),
    onSuccess: () => refreshAnalysis(qc, rfpId),
    onError: () => setChecked(done),
  });
  return (
    <li className="flex items-start gap-3 py-2">
      {item.kind === "manual"
        ? <input type="checkbox" className="mt-1 size-4" checked={checked} onChange={(e) => { setChecked(e.target.checked); tick.mutate(e.target.checked); }} aria-label={item.label} />
        : <span className={cx("mt-0.5 grid size-4 shrink-0 place-items-center rounded-full text-[10px]", done ? "bg-ok text-on-solid" : item.status === "na" ? "bg-panel2 text-muted" : "border border-warn text-warn")}>{done ? <Check className="size-3" /> : item.status === "na" ? "-" : "!"}</span>}
      <div className="min-w-0"><p className={cx("text-sm", item.status === "na" && "text-muted")}>{item.label}{item.kind === "manual" && <span className="ml-2 text-xs text-muted">tick when prepared</span>}</p>
        {item.detail && <p className="text-xs text-muted">{item.detail}</p>}</div>
    </li>
  );
}

function GateTab({ rfpId }: { rfpId: number }) {
  const gate = useReadiness(rfpId);
  const list = useChecklist(rfpId);
  if (gate.isLoading) return <Spinner />;
  if (gate.error) return <ErrorNote error={gate.error} />;
  const g = gate.data!;
  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,1.4fr)_minmax(0,1fr)]">
      <div className="space-y-3">
        {g.blockers.length === 0 && <p className="card border-l-4 border-ok p-4 text-sm"><b className="text-ok">Nothing blocks review.</b> Unsupported claims, mismatched codes and unowned mandatory requirements are all clear.</p>}
        <IssueList title="Fix before review" issues={g.blockers} tone="bad" rfpId={rfpId} />
        <IssueList title="Worth a look" issues={g.warnings} tone="warn" rfpId={rfpId} />
        <IssueList title="For information" issues={g.info} tone="info" rfpId={rfpId} />
      </div>
      <section className="card p-4" aria-label="Submission checklist">
        <h2 className="h-section mb-1 flex items-center gap-1.5"><BookOpenCheck className="size-3.5" /> Submission checklist</h2>
        {list.data && <p className="mb-1 text-xs text-muted">{list.data.done} done, {list.data.outstanding} outstanding</p>}
        {list.isLoading ? <Spinner /> : <ul className="divide-y divide-line/60">{(list.data?.items ?? []).map((i) => <ChecklistRow key={i.key} rfpId={rfpId} item={i} />)}</ul>}
      </section>
    </div>
  );
}

// ------------------------------------------------------------------ workflow
function WorkflowTab({ rfpId }: { rfpId: number }) {
  const { data, isLoading, error } = useWorkflow(rfpId);
  if (isLoading) return <Spinner />;
  if (error) return <ErrorNote error={error} />;
  if (!data) return null;
  return (
    <div className="space-y-4">
      <div className="grid gap-3 sm:grid-cols-4 lg:grid-cols-7">
        {Object.entries(data.counts).map(([k, n]) => <Tile key={k} label={data.labels[k] ?? k} value={n} tone={k === "approved" && n ? "ok" : k === "approved_recheck" && n ? "warn" : undefined} />)}
      </div>
      <section className="card p-4" aria-label="Who has what">
        <h2 className="h-section mb-2 flex items-center gap-1.5"><Users className="size-3.5" /> Who has what</h2>
        {Object.keys(data.by_role).length === 0 ? <p className="text-sm text-muted">Nothing is waiting on anyone.</p> :
          <ul className="space-y-1.5 text-sm">{Object.entries(data.by_role).map(([role, ids]) => <li key={role} className="flex flex-wrap items-center gap-2"><b className="w-48">{role}</b><Reqs ids={ids} rfpId={rfpId} /></li>)}</ul>}
      </section>
      <div className="card overflow-x-auto">
        <table className="w-full min-w-[620px] text-left text-sm">
          <thead className="border-b border-line text-xs text-muted"><tr><th className="px-3 py-2">Ref</th><th className="px-3 py-2">State</th><th className="px-3 py-2">With</th><th className="px-3 py-2">Suggested</th></tr></thead>
          <tbody>{data.items.map((i) => (
            <tr key={i.req_id} className="border-b border-line/60">
              <td className="px-3 py-1.5 font-medium">{i.req_id}</td>
              <td className="px-3 py-1.5"><Chip tone={i.state.startsWith("approved") ? (i.state === "approved" ? "ok" : "warn") : i.state === "ready_for_review" ? "info" : "muted"}>{i.label}</Chip></td>
              <td className="px-3 py-1.5 text-xs">{i.assigned_to ?? <span className="text-muted">-</span>}</td>
              <td className="px-3 py-1.5 text-xs">{i.suggested_owner || <span className="text-muted">-</span>}</td>
            </tr>))}</tbody>
        </table>
      </div>
    </div>
  );
}

// ------------------------------------------------------------------ page
export default function Analysis({ rfpId }: { rfpId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reviewer] = useReviewer();
  const rfp = useRfp(rfpId);
  const matrix = useMatrix(rfpId);
  const gate = useReadiness(rfpId);
  const [tab, setTab] = useState<Tab>("matrix");
  const [proceedOpen, setProceedOpen] = useState(false);
  const [reason, setReason] = useState("");

  const run = (label: string, fn: () => Promise<unknown>, done: (r: never) => string) =>
    useMutation({ mutationFn: fn, onSuccess: (r) => { refreshAnalysis(qc, rfpId); toast("ok", done(r as never)); }, onError: (e: Error) => toast("bad", `${label}: ${e.message}`) });
  const assess = run("Assessment", () => http.post(`/rfps/${rfpId}/assess`, { force: false }), () => "Assessment started. Results appear as each requirement finishes.");
  const interpret = run("Reading the requirements", () => http.post(`/rfps/${rfpId}/interpret`), (r: { with_findings: number; with_dependencies: number }) => `Found ${r.with_findings} requirement(s) with unclear wording and ${r.with_dependencies} with dependencies.`);
  const assign = run("Assigning", () => http.post(`/rfps/${rfpId}/assign-suggested`, { reviewer }), (r: { assigned: unknown[] }) => `${r.assigned.length} requirement(s) handed to the suggested role.`);
  const redraft = run("Redraft", () => http.post(`/rfps/${rfpId}/regenerate-affected`, { reason: "Customer replies changed what these requirements mean", reviewer }),
    (r: { redrafting: string[]; kept_approved: string[] }) => `Redrafting ${r.redrafting.length}; ${r.kept_approved.length} approved answer(s) kept as they are and marked for a re-check.`);
  const proceed = useMutation({
    mutationFn: (acknowledge: boolean) => http.post(`/rfps/${rfpId}/proceed-to-review`, { acknowledge, reason, reviewer }),
    onSuccess: () => { refreshAnalysis(qc, rfpId); qc.invalidateQueries({ queryKey: ["rfp", rfpId] }); setProceedOpen(false); toast("ok", "Marked as ready for review"); },
    onError: (e: Error) => toast("bad", e.message),
  });

  const g = gate.data;
  const rows = matrix.data?.rows ?? [];
  const validated = rows.filter((r) => r.code && (r.confirmed || !r.needs_validation)).length;
  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs text-muted"><Link className="underline" to={`/rfps/${rfpId}`}>{rfp.data?.name ?? "RFP"}</Link> / Analysis</p>
        <h1 className="text-xl font-bold">Compliance, gaps and readiness</h1>
      </div>
      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-5">
        <Tile label="Requirements" value={g?.counts.requirements ?? "-"} />
        <Tile label="Codes validated" value={`${validated} of ${rows.filter((r) => r.triage !== "owner").length}`} />
        <Tile label="Open clarifications" value={g?.counts.open_clarifications ?? "-"} tone={g?.counts.open_clarifications ? "warn" : undefined} />
        <Tile label="Blocking review" value={g ? g.blockers.length : "-"} tone={g?.blockers.length ? "bad" : "ok"} />
        <Tile label="Status" value={g?.in_review ? "In review" : "Preparing"} />
      </div>
      <div className="flex flex-wrap gap-2">
        <button className="btn" disabled={assess.isPending || matrix.data?.assessing} onClick={() => assess.mutate()}>
          {assess.isPending || matrix.data?.assessing ? <Spinner className="size-4" /> : <ScanSearch className="size-4" />} {matrix.data?.assessing ? "Assessing..." : "Assess capabilities"}</button>
        <button className="btn" disabled={interpret.isPending} onClick={() => interpret.mutate()}>{interpret.isPending ? <Spinner className="size-4" /> : <GitBranch className="size-4" />} Read the requirements</button>
        <button className="btn" disabled={assign.isPending} onClick={() => assign.mutate()}><Users className="size-4" /> Assign suggested owners</button>
        <button className="btn" disabled={redraft.isPending} onClick={() => redraft.mutate()} title="Redrafts what a customer reply or a dependency changed. Approved answers are never rewritten."><RefreshCw className="size-4" /> Redraft what changed</button>
        <button className="btn btn-primary ml-auto" disabled={proceed.isPending || g?.in_review} onClick={() => (g?.can_proceed ? proceed.mutate(false) : setProceedOpen(true))}>
          <Check className="size-4" /> {g?.in_review ? "In review" : "Proceed to review"}</button>
      </div>
      <div className="flex flex-wrap gap-1" role="tablist" aria-label="Analysis views">
        {TABS.map((t) => (
          <button key={t.key} role="tab" aria-selected={tab === t.key} onClick={() => setTab(t.key)}
            className={cx("rounded-lg px-3 py-1.5 text-sm font-medium", tab === t.key ? "bg-accent text-on-solid" : "bg-panel2 text-muted")}>{t.label}</button>
        ))}
      </div>
      {tab === "matrix" && <MatrixTab rfpId={rfpId} />}
      {tab === "gaps" && <GapsTab rfpId={rfpId} />}
      {tab === "gate" && <GateTab rfpId={rfpId} />}
      {tab === "workflow" && <WorkflowTab rfpId={rfpId} />}
      {proceedOpen && g && (
        <Modal title="This response has issues a reviewer should not have to find" onClose={() => setProceedOpen(false)}>
          <ul className="list-disc space-y-1 pl-5 text-sm">{g.blockers.map((b, n) => <li key={n}>{b.message}</li>)}</ul>
          <label className="label mt-3" htmlFor="proceed-reason">Why proceed anyway? (recorded in the audit log)</label>
          <textarea id="proceed-reason" className="input" rows={3} value={reason} onChange={(e) => setReason(e.target.value)} />
          <div className="mt-4 flex justify-end gap-2">
            <button className="btn" onClick={() => setProceedOpen(false)}>Go back and fix them</button>
            <button className="btn btn-danger" disabled={!reason.trim() || proceed.isPending} onClick={() => proceed.mutate(true)}>Proceed anyway</button>
          </div>
        </Modal>
      )}
    </div>
  );
}
