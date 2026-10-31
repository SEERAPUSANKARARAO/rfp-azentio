import { useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BookOpen, FileSpreadsheet, FileText, Play, UploadCloud } from "lucide-react";
import { http, type Job, type RfpDetail, type RfpSummary } from "../api";
import { useKbStatus, useRfp, useRfps, useSummary } from "../hooks";
import { Chip, Empty, ErrorNote, ProgressBar, Spinner, cx, fmtDate, useToast } from "../ui";

const ICON = { xlsx: FileSpreadsheet, docx: FileText, pdf: FileText } as const;

function RfpCard({ r }: { r: RfpSummary }) {
  const { data: s } = useSummary(r.id);
  const Icon = ICON[r.file_type];
  return (
    <li className="card flex flex-col gap-3 p-4">
      <div className="flex items-start gap-3">
        <span className="grid size-10 shrink-0 place-items-center rounded-lg bg-accent-soft text-accent"><Icon className="size-5" /></span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate font-semibold" title={r.name}>{r.name}</h3>
          <p className="truncate text-xs text-muted">{r.filename}</p>
        </div>
        <Chip tone={r.status === "ready" ? "ok" : r.status === "paused" ? "warn" : r.status === "drafting" ? "info" : "muted"}>{r.status}</Chip>
      </div>
      <div>
        <p className="mb-1 flex justify-between text-xs text-muted">
          <span>{s ? `${s.drafted}/${s.total} drafted · ${s.accepted} approved` : `${r.requirement_count} requirements`}</span>
          {r.deadline && <span>due {fmtDate(r.deadline)}</span>}
        </p>
        <ProgressBar done={s?.accepted ?? 0} total={s?.total ?? r.requirement_count} label="Approved answers" />
      </div>
      <div className="flex flex-wrap gap-2">
        {s && s.needs_review > 0 && <Chip tone="warn">{s.needs_review} need review</Chip>}
        {s && s.clarify > 0 && <Chip tone="info">{s.clarify} to clarify</Chip>}
        {s && s.owner > 0 && <Chip>{s.owner} with owners</Chip>}
        {s && s.conflicting > 0 && <Chip tone="bad">{s.conflicting} conflicting</Chip>}
      </div>
      <div className="mt-auto flex gap-2">
        <Link className="btn btn-primary btn-sm" to={`/rfps/${r.id}`}>Open Response Studio</Link>
        <Link className="btn btn-sm" to={`/rfps/${r.id}/clarifications`}>Clarifications</Link>
      </div>
    </li>
  );
}

function Analysis({ id, onClose }: { id: number; onClose: () => void }) {
  const nav = useNavigate();
  const toast = useToast();
  const qc = useQueryClient();
  const { data: rfp } = useRfp(id);
  const kb = useKbStatus();
  const sample = useMutation({
    mutationFn: () => http.post("/kb/index-samples"),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["kbstatus"] }); qc.invalidateQueries({ queryKey: ["kbdocs"] }); toast("ok", "Sample knowledge base loaded"); },
    onError: (e: Error) => toast("bad", e.message),
  });
  const draft = useMutation({
    mutationFn: () => http.post<Job>(`/rfps/${id}/draft`),
    onSuccess: () => nav(`/rfps/${id}`),
    onError: (e: Error) => toast("bad", e.message),
  });
  if (!rfp) return <div className="card flex justify-center p-8"><Spinner /></div>;
  const rep = (rfp as RfpDetail).parse_report;
  const rules = rfp.format_rules;
  const emptyKb = kb.data && kb.data.documents === 0;
  return (
    <section className="card border-accent p-5" aria-label="RFP analysis">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <h2 className="text-base font-bold">I read {rfp.filename}</h2>
          <p className="text-sm text-muted">Here is what I found. Check it before I draft anything.</p>
        </div>
        <button className="btn btn-ghost btn-sm" onClick={onClose}>Dismiss</button>
      </div>
      <div className="grid gap-4 md:grid-cols-3">
        <div>
          <h3 className="h-section mb-1">Requirements</h3>
          <p className="text-2xl font-bold">{rep.requirements}</p>
          <p className="text-xs text-muted">{rep.mandatory} mandatory · {rep.optional} optional{rep.unknown_priority ? ` · ${rep.unknown_priority} priority unclear` : ""}</p>
          <ul className="mt-2 space-y-0.5 text-xs">
            {rep.sections.map((s) => <li key={s.code + s.title} className="flex justify-between gap-2"><span className="truncate">{s.code ? `${s.code}. ` : ""}{s.title}</span><b>{s.count}</b></li>)}
          </ul>
        </div>
        <div>
          <h3 className="h-section mb-1">Formatting rules I detected</h3>
          <ul className="space-y-1 text-sm">
            <li>Words per answer: <b>{rules.word_limit ?? "not stated"}</b></li>
            <li>Compliance wording: <b>{rules.compliance_codes?.length ? rules.compliance_codes.join(" / ") : "not stated"}</b></li>
            {rules.response_scheme?.kind === "capability" && (
              <li>Response codes: {rules.response_scheme.codes.map((c) => <span key={c.code} className="mr-1.5 whitespace-nowrap"><b className="font-mono">{c.code}</b> {c.label}{c.needs.length ? ` (needs ${c.needs.join(", ")})` : ""}{c.klass ? "" : " (not mapped)"}</span>)}</li>
            )}
            <li>File name: <b className="break-all">{rules.file_name ?? "not stated"}</b></li>
            <li>Questions by: <b>{rules.clarification_deadline ? fmtDate(rules.clarification_deadline) : "not stated"}</b></li>
            <li>Submission due: <b>{rules.deadline ? fmtDate(rules.deadline) : "not stated"}</b></li>
          </ul>
          <p className="mt-1 text-xs text-muted">Confirm or correct these when you export.</p>
        </div>
        <div>
          <h3 className="h-section mb-1">Heads up</h3>
          {rep.warnings.length === 0 && rep.low_confidence.length === 0 ? <p className="text-sm text-ok">Every ID, section and order was read cleanly.</p> : (
            <ul className="list-disc space-y-1 pl-4 text-xs text-warn">
              {rep.warnings.slice(0, 5).map((w) => <li key={w}>{w}</li>)}
              {rep.low_confidence.length > 0 && <li>Less certain parse: {rep.low_confidence.slice(0, 6).join(", ")}</li>}
            </ul>
          )}
        </div>
      </div>
      {emptyKb && (
        <div className="mt-4 flex flex-wrap items-center gap-3 rounded-lg bg-warn-bg px-3 py-2 text-sm text-warn" role="alert">
          <BookOpen className="size-4" /> <span className="flex-1">The knowledge base is empty, so there is nothing to ground answers in.</span>
          <button className="btn btn-sm" onClick={() => sample.mutate()} disabled={sample.isPending}>Load the sample (synthetic) knowledge base</button>
          <Link className="btn btn-sm" to="/knowledge">Upload your own</Link>
        </div>
      )}
      <div className="mt-4 flex flex-wrap gap-2">
        <button className="btn btn-primary" disabled={draft.isPending || !!emptyKb} onClick={() => draft.mutate()}>
          {draft.isPending ? <Spinner className="size-4" /> : <Play className="size-4" />} Start drafting
        </button>
        <Link className="btn" to={`/rfps/${id}`}>Open without drafting</Link>
      </div>
    </section>
  );
}

