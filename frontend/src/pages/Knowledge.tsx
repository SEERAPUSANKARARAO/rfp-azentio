import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, CheckCircle2, GitCompareArrows, RotateCcw, ScanSearch, Upload } from "lucide-react";
import { http, type Conflict, type ConflictSide, type KbDoc } from "../api";
import { useConflicts, useKbDocs, useKbStatus, useReviewer } from "../hooks";
import { Chip, Empty, ErrorNote, Spinner, Modal, cx, fmtDate, useToast } from "../ui";

const KINDS = ["product_doc", "sow", "past_rfp", "security_doc", "policy"];

function Tile({ label, value, tone }: { label: string; value: string | number; tone?: "bad" | "warn" | "ok" }) {
  return (
    <div className={cx("card px-4 py-3", tone === "bad" && "border-bad", tone === "warn" && "border-warn")}>
      <p className="text-xs text-muted">{label}</p>
      <p className={cx("text-2xl font-bold", tone === "bad" && "text-bad", tone === "warn" && "text-warn")}>{value}</p>
    </div>
  );
}

function Side({ tag, side, tone, canonical }: { tag: string; side: ConflictSide; tone: "stale" | "current"; canonical: boolean }) {
  return (
    <div className={cx("rounded-xl border-2 p-3", tone === "stale" ? "border-bad/50 bg-bad-bg/40" : "border-ok/50 bg-ok-bg/40")}>
      <p className="mb-1 flex flex-wrap items-center gap-2 text-xs font-bold">
        <span className={cx("grid size-5 place-items-center rounded", tone === "stale" ? "bg-bad text-on-solid" : "bg-ok text-on-solid")}>{tag}</span>
        {tone === "stale" ? "Older" : "Newer"} · {fmtDate(side.date)}{side.version ? ` · v${side.version}` : ""}
        {canonical && <Chip tone="ok"><CheckCircle2 className="size-3" /> canonical</Chip>}
        {side.stale_reasons.length > 0 && <Chip tone="bad" title={side.stale_reasons.join("; ")}>stale</Chip>}
      </p>
      <p className="text-sm font-semibold">{side.title}</p>
      <p className="mb-1 text-xs text-muted">{side.location}</p>
      <p className="text-sm whitespace-pre-line">{side.text.split("\n").slice(1).join(" ") || side.text}</p>
    </div>
  );
}

function ConflictCard({ c }: { c: Conflict }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reviewer] = useReviewer();
  const refresh = () => ["conflicts", "kbdocs", "rfps"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const resolve = useMutation({
    mutationFn: (doc: number) => http.post<{ affected: unknown[]; recalculation: { skipped?: string; merged?: boolean }[] }>(`/kb/conflicts/${c.id}/resolve`, { canonical_doc_id: doc, reviewer, recalculate: true }),
    onSuccess: (r) => {
      refresh();
      const skipped = r.recalculation.find((x) => x.skipped);
      const joined = r.recalculation.some((x: { merged?: boolean }) => x.merged);
      toast(skipped ? "info" : "ok", r.affected.length === 0
        ? "Resolved. No drafted answer relied on this."
        : `Resolved. ${r.affected.length} answer${r.affected.length === 1 ? " is" : "s are"} being redrafted${joined ? " (joined the run in progress)" : ""}${skipped ? ` (${skipped.skipped})` : ""}; approvals on ${r.affected.length === 1 ? "it was" : "them were"} withdrawn.`);
    },
    onError: (e: Error) => toast("bad", e.message),
  });
  const reopen = useMutation({ mutationFn: () => http.post(`/kb/conflicts/${c.id}/reopen`, { reviewer }), onSuccess: refresh });
  const resolved = c.status === "resolved";
  return (
    <li className="card p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <GitCompareArrows className={cx("size-4", resolved ? "text-ok" : "text-bad")} />
        <h3 className="font-semibold">{c.topic}</h3>
        <Chip tone={resolved ? "ok" : "bad"}>{resolved ? "resolved" : "needs a decision"}</Chip>
        {c.affected.length > 0 && <Chip tone="warn">{c.affected.length} answer{c.affected.length === 1 ? "" : "s"} affected</Chip>}
      </div>
      <p className="mb-3 text-sm text-muted">{c.detail}</p>
      <div className="grid gap-3 md:grid-cols-2">
        <Side tag="A" side={c.older} tone="stale" canonical={c.canonical_doc_id === c.older.doc_id} />
        <Side tag="B" side={c.newer} tone="current" canonical={c.canonical_doc_id === c.newer.doc_id} />
      </div>
      <div className="mt-3 flex flex-wrap items-center gap-2">
        {resolved ? (
          <button className="btn btn-sm" onClick={() => reopen.mutate()} disabled={reopen.isPending}><RotateCcw className="size-3.5" /> Reopen</button>
        ) : (
          <>
            <button className="btn btn-primary" onClick={() => resolve.mutate(c.newer.doc_id)} disabled={resolve.isPending}>
              {resolve.isPending && <Spinner className="size-4" />} Mark B canonical{c.affected.length > 0 ? ` & redraft ${c.affected.length} affected answer${c.affected.length === 1 ? "" : "s"}` : ""}
            </button>
            <button className="btn btn-sm" onClick={() => resolve.mutate(c.older.doc_id)} disabled={resolve.isPending}>Mark A canonical instead</button>
            <span className="text-xs text-muted">The other passage stops being used. Approvals on affected answers are withdrawn.</span>
          </>
        )}
      </div>
      {c.affected.length > 0 && <p className="mt-2 text-xs text-muted">Affects: {c.affected.map((a) => a.req_id).join(", ")}</p>}
    </li>
  );
}

