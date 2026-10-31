import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Check, Plus, Trash2, X } from "lucide-react";
import { http, type Clarification } from "../api";
import { refreshAnalysis, useClarifications, useReviewer, useRfp } from "../hooks";
import { Chip, Empty, ErrorNote, Spinner, cx, useToast } from "../ui";

function Row({ rfpId, c }: { rfpId: number; c: Clarification }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reviewer] = useReviewer();
  const [text, setText] = useState(c.text);
  const [reply, setReply] = useState(c.answer ?? "");
  const roles = useQuery({ queryKey: ["assignees", rfpId], queryFn: () => http.get<string[]>(`/rfps/${rfpId}/assignees`) });
  const refresh = () => { qc.invalidateQueries({ queryKey: ["clar", rfpId] }); refreshAnalysis(qc, rfpId); };
  const patch = useMutation({
    mutationFn: (b: Partial<Clarification>) => http.patch<Clarification>(`/rfps/${rfpId}/clarifications/${c.id}`, { ...b, reviewer }),
    onSuccess: refresh, onError: (e: Error) => toast("bad", e.message),
  });
  const del = useMutation({ mutationFn: () => http.del(`/rfps/${rfpId}/clarifications/${c.id}?reviewer=${encodeURIComponent(reviewer)}`), onSuccess: refresh });
  const dirty = text.trim() !== c.text;
  return (
    <li className={cx("card p-4", c.status === "dismissed" && "opacity-60")}>
      <div className="mb-2 flex flex-wrap items-center gap-2">
        {c.requirement_ids.map((r) => <Chip key={r} tone="info">{r}</Chip>)}
        <Chip tone={c.priority === "high" ? "bad" : c.priority === "medium" ? "warn" : "muted"} title="How much the answer could change design, delivery or price">{c.priority} priority</Chip>
        {c.impact && <Chip tone="muted">{c.impact}</Chip>}
        {c.source !== "missing_evidence" && <Chip tone="warn">{c.source === "contradiction" ? "requirements contradict" : "requirement unclear"}</Chip>}
        <Chip tone={c.status === "approved" || c.status === "answered" ? "ok" : c.status === "dismissed" ? "muted" : "warn"}>{c.status === "open" ? "needs a decision" : c.status}</Chip>
        <label className="ml-auto flex items-center gap-1.5 text-xs text-muted">Owner
          <select aria-label="Owner of this question" className="input !w-44 !py-0.5" value={c.assigned_to ?? ""} onChange={(e) => patch.mutate({ assigned_to: e.target.value })}>
            <option value="">Unassigned</option>{(roles.data ?? []).map((r) => <option key={r} value={r}>{r}</option>)}</select></label>
      </div>
      <label className="sr-only" htmlFor={`q${c.id}`}>Question</label>
      <textarea id={`q${c.id}`} className="input" rows={2} value={text} onChange={(e) => setText(e.target.value)} />
      {c.reason && <p className="mt-1.5 text-xs text-muted"><b>Why we are asking:</b> {c.reason}</p>}
      <div className="mt-2">
        <label className="label" htmlFor={`a${c.id}`}>The customer's reply</label>
        <textarea id={`a${c.id}`} className="input" rows={2} value={reply} onChange={(e) => setReply(e.target.value)} placeholder="Paste or type what they said. It becomes part of the requirement when answers are redrafted." />
        {reply.trim() !== (c.answer ?? "") && <button className="btn btn-sm mt-1.5" disabled={!reply.trim()} onClick={() => patch.mutate({ answer: reply.trim() })}>Record the reply</button>}
      </div>
      <div className="mt-3 flex flex-wrap gap-2">
        {dirty && <button className="btn btn-sm btn-primary" onClick={() => patch.mutate({ text })}>Save wording</button>}
        {c.status !== "approved" && <button className="btn btn-sm" onClick={() => patch.mutate({ status: "approved", ...(dirty ? { text } : {}) })}><Check className="size-3.5" /> Approve for the customer</button>}
        {c.status !== "dismissed" ? <button className="btn btn-sm" onClick={() => patch.mutate({ status: "dismissed" })}><X className="size-3.5" /> Dismiss</button>
          : <button className="btn btn-sm" onClick={() => patch.mutate({ status: "open" })}>Reopen</button>}
        <button className="btn btn-sm btn-danger ml-auto" onClick={() => del.mutate()} aria-label="Delete question"><Trash2 className="size-3.5" /></button>
      </div>
    </li>
  );
}

export default function Clarifications({ rfpId }: { rfpId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reviewer] = useReviewer();
  const rfp = useRfp(rfpId);
  const { data, isLoading, error } = useClarifications(rfpId);
  const [text, setText] = useState("");
  const [reason, setReason] = useState("");
  const add = useMutation({
    mutationFn: () => http.post<Clarification>(`/rfps/${rfpId}/clarifications`, { text, reason, reviewer }),
    onSuccess: () => { setText(""); setReason(""); qc.invalidateQueries({ queryKey: ["clar", rfpId] }); toast("ok", "Question added"); },
    onError: (e: Error) => toast("bad", e.message),
  });
  const live = (data ?? []).filter((c) => c.status !== "dismissed");
  return (
    <div className="mx-auto max-w-3xl space-y-4">
      <div>
        <p className="text-xs text-muted"><Link className="underline" to={`/rfps/${rfpId}`}>{rfp.data?.name ?? "RFP"}</Link> / Clarifications</p>
        <h1 className="text-xl font-bold">Clarification questions</h1>
        <p className="text-sm text-muted">Where the sources could not answer, I list the question instead of guessing. Approved questions go into the exported response
          {rfp.data?.deadline ? "" : ""}{rfp.data?.format_rules?.clarification_deadline ? `; the customer wants them by ${rfp.data.format_rules.clarification_deadline}` : ""}.</p>
      </div>
      {isLoading ? <Spinner /> : error ? <ErrorNote error={error} /> : live.length === 0 && (data ?? []).length === 0 ? (
        <Empty title="Nothing to clarify yet">Questions appear here when drafting finds a gap the sources cannot fill. You can also add your own below.</Empty>
      ) : <ul className="space-y-3">{(data ?? []).map((c) => <Row key={c.id} rfpId={rfpId} c={c} />)}</ul>}
      <form className="card space-y-2 p-4" onSubmit={(e) => { e.preventDefault(); add.mutate(); }}>
        <h2 className="text-sm font-semibold">Add your own question</h2>
        <textarea className="input" rows={2} placeholder="Ask the customer…" value={text} onChange={(e) => setText(e.target.value)} aria-label="New question" />
        <input className="input" placeholder="Why we are asking (optional)" value={reason} onChange={(e) => setReason(e.target.value)} aria-label="Reason" />
        <button className="btn btn-primary btn-sm" disabled={!text.trim() || add.isPending}><Plus className="size-3.5" /> Add question</button>
      </form>
    </div>
  );
}
