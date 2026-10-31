import { useEffect, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { BadgeCheck, GitBranch, Layers, ShieldQuestion, Wrench } from "lucide-react";
import {
  CAPABILITY_CHOICES, CAP_LABEL, CAP_TONE, GAP_HELP, GAP_LABEL, KIND_LABEL, NEED_LABEL, http, type MatrixRow,
} from "../api";
import { refreshAnalysis, useMatrix, useReviewer } from "../hooks";
import { Chip, Spinner, useToast } from "../ui";

/** How this requirement is met, what code to give it, what is missing and who should look. All of it is evidence-based
 *  and checked in code; a person validates anything incomplete. */
export default function AssessmentPanel({ rfpId, requirementId }: { rfpId: number; requirementId: number }) {
  const { data } = useMatrix(rfpId);
  const row = data?.rows.find((r) => r.requirement_id === requirementId);
  const scheme = data?.scheme ?? null;
  if (!row) return null;
  const i = row.interpretation ?? {};
  const unclear = (i.findings ?? []).length > 0;
  const hasInterp = unclear || (i.depends_on ?? []).length > 0 || (i.depended_on_by ?? []).length > 0 || (i.components ?? []).length > 0;

  return (
    <div className="space-y-3">
      <h3 className="h-section flex items-center gap-1.5"><Layers className="size-3.5" /> Capability and response code</h3>
      {!row.assessed ? (
        <p className="text-sm text-muted">{row.triage === "owner" ? "Handled by an owner, so it is not classified." : "Not assessed yet. Use Assess on the Analysis page."}</p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2">
            <Chip tone={CAP_TONE[row.capability] ?? "muted"}>{CAP_LABEL[row.capability] ?? row.capability}</Chip>
            {row.code ? <span className="chip chip-info font-mono" title="Suggested response code">{row.code}</span>
              : scheme?.kind === "capability" && <span className="text-xs text-muted">no code suggested</span>}
            {row.confirmed ? <Chip tone="ok"><BadgeCheck className="size-3" /> validated by {row.confirmed_by}</Chip>
              : row.needs_validation ? <Chip tone="warn"><ShieldQuestion className="size-3" /> needs a person to validate</Chip>
              : row.code ? <Chip tone="ok">evidence complete</Chip> : null}
            {!row.assessment_current && <Chip tone="warn" title="The answer changed after this was assessed">out of date</Chip>}
          </div>

          {row.basis && <p className="text-sm">{row.basis}</p>}
          {row.parts.length > 0 && (
            <ul className="space-y-1.5 text-xs">
              {row.parts.map((p, n) => (
                <li key={n} className="rounded-md border border-line p-2">
                  <p className="flex flex-wrap items-center gap-1.5"><Chip tone={CAP_TONE[p.capability] ?? "muted"}>{CAP_LABEL[p.capability]}</Chip><span className="font-medium">{p.what}</span></p>
                  {p.quote && <blockquote className="mt-1 rounded bg-warn-bg px-2 py-1 text-ink line-clamp-3">{p.quote}</blockquote>}
                  {p.reason && <p className="mt-1 text-warn">Not accepted: {p.reason}.</p>}
                </li>
              ))}
            </ul>
          )}
          {row.validation_reasons.length > 0 && (
            <ul className="list-disc space-y-0.5 rounded-lg bg-warn-bg py-2 pr-3 pl-7 text-xs text-warn">
              {row.validation_reasons.map((r) => <li key={r}>{r}</li>)}
            </ul>
          )}
          {row.needs.length > 0 && (
            <p className="text-xs text-muted">This code needs {row.needs.map((n) => NEED_LABEL[n] ?? n).join(", ")}.
              {row.needs.includes("estimate") && !row.estimate && <b className="text-warn"> The estimate is missing.</b>}
              {row.needs.includes("attachment") && row.attachments.length === 0 && <b className="text-warn"> The attachment is missing.</b>}</p>
          )}
          {row.gaps.length > 0 && (
            <ul className="space-y-1.5 text-xs">
              {row.gaps.map((g) => (
                <li key={g.type} className="rounded-md border border-line p-2">
                  <p className="flex flex-wrap items-center gap-1.5"><Chip tone={g.type === "confirmed_gap" || g.type === "security_concern" ? "bad" : "warn"}>{GAP_LABEL[g.type] ?? g.type}</Chip>
                    <span className="text-muted">suggested owner: <b className="text-ink">{g.owner}</b></span></p>
                  <p className="mt-1">{g.detail}</p>
                  {GAP_HELP[g.type] && <p className="mt-0.5 text-muted">{GAP_HELP[g.type]}</p>}
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {hasInterp && (
        <div className="space-y-1.5 text-xs">
          <h4 className="flex items-center gap-1.5 font-semibold"><GitBranch className="size-3.5" /> About the requirement itself</h4>
          {(i.depends_on ?? []).length > 0 && <p>Depends on {(i.depends_on ?? []).map((d) => `${d.req_id}${d.how === "implied" ? " (implied)" : ""}`).join(", ")}.</p>}
          {(i.depended_on_by ?? []).length > 0 && <p>Other requirements rely on this one: {(i.depended_on_by ?? []).join(", ")}.</p>}
          {(i.components ?? []).length > 0 && <p className="flex flex-wrap items-center gap-1">Touches {(i.components ?? []).map((c) => <Chip key={c} tone="info">{c}</Chip>)}{i.spans_multiple && <b className="text-warn">spans several components</b>}</p>}
          {(i.findings ?? []).map((f) => (
            <p key={f.detail} className="rounded-md bg-warn-bg px-2 py-1 text-warn"><b>{KIND_LABEL[f.kind] ?? f.kind}.</b> {f.detail}{f.with.length > 0 && ` (see ${f.with.join(", ")})`}</p>
          ))}
        </div>
      )}

      {row.assessed && <Validate rfpId={rfpId} row={row} codes={scheme?.kind === "capability" ? scheme.codes.map((c) => c.code) : []} />}
    </div>
  );
}

function Validate({ rfpId, row, codes }: { rfpId: number; row: MatrixRow; codes: string[] }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reviewer] = useReviewer();
  const [cap, setCap] = useState(row.capability);
  const [code, setCode] = useState(row.code);
  const [estimate, setEstimate] = useState(row.estimate ?? "");
  const [attach, setAttach] = useState(row.attachments.join(", "));
  useEffect(() => { setCap(row.capability); setCode(row.code); setEstimate(row.estimate ?? ""); setAttach(row.attachments.join(", ")); },
    [row.requirement_id, row.capability, row.code, row.estimate, row.attachments]);
  const save = useMutation({
    mutationFn: (confirm: boolean) => http.patch(`/rfps/${rfpId}/assessments/${row.requirement_id}`, {
      ...(cap !== row.capability ? { capability: cap } : {}), ...(code !== row.code ? { code } : {}),
      estimate, attachments: attach.split(",").map((x) => x.trim()).filter(Boolean), confirm, reviewer }),
    onSuccess: (_d, confirm) => { refreshAnalysis(qc, rfpId); toast("ok", confirm ? "Code validated" : "Saved"); },
    onError: (e: Error) => toast("bad", e.message),
  });
  const id = (k: string) => `${k}-${row.requirement_id}`;
  return (
    <details className="rounded-lg border border-line p-2.5 text-xs">
      <summary className="flex cursor-pointer items-center gap-1.5 font-semibold"><Wrench className="size-3.5" /> Validate or correct</summary>
      <div className="mt-2 grid gap-2">
        <div><label className="label" htmlFor={id("cap")}>Capability</label>
          <select id={id("cap")} className="input" value={cap} onChange={(e) => setCap(e.target.value)}>
            {CAPABILITY_CHOICES.map((c) => <option key={c} value={c}>{CAP_LABEL[c]}</option>)}</select></div>
        {codes.length > 0 && (
          <div><label className="label" htmlFor={id("code")}>Response code</label>
            <select id={id("code")} className="input" value={code} onChange={(e) => setCode(e.target.value)}>
              <option value="">No code</option>{codes.map((c) => <option key={c} value={c}>{c}</option>)}</select></div>
        )}
        <div><label className="label" htmlFor={id("est")}>Effort estimate (when the code asks for one)</label>
          <input id={id("est")} className="input" value={estimate} onChange={(e) => setEstimate(e.target.value)} placeholder="e.g. 15 person-days" /></div>
        <div><label className="label" htmlFor={id("att")}>Attachments (file names, separated by commas)</label>
          <input id={id("att")} className="input" value={attach} onChange={(e) => setAttach(e.target.value)} /></div>
        <div className="flex gap-2">
          <button className="btn btn-sm" disabled={save.isPending} onClick={() => save.mutate(false)}>Save</button>
          <button className="btn btn-sm btn-primary" disabled={save.isPending} onClick={() => save.mutate(true)}>
            {save.isPending && <Spinner className="size-3.5" />} Validate this code</button>
        </div>
      </div>
    </details>
  );
}
