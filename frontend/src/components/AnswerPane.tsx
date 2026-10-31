import { Fragment, useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Flag, Lightbulb, Pencil, RefreshCw, Undo2, UserCheck } from "lucide-react";
import { http, type Answer, type ComplianceStatus, type FormatRules } from "../api";
import { replaceAnswer, useAssignees, useReviewer } from "../hooks";
import { Chip, ErrorNote, Modal, Spinner, StatusChip, cx, useToast } from "../ui";

const STATUSES: ComplianceStatus[] = ["Complied", "Partially Complied", "Not Complied", "Cannot Confirm"];
const wordCount = (t: string) => t.replace(/\[\d+\]/g, "").split(/\s+/).filter((w) => /\w/.test(w)).length;

function WithCitations({ text, answer, active, onMarker }: { text: string; answer: Answer; active: number | null; onMarker: (n: number | null) => void }) {
  return (
    <p className="text-[15px] leading-relaxed whitespace-pre-wrap">
      {text.split(/(\[\d+\])/g).map((part, i) => {
        const m = /^\[(\d+)\]$/.exec(part);
        if (!m) return <Fragment key={i}>{part}</Fragment>;
        const n = Number(m[1]);
        const c = answer.citations.find((x) => x.marker === n);
        if (!c) return <Fragment key={i}>{part}</Fragment>;
        return (
          <button key={i} onClick={() => onMarker(active === n ? null : n)} title={`${c.doc_title}, ${c.location}`}
            aria-label={`Source ${n}: ${c.doc_title}`}
            className={cx("mx-0.5 inline-flex min-w-6 items-center justify-center rounded-md px-1.5 text-xs font-semibold align-baseline transition",
              active === n ? "bg-accent text-on-solid" : "bg-accent-soft text-accent hover:bg-accent hover:text-on-solid")}>
            {n}
          </button>
        );
      })}
    </p>
  );
}