type UploadItem = {
  file: File; title: string; kind: string; doc_date: string; version: string; customer: string; owner: string;
  reuse_permitted: boolean; answerable: boolean;
  state: "ready" | "sending" | "done" | "failed"; note: string; warnings: string[];
};

type UploadResult = { action: string; chunks: number; warnings: string[]; needs_conflict_check: boolean };

// A first guess from the file name; the person can change it before anything is sent.
function guessKind(name: string): string {
  const n = name.toLowerCase();
  if (/past[_ -]?rfp|rfp[_ -]?response/.test(n)) return "past_rfp";
  if (/(^|[^a-z])sow([^a-z]|$)|statement[_ -]of[_ -]work/.test(n)) return "sow";
  if (/security|pentest|soc2|cert/.test(n)) return "security_doc";
  if (/policy|residency|(^|[_ -])dr[_ -]|commercial|legal/.test(n)) return "policy";
  return "product_doc";
}

function newItem(file: File): UploadItem {
  return { file, title: "", kind: guessKind(file.name), doc_date: "", version: "", customer: "", owner: "",
           reuse_permitted: true, answerable: true, state: "ready", note: "", warnings: [] };
}

function UploadForm({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reviewer] = useReviewer();
  const [items, setItems] = useState<UploadItem[]>([]);
  const [busy, setBusy] = useState(false);
  const [finished, setFinished] = useState(false);

  const patch = (i: number, p: Partial<UploadItem>) => setItems((xs) => xs.map((x, j) => (j === i ? { ...x, ...p } : x)));
  const pick = (files: FileList | null) => {
    if (!files) return;
    setItems((xs) => {
      const known = new Set(xs.map((x) => `${x.file.name}:${x.file.size}`));
      return [...xs, ...Array.from(files).filter((f) => !known.has(`${f.name}:${f.size}`)).map(newItem)];
    });
    setFinished(false);
  };
  const applyToAll = () => setItems((xs) => xs.map((x, j) => j === 0 || x.state === "done" ? x : {
    ...x, kind: xs[0].kind, doc_date: xs[0].doc_date, version: xs[0].version, owner: xs[0].owner,
    reuse_permitted: xs[0].reuse_permitted, answerable: xs[0].answerable }));

  // One request per file, in order: indexing writes to one SQLite database, so there is nothing to gain from racing.
  const run = async () => {
    setBusy(true);
    let added = 0, failed = 0, check = false, warned = false;
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.state === "done") continue;
      patch(i, { state: "sending", note: "", warnings: [] });
      try {
        const fd = new FormData();
        fd.append("file", it.file);
        (["title", "kind", "doc_date", "version", "customer", "owner"] as const).forEach((k) => fd.append(k, it[k]));
        fd.append("reuse_permitted", String(it.reuse_permitted));
        fd.append("answerable", String(it.answerable));
        fd.append("reviewer", reviewer);
        const r = await http.post<UploadResult>("/kb/documents", fd);
        patch(i, { state: "done", note: `${r.chunks} passages indexed`, warnings: r.warnings });
        added++; check = check || r.needs_conflict_check; warned = warned || r.warnings.length > 0;
      } catch (e) {
        patch(i, { state: "failed", note: (e as Error).message });
        failed++;
      }
    }
    setBusy(false);
    setFinished(true);
    ["kbdocs", "kbstatus"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
    if (added) toast(failed ? "bad" : "ok", `${added} source${added === 1 ? "" : "s"} added${failed ? `, ${failed} failed` : ""}${check ? ". Run conflict detection to check them against the existing sources." : ""}`);
    else if (failed) toast("bad", `${failed} upload${failed === 1 ? "" : "s"} failed`);
    if (items.length === 1 && added === 1 && !warned) onClose();
  };

  const pending = items.filter((x) => x.state !== "done").length;
  return (
    <Modal title="Add sources to the knowledge base" onClose={onClose} wide>
      <label className="label" htmlFor="kf">Files (Word, PDF, Markdown or text). You can choose several at once.</label>
      <input id="kf" type="file" multiple accept=".docx,.pdf,.md,.txt" className="input" disabled={busy}
             onChange={(e) => { pick(e.target.files); e.target.value = ""; }} />
      {items.length > 1 && !busy && (
        <p className="mt-2 text-xs text-muted">Each file has its own date, version and customer.{" "}
          <button className="underline" onClick={applyToAll}>Copy kind, date, version, owner and switches from the first file to all</button></p>
      )}
      <ul className="mt-3 space-y-3">
        {items.map((it, i) => {
          const locked = busy || it.state === "done";
          const id = (k: string) => `${k}-${i}`;
          return (
            <li key={`${it.file.name}:${it.file.size}`} className="rounded-lg border border-line p-3">
              <div className="mb-2 flex items-center justify-between gap-2">
                <p className="truncate text-sm font-medium" title={it.file.name}>{it.file.name}</p>
                <span className="flex shrink-0 items-center gap-2">
                  {it.state === "sending" && <Chip tone="info"><Spinner className="size-3" /> uploading</Chip>}
                  {it.state === "done" && <Chip tone="ok">added</Chip>}
                  {it.state === "failed" && <Chip tone="bad">failed</Chip>}
                  {!locked && <button className="btn btn-ghost btn-sm" aria-label={`Remove ${it.file.name}`} onClick={() => setItems((xs) => xs.filter((_, j) => j !== i))}>Remove</button>}
                </span>
              </div>
              <div className="grid gap-2 sm:grid-cols-4">
                <div className="sm:col-span-4"><label className="label" htmlFor={id("kt")}>Title</label>
                  <input id={id("kt")} className="input" disabled={locked} value={it.title} onChange={(e) => patch(i, { title: e.target.value })} placeholder="defaults to the file name" /></div>
                <div><label className="label" htmlFor={id("kk")}>Kind</label>
                  <select id={id("kk")} className="input" disabled={locked} value={it.kind} onChange={(e) => patch(i, { kind: e.target.value })}>{KINDS.map((k) => <option key={k} value={k}>{k.replace("_", " ")}</option>)}</select></div>
                <div><label className="label" htmlFor={id("kd")}>Document date</label>
                  <input id={id("kd")} type="date" className="input" disabled={locked} value={it.doc_date} onChange={(e) => patch(i, { doc_date: e.target.value })} /></div>
                <div><label className="label" htmlFor={id("kv")}>Product version</label>
                  <input id={id("kv")} className="input" disabled={locked} value={it.version} onChange={(e) => patch(i, { version: e.target.value })} placeholder="e.g. 6.2" /></div>
                <div><label className="label" htmlFor={id("ko")}>Owner</label>
                  <input id={id("ko")} className="input" disabled={locked} value={it.owner} onChange={(e) => patch(i, { owner: e.target.value })} /></div>
                <div className="sm:col-span-4"><label className="label" htmlFor={id("kc")}>Customer name (redacted wherever it appears)</label>
                  <input id={id("kc")} className="input" disabled={locked} value={it.customer} onChange={(e) => patch(i, { customer: e.target.value })} placeholder="only for past RFP responses and SOWs" /></div>
                <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" disabled={locked} checked={it.reuse_permitted} onChange={(e) => patch(i, { reuse_permitted: e.target.checked })} /> Reuse permitted</label>
                <label className="flex items-center gap-2 text-sm sm:col-span-2"><input type="checkbox" disabled={locked} checked={it.answerable} onChange={(e) => patch(i, { answerable: e.target.checked })} /> May be used to draft answers</label>
              </div>
              {it.state === "ready" && !it.doc_date && <p className="mt-2 text-xs text-muted">No date yet: without one, freshness cannot be checked for this source.</p>}
              {it.state === "done" && <p className="mt-2 text-xs text-ok">{it.note}</p>}
              {it.state === "failed" && <p className="mt-2 text-xs text-bad" role="alert">{it.note}</p>}
              {it.warnings.length > 0 && <ul className="mt-2 list-disc space-y-1 rounded-lg bg-warn-bg py-2 pr-3 pl-7 text-xs text-warn">{it.warnings.map((w) => <li key={w}>{w}</li>)}</ul>}
            </li>
          );
        })}
      </ul>
      <div className="mt-4 flex justify-end gap-2">
        <button className="btn" onClick={onClose}>{finished ? "Done" : "Cancel"}</button>
        {pending > 0 && (
          <button className="btn btn-primary" disabled={busy || items.length === 0} onClick={run}>
            {busy ? <Spinner className="size-4" /> : <Upload className="size-4" />}
            {busy ? "Uploading…" : items.length > 1 ? `Add ${pending} sources` : "Add source"}
          </button>
        )}
      </div>
    </Modal>
  );
}

