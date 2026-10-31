import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, FileSpreadsheet, Info, OctagonAlert } from "lucide-react";
import { ApiError, download, downloadGet, http, saveBlob, type ExportCheck, type FormatRules } from "../api";
import { Chip, ErrorNote, Modal, Spinner, cx, useToast } from "../ui";

const ICON = { blocking: OctagonAlert, warning: AlertTriangle, info: Info } as const;
const TONE = { blocking: "bg-bad-bg text-bad", warning: "bg-warn-bg text-warn", info: "bg-info-bg text-info" } as const;

export default function ExportDialog({ rfpId, rules, onClose }: { rfpId: number; rules: FormatRules | undefined; onClose: () => void }) {
  const qc = useQueryClient();
  const toast = useToast();
  const [approvedOnly, setApprovedOnly] = useState(true);
  const [wl, setWl] = useState<string>(String(rules?.word_limit ?? ""));
  const [fileName, setFileName] = useState(rules?.file_name ?? "");
  const [blocked, setBlocked] = useState<ExportCheck | null>(null);

  const check = useQuery({
    queryKey: ["exportcheck", rfpId, approvedOnly],
    queryFn: () => http.get<ExportCheck>(`/rfps/${rfpId}/export/check?approved_only=${approvedOnly}`),
  });
  useEffect(() => setBlocked(null), [approvedOnly]);

  const saveRules = useMutation({
    mutationFn: () => http.put<FormatRules>(`/rfps/${rfpId}/format-rules`, { ...rules, word_limit: wl ? Number(wl) : null, file_name: fileName || null }),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["rfp", rfpId] }); qc.invalidateQueries({ queryKey: ["exportcheck"] }); toast("ok", "Formatting rules confirmed"); },
    onError: (e: Error) => toast("bad", e.message),
  });

  const doExport = useMutation({
    mutationFn: async (allow: boolean) => download(`/rfps/${rfpId}/export?approved_only=${approvedOnly}&allow_blocking=${allow}`),
    onSuccess: ({ blob, filename, draft }) => {
      saveBlob(blob, filename);
      toast(draft ? "info" : "ok", draft ? `Saved ${filename}: this is a DRAFT copy, not for submission` : `Saved ${filename}`);
      qc.invalidateQueries({ queryKey: ["audit"] });
    },
    onError: (e: Error) => {
      if (e instanceof ApiError && e.status === 409 && e.detail && typeof e.detail === "object") setBlocked(e.detail as ExportCheck);
      else toast("bad", e.message);
    },
  });

  const matrix = useMutation({
    mutationFn: () => downloadGet(`/rfps/${rfpId}/export/matrix`),
    onSuccess: ({ blob, filename }) => saveBlob(blob, filename), onError: (e: Error) => toast("bad", e.message),
  });

  const data = check.data;
  return (
    <Modal title="Export response" onClose={onClose} wide>
      <div className="space-y-5">
        <fieldset>
          <legend className="h-section mb-2">What goes in the file</legend>
          <label className="flex cursor-pointer items-start gap-2 text-sm">
            <input type="radio" name="scope" className="mt-1" checked={approvedOnly} onChange={() => setApprovedOnly(true)} />
            <span><b>Approved answers only</b> (for submission). Anything not accepted is left blank.</span>
          </label>
          <label className="mt-1.5 flex cursor-pointer items-start gap-2 text-sm">
            <input type="radio" name="scope" className="mt-1" checked={!approvedOnly} onChange={() => setApprovedOnly(false)} />
            <span><b>Review copy</b>: includes drafts, every unapproved answer is marked, and the file is named <code>_DRAFT</code>.</span>
          </label>
        </fieldset>

        <div>
          <h3 className="h-section mb-2">Checks</h3>
          {check.isLoading ? <Spinner /> : check.error ? <ErrorNote error={check.error} /> : data && (
            <>
              <p className="mb-2 text-sm text-muted">{data.counts.included} of {data.counts.requirements} requirements will be answered · {data.counts.approved} approved · {data.counts.words_total} words</p>
              {data.issues.length === 0 ? <p className="rounded-lg bg-ok-bg px-3 py-2 text-sm text-ok">Nothing to flag.</p> : (
                <ul className="space-y-1.5">
                  {data.issues.map((i) => {
                    const Icon = ICON[i.severity];
                    return (
                      <li key={i.code} className={cx("flex items-start gap-2 rounded-lg px-3 py-2 text-sm", TONE[i.severity])}>
                        <Icon className="mt-0.5 size-4 shrink-0" /> <span>{i.message}</span>
                      </li>
                    );
                  })}
                </ul>
              )}
            </>
          )}
        </div>

        <div>
          <h3 className="h-section mb-2 flex items-center gap-2">Formatting rules found in the RFP {rules?.confirmed ? <Chip tone="ok">confirmed</Chip> : <Chip tone="warn">please confirm</Chip>}</h3>
          <div className="grid gap-3 sm:grid-cols-2">
            <div><label className="label" htmlFor="wl">Words per answer</label><input id="wl" className="input" inputMode="numeric" value={wl} onChange={(e) => setWl(e.target.value.replace(/\D/g, ""))} placeholder="no limit stated" /></div>
            <div><label className="label" htmlFor="fn">Required file name</label><input id="fn" className="input" value={fileName} onChange={(e) => setFileName(e.target.value)} placeholder="<VendorName>_Response.docx" /></div>
          </div>
          <p className="mt-2 text-xs text-muted">
            Compliance wording: {rules?.compliance_codes?.length ? rules.compliance_codes.join(" / ") : "none stated"}
            {rules?.deadline ? ` · Submission due ${rules.deadline}` : ""}{rules?.clarification_deadline ? ` · Questions by ${rules.clarification_deadline}` : ""}
          </p>
          <button className="btn btn-sm mt-2" disabled={saveRules.isPending} onClick={() => saveRules.mutate()}>Confirm rules</button>
        </div>

        {blocked && (
          <div role="alert" className="rounded-lg border border-bad p-3 text-sm">
            <p className="font-semibold text-bad">This breaks a rule the RFP stated.</p>
            <ul className="mt-1 list-disc pl-5">{blocked.issues.filter((i) => i.severity === "blocking").map((i) => <li key={i.code}>{i.message}</li>)}</ul>
            <button className="btn btn-danger btn-sm mt-2" onClick={() => doExport.mutate(true)}>Export anyway</button>
          </div>
        )}

        <div className="flex flex-wrap items-center justify-between gap-2 border-t border-line pt-4">
          <button className="btn" disabled={matrix.isPending} onClick={() => matrix.mutate()}><FileSpreadsheet className="size-4" /> Compliance matrix</button>
          <div className="flex gap-2">
            <button className="btn" onClick={onClose}>Close</button>
            <button className="btn btn-primary" disabled={doExport.isPending || check.isLoading} onClick={() => doExport.mutate(false)}>
              {doExport.isPending ? <Spinner className="size-4" /> : <Download className="size-4" />} {approvedOnly ? "Download response" : "Download review copy"}
            </button>
          </div>
        </div>
      </div>
    </Modal>
  );
}