export default function AnswerPane({ rfpId, answer, rules, activeMarker, onMarker, onAcceptedNext }: {
  rfpId: number; answer: Answer | null; rules: FormatRules | undefined; activeMarker: number | null;
  onMarker: (n: number | null) => void; onAcceptedNext: () => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reviewer] = useReviewer();
  const assignees = useAssignees(rfpId);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState("");
  const [regenOpen, setRegenOpen] = useState(false);
  const [instruction, setInstruction] = useState("");
  const [flagOpen, setFlagOpen] = useState(false);
  const [assignee, setAssignee] = useState("");
  const [note, setNote] = useState("");
  const [factOpen, setFactOpen] = useState(false);
  const [fact, setFact] = useState("");
  const [redraft, setRedraft] = useState(true);

  const rid = answer?.requirement_id;
  useEffect(() => { setEditing(false); setRegenOpen(false); }, [rid]);

  const base = `/rfps/${rfpId}/answers/${rid}`;
  const done = (a: Answer) => replaceAnswer(qc, rfpId, a);
  const fail = (e: Error) => toast("bad", e.message);

  const accept = useMutation({
    mutationFn: () => http.post<Answer>(`${base}/accept`, { reviewer }),
    onSuccess: (a) => { done(a); toast("ok", `${a.req_id} approved`); onAcceptedNext(); }, onError: fail,
  });
  const edit = useMutation({
    mutationFn: (text: string) => http.post<Answer>(`${base}/edit`, { text, reviewer }),
    onSuccess: (a) => {
      done(a); setEditing(false);
      toast(a.flags.includes("se_provided") ? "info" : "ok",
        a.flags.includes("se_provided") ? "Saved. It states figures no cited source does, so it is marked as your own facts." : "Saved. Approval withdrawn: accept again when ready.");
    }, onError: fail,
  });
  const revert = useMutation({ mutationFn: () => http.post<Answer>(`${base}/revert`, { reviewer }), onSuccess: (a) => { done(a); toast("ok", "Back to the agent's draft"); }, onError: fail });
  const setStatus = useMutation({ mutationFn: (status: string) => http.post<Answer>(`${base}/status`, { status, reviewer }), onSuccess: done, onError: fail });
  const regen = useMutation({
    mutationFn: () => http.post<Answer>(`${base}/regenerate`, { instruction: instruction.trim() || null, reviewer }),
    onSuccess: (a) => { done(a); setRegenOpen(false); setInstruction(""); toast("ok", "Redrafted. Review it before accepting."); }, onError: fail,
  });
  const flag = useMutation({
    mutationFn: (to: string | null) => http.post<Answer>(`${base}/flag`, { assignee: to, note, reviewer }),
    onSuccess: (a) => { done(a); setFlagOpen(false); setNote(""); toast("ok", a.assigned_to ? `Handed to ${a.assigned_to}` : "Taken back"); }, onError: fail,
  });
  const addFact = useMutation({
    mutationFn: () => http.post<Answer>(`${base}/facts`, { text: fact, regenerate: redraft, reviewer }),
    onSuccess: (a) => { done(a); setFactOpen(false); setFact(""); toast("ok", "Fact saved as an SE-provided source" + (redraft ? " and the answer was redrafted" : "")); }, onError: fail,
  });
  const busy = [accept, edit, revert, setStatus, regen, flag, addFact].some((m) => m.isPending);

  if (!answer) return <section className="card flex h-full items-center justify-center p-8 text-sm text-muted">Select a question from the queue.</section>;

  const limit = rules?.word_limit ?? null;
  const words = wordCount(editing ? draft : answer.text);
  const over = limit !== null && words > limit;
  const queued = answer.review_status === "queued";
  const ownerRouted = answer.triage === "owner";
  const approved = answer.review_status === "accepted";

  return (
    <section aria-label="Answer" className="card flex h-full min-h-0 flex-col">
      <div className="min-h-0 flex-1 space-y-4 overflow-y-auto p-5">
        <div className="rounded-xl border border-line bg-panel2 p-4">
          <div className="mb-1.5 flex flex-wrap items-center gap-2">
            <span className="text-sm font-bold">{answer.req_id}</span>
            <Chip tone={answer.mandatory === false ? "muted" : "info"}>{answer.mandatory === false ? "Optional" : "Mandatory"}</Chip>
            <span className="text-xs text-muted">{answer.section_code ? `${answer.section_code}. ` : ""}{answer.section_title}</span>
          </div>
          <p className="text-[15px] font-medium">{answer.requirement}</p>
        </div>

        {answer.error && <ErrorNote error={new Error(`Drafting failed: ${answer.error}`)} />}

        {!queued && (
          <div className="flex flex-wrap items-center gap-2">
            <span className="text-xs font-medium text-muted">Status</span>
            {ownerRouted && answer.status === "Pending" ? <Chip tone="warn">Awaiting {answer.assigned_to}</Chip> : <StatusChip status={answer.status} />}
            <select aria-label="Change compliance status" className="input !w-auto !py-1 text-xs" value="" disabled={busy}
              onChange={(e) => e.target.value && setStatus.mutate(e.target.value)}>
              <option value="">Change status…</option>
              {STATUSES.filter((s) => s !== answer.status).map((s) => <option key={s}>{s}</option>)}
            </select>
            {approved && <Chip tone="ok"><Check className="size-3" /> Approved</Chip>}
            {answer.edited && <Chip tone="info">Edited</Chip>}
          </div>
        )}

        <div>
          <div className="mb-1.5 flex items-center justify-between">
            <h3 className="h-section">{ownerRouted && !answer.text ? "Answer (to be written by the owner)" : "Drafted response"}</h3>
            {(answer.text || editing) && (
              <span className={cx("text-xs", over ? "font-semibold text-bad" : "text-muted")} aria-live="polite">
                {words}{limit ? ` / ${limit}` : ""} words{over ? ` · ${words - (limit ?? 0)} over the RFP's limit` : ""}
              </span>
            )}
          </div>
          {queued ? (
            <p className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">Not drafted yet. Use <b>Draft answers</b> in the header.</p>
          ) : editing ? (
            <>
              <textarea autoFocus value={draft} onChange={(e) => setDraft(e.target.value)} rows={9} className="input leading-relaxed" aria-label="Edit answer" />
              <p className="mt-1 text-xs text-muted">Editing withdraws any approval. Keep the [n] markers so sources stay attached to the statements.</p>
            </>
          ) : answer.text ? (
            <div className="rounded-xl border border-line p-4">
              <WithCitations text={answer.text} answer={answer} active={activeMarker} onMarker={onMarker} />
            </div>
          ) : (
            <p className="rounded-lg border border-dashed border-line p-6 text-center text-sm text-muted">
              {ownerRouted ? `This goes to ${answer.assigned_to}. You can write the answer yourself, or leave it with them.` : "No answer text."}
            </p>
          )}
        </div>

        {regenOpen && (
          <div className="rounded-xl border border-line bg-panel2 p-3">
            <label className="label" htmlFor="instr">Wording instruction (optional). It changes style only, never the sourcing rules.</label>
            <input id="instr" className="input" value={instruction} onChange={(e) => setInstruction(e.target.value)} placeholder="e.g. shorter, more formal, lead with the limitation" />
            <div className="mt-2 flex flex-wrap items-center gap-1.5">
              {["Shorter", "More formal", "Plain language"].map((p) => <button key={p} className="btn btn-sm" onClick={() => setInstruction(p)}>{p}</button>)}
              <span className="flex-1" />
              <button className="btn btn-sm" onClick={() => setRegenOpen(false)}>Cancel</button>
              <button className="btn btn-primary btn-sm" disabled={busy} onClick={() => regen.mutate()}>
                {regen.isPending ? <><Spinner className="size-3.5" /> Redrafting…</> : "Redraft"}
              </button>
            </div>
          </div>
        )}
      </div>

      {!queued && (
        <div className="border-t border-line p-3">
          {editing ? (
            <div className="flex items-center justify-end gap-2">
              <button className="btn" onClick={() => setEditing(false)}>Cancel</button>
              <button className="btn btn-primary" disabled={busy || !draft.trim() || draft.trim() === answer.text}
                onClick={() => edit.mutate(draft)}>{edit.isPending ? <Spinner className="size-4" /> : <Check className="size-4" />} Save edit</button>
            </div>
          ) : (
            <div className="flex flex-wrap items-center gap-2">
              <button className="btn btn-primary" disabled={busy || !answer.text.trim() || !!answer.error || approved}
                onClick={() => accept.mutate()} title="Approve this answer and go to the next (Tab or Ctrl+Enter)">
                {accept.isPending ? <Spinner className="size-4" /> : <Check className="size-4" />} {approved ? "Approved" : "Accept & next"}
                {!approved && <kbd className="ml-1 rounded bg-on-solid/20 px-1 text-[10px]">Tab</kbd>}
              </button>
              <button className="btn" disabled={busy} onClick={() => { setDraft(answer.text); setEditing(true); }}><Pencil className="size-4" /> {answer.text ? "Edit" : "Write answer"}</button>
              {!ownerRouted && <button className="btn" disabled={busy} onClick={() => setRegenOpen((v) => !v)} aria-expanded={regenOpen}><RefreshCw className="size-4" /> Regenerate</button>}
              <button className="btn" disabled={busy} onClick={() => setFactOpen(true)} title="Supply a fact the sources lack"><Lightbulb className="size-4" /> Add fact</button>
              {answer.assigned_to && answer.triage !== "owner" ? (
                <button className="btn" disabled={busy} onClick={() => flag.mutate(null)}><UserCheck className="size-4" /> Take back from {answer.assigned_to}</button>
              ) : (
                <button className="btn" disabled={busy} onClick={() => setFlagOpen(true)}><Flag className="size-4" /> Flag for…</button>
              )}
              {answer.edited && <button className="btn btn-ghost" disabled={busy} onClick={() => revert.mutate()}><Undo2 className="size-4" /> Revert edit</button>}
              <span className="ml-auto hidden text-[11px] text-muted xl:inline">Tab accept · J/K move · E edit</span>
            </div>
          )}
        </div>
      )}

      {flagOpen && (
        <Modal title={`Flag ${answer.req_id} for someone else`} onClose={() => setFlagOpen(false)}>
          <label className="label" htmlFor="asg">Who should look at this?</label>
          <select id="asg" className="input" value={assignee} onChange={(e) => setAssignee(e.target.value)}>
            <option value="">Choose…</option>
            {(assignees.data ?? []).map((a) => <option key={a}>{a}</option>)}
          </select>
          <label className="label mt-3" htmlFor="note">Note for them</label>
          <textarea id="note" className="input" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="What do you need them to confirm?" />
          <p className="mt-2 text-xs text-muted">A handed-off answer is not final: any approval is withdrawn.</p>
          <div className="mt-4 flex justify-end gap-2">
            <button className="btn" onClick={() => setFlagOpen(false)}>Cancel</button>
            <button className="btn btn-primary" disabled={!assignee || flag.isPending} onClick={() => flag.mutate(assignee)}>Flag</button>
          </div>
        </Modal>
      )}

      {factOpen && (
        <Modal title={`Add a fact for ${answer.req_id}`} onClose={() => setFactOpen(false)}>
          <p className="mb-3 text-sm text-muted">Use this when you know something the knowledge base does not say. It is saved as a dated source in your name and cited as <b>SE-provided</b>, so a reader can tell it from approved documentation.</p>
          <label className="label" htmlFor="fact">The fact (one or two plain sentences)</label>
          <textarea id="fact" autoFocus className="input" rows={4} value={fact} onChange={(e) => setFact(e.target.value)} />
          <label className="mt-3 flex items-center gap-2 text-sm"><input type="checkbox" checked={redraft} onChange={(e) => setRedraft(e.target.checked)} /> Redraft this answer using it</label>
          {addFact.error && <div className="mt-3"><ErrorNote error={addFact.error} /></div>}
          <div className="mt-4 flex justify-end gap-2">
            <button className="btn" onClick={() => setFactOpen(false)}>Cancel</button>
            <button className="btn btn-primary" disabled={fact.trim().length < 12 || addFact.isPending} onClick={() => addFact.mutate()}>
              {addFact.isPending ? <><Spinner className="size-4" /> Saving…</> : "Save fact"}
            </button>
          </div>
        </Modal>
      )}
    </section>
  );
}