function DocRow({ d }: { d: KbDoc }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reviewer] = useReviewer();
  const refresh = () => ["kbdocs", "conflicts"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
  const patch = useMutation({ mutationFn: (b: object) => http.patch(`/kb/documents/${d.id}`, { ...b, reviewer }), onSuccess: refresh, onError: (e: Error) => toast("bad", e.message) });
  const del = useMutation({ mutationFn: () => http.del(`/kb/documents/${d.id}?reviewer=${encodeURIComponent(reviewer)}`), onSuccess: () => { refresh(); toast("ok", "Removed"); }, onError: (e: Error) => toast("bad", e.message) });
  return (
    <tr className="border-b border-line/60 last:border-0 align-top">
      <td className="px-3 py-2"><p className="font-medium">{d.title}</p><p className="text-xs text-muted">{d.kind.replace("_", " ")} · {d.chunks} passages{d.owner ? ` · ${d.owner}` : ""}</p></td>
      <td className="px-3 py-2 whitespace-nowrap text-muted">{fmtDate(d.doc_date)}{d.version ? ` · v${d.version}` : ""}</td>
      <td className="px-3 py-2">
        <div className="flex flex-wrap gap-1">
          {d.stale_reasons.length > 0 && <Chip tone="bad" title={d.stale_reasons.join("; ")}><AlertTriangle className="size-3" /> stale</Chip>}
          {d.status === "deprecated" && <Chip tone="warn">deprecated</Chip>}
          {d.canonical && <Chip tone="ok">canonical</Chip>}
          {!d.reuse_permitted && <Chip tone="bad">never reused</Chip>}
          {!d.answerable && <Chip>not for answers</Chip>}
          {d.customer_redacted && <Chip tone="info">redacted</Chip>}
          <Chip tone={d.label === "SAMPLE" ? "muted" : "info"}>{d.label.toLowerCase()}</Chip>
        </div>
      </td>
      <td className="px-3 py-2 text-right whitespace-nowrap">
        <button className="btn btn-sm" onClick={() => patch.mutate({ status: d.status === "deprecated" ? "approved" : "deprecated" })}>{d.status === "deprecated" ? "Approve" : "Deprecate"}</button>{" "}
        <button className="btn btn-sm btn-danger" onClick={() => confirm(`Delete "${d.title}"?`) && del.mutate()}>Delete</button>
      </td>
    </tr>
  );
}