export default function RfpList() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data: rfps, isLoading, error } = useRfps();
  const kb = useKbStatus();
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);
  const [fresh, setFresh] = useState<number | null>(null);

  const upload = useMutation({
    mutationFn: (file: File) => { const fd = new FormData(); fd.append("file", file); return http.post<RfpSummary>("/rfps", fd); },
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["rfps"] }); setFresh(r.id); toast("ok", `Imported ${r.requirement_count} requirements`); },
    onError: (e: Error) => toast("bad", e.message),
  });
  const pick = (f?: File | null) => f && upload.mutate(f);

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Active RFPs</h1>
          <p className="text-sm text-muted">Upload an RFP. I read its structure, draft answers only from your approved sources, and you decide what goes out.</p>
        </div>
        {kb.data && (
          <Link to="/knowledge" className="card flex items-center gap-2 px-3 py-2 text-xs text-muted hover:bg-panel2">
            <BookOpen className="size-4" /> {kb.data.documents} sources · {kb.data.chunks} passages · search: <b className="text-ink">{kb.data.retrieval_mode}</b>
          </Link>
        )}
      </div>

      <label onDragOver={(e) => { e.preventDefault(); setOver(true); }} onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); pick(e.dataTransfer.files[0]); }}
        className={cx("flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed px-6 py-8 text-center transition",
          over ? "border-accent bg-accent-soft" : "border-line hover:bg-panel2")}>
        <input ref={input} type="file" className="sr-only" accept=".xlsx,.xlsm,.docx,.pdf" onChange={(e) => { pick(e.target.files?.[0]); e.target.value = ""; }} />
        {upload.isPending ? <Spinner className="size-7" /> : <UploadCloud className="size-7 text-accent" />}
        <span className="text-sm font-semibold">{upload.isPending ? "Reading the RFP…" : "Drop an RFP here, or click to choose"}</span>
        <span className="text-xs text-muted">Excel, Word or PDF · IDs, sections and order are kept exactly</span>
      </label>

      {fresh && <Analysis id={fresh} onClose={() => setFresh(null)} />}

      {isLoading ? <div className="flex justify-center py-10"><Spinner className="size-6" /></div> : error ? <ErrorNote error={error} /> :
        rfps && rfps.length > 0 ? (
          <ul className="grid gap-4 sm:grid-cols-2 xl:grid-cols-3">{rfps.map((r) => <RfpCard key={r.id} r={r} />)}</ul>
        ) : <Empty title="No RFPs yet">Upload one above to begin. Sample RFPs are in <code>sample_data/rfps</code>.</Empty>}
    </div>
  );
}