export default function Knowledge() {
  const qc = useQueryClient();
  const toast = useToast();
  const status = useKbStatus();
  const docs = useKbDocs();
  const conflicts = useConflicts();
  const [adding, setAdding] = useState(false);
  const detect = useMutation({
    mutationFn: () => http.post<{ candidate_pairs: number; contradictions: number; judge_failures: number }>("/kb/detect-conflicts"),
    onSuccess: (r) => { qc.invalidateQueries({ queryKey: ["conflicts"] }); toast(r.judge_failures ? "info" : "ok", `Compared ${r.candidate_pairs} pairs: ${r.contradictions} contradiction(s)${r.judge_failures ? `, ${r.judge_failures} could not be judged` : ""}`); },
    onError: (e: Error) => toast("bad", e.message),
  });
  const sample = useMutation({
    mutationFn: () => http.post("/kb/index-samples"),
    onSuccess: () => { ["kbdocs", "kbstatus"].forEach((k) => qc.invalidateQueries({ queryKey: [k] })); toast("ok", "Sample knowledge base loaded"); },
    onError: (e: Error) => toast("bad", e.message),
  });

  const list = docs.data ?? [];
  const open = (conflicts.data ?? []).filter((c) => c.status === "open");
  const resolved = (conflicts.data ?? []).filter((c) => c.status === "resolved");
  const stale = list.filter((d) => d.stale_reasons.length > 0).length;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Knowledge base &amp; sources</h1>
          <p className="text-sm text-muted">Answers are drafted only from these. Customer names are redacted when a source is added.</p>
        </div>
        <div className="flex gap-2">
          <button className="btn" onClick={() => detect.mutate()} disabled={detect.isPending || list.length === 0}>{detect.isPending ? <Spinner className="size-4" /> : <ScanSearch className="size-4" />} Detect conflicts</button>
          <button className="btn btn-primary" onClick={() => setAdding(true)}><Upload className="size-4" /> Add source</button>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-3 lg:grid-cols-5">
        <Tile label="Sources" value={status.data?.documents ?? list.length} />
        <Tile label="Searchable passages" value={status.data?.chunks ?? "-"} />
        <Tile label="Stale sources" value={stale} tone={stale ? "warn" : undefined} />
        <Tile label="Open conflicts" value={open.length} tone={open.length ? "bad" : undefined} />
        <Tile label="Search mode" value={status.data?.retrieval_mode ?? "-"} />
      </div>

      <section aria-label="Conflict and stale data detector">
        <h2 className="mb-2 text-sm font-semibold">Knowledge conflict &amp; stale-data detector</h2>
        {conflicts.error ? <ErrorNote error={conflicts.error} /> : (conflicts.data ?? []).length === 0 ? (
          <div className="card p-4 text-sm text-muted">No contradictions recorded. Run <b>Detect conflicts</b> after adding sources: a model compares passages that talk about the same thing and the SME settles any it finds.</div>
        ) : (
          <ul className="space-y-3">{[...open, ...resolved].map((c) => <ConflictCard key={c.id} c={c} />)}</ul>
        )}
      </section>

      <section aria-label="Sources">
        <h2 className="mb-2 text-sm font-semibold">Sources</h2>
        {docs.isLoading ? <Spinner /> : docs.error ? <ErrorNote error={docs.error} /> : list.length === 0 ? (
          <Empty title="The knowledge base is empty">
            <p>Add your own sources, or load the synthetic sample set (clearly labelled as sample) to try the tool.</p>
            <button className="btn mt-3" onClick={() => sample.mutate()} disabled={sample.isPending}>Load sample knowledge base</button>
          </Empty>
        ) : (
          <div className="card overflow-x-auto">
            <table className="w-full text-sm">
              <thead><tr className="border-b border-line text-left text-xs text-muted"><th className="px-3 py-2">Source</th><th className="px-3 py-2">Dated</th><th className="px-3 py-2">Status</th><th className="px-3 py-2" /></tr></thead>
              <tbody>{list.map((d) => <DocRow key={d.id} d={d} />)}</tbody>
            </table>
          </div>
        )}
      </section>
      {adding && <UploadForm onClose={() => setAdding(false)} />}
    </div>
  );
}
