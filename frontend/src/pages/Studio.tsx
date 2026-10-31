import { useCallback, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Download, HelpCircle, Play, RotateCw } from "lucide-react";
import { http, type Job } from "../api";
import { useAnswers, useJob, useRfp, useSummary, lastRfp } from "../hooks";
import { Empty, ErrorNote, ProgressBar, Spinner, cx, useToast } from "../ui";
import Queue, { matches, type Filter } from "../components/Queue";
import AnswerPane from "../components/AnswerPane";
import Evidence from "../components/Evidence";
import ExportDialog from "../components/ExportDialog";
import ChatDock from "../components/ChatDock";

function JobBanner({ job, onStart, starting }: { job: Job | undefined; onStart: () => void; starting: boolean }) {
  if (!job) return null;
  if (job.status === "running")
    return (
      <div role="status" className="flex items-center gap-2 rounded-lg bg-info-bg px-3 py-2 text-sm text-info">
        <Spinner className="size-4" /> <span>{job.message || "Drafting"} <b>{job.done}/{job.total}</b></span>
      </div>
    );
  if (job.status === "paused")
    return (
      <div role="alert" className="flex flex-wrap items-center gap-2 rounded-lg bg-warn-bg px-3 py-2 text-sm text-warn">
        <AlertTriangle className="size-4" /> <span className="flex-1">{job.message}</span>
        <button className="btn btn-sm" onClick={onStart} disabled={starting}><RotateCw className="size-3.5" /> Resume</button>
      </div>
    );
  return null;
}

export default function Studio({ rfpId }: { rfpId: number }) {
  const qc = useQueryClient();
  const toast = useToast();
  const rfp = useRfp(rfpId);
  const answers = useAnswers(rfpId);
  const summary = useSummary(rfpId);
  const job = useJob(rfpId);
  const [filter, setFilter] = useState<Filter>("all");
  const [selected, setSelected] = useState<number | null>(null);
  const [marker, setMarker] = useState<number | null>(null);
  const [exportOpen, setExportOpen] = useState(false);
  const [pane, setPane] = useState<"queue" | "answer" | "evidence">("answer");

  useEffect(() => lastRfp.set(rfpId), [rfpId]);

  const list = answers.data ?? [];
  const visible = useMemo(() => list.filter((a) => matches(filter, a)), [list, filter]);
  const current = useMemo(() => list.find((a) => a.requirement_id === selected) ?? null, [list, selected]);

  // first load: land on the first answer that needs a person, else the first question
  useEffect(() => {
    if (selected === null && list.length) setSelected((list.find((a) => a.review_status === "drafted" && a.needs_review) ?? list[0]).requirement_id);
  }, [list, selected]);
  useEffect(() => setMarker(null), [selected]);

  const move = useCallback((delta: number) => {
    const i = visible.findIndex((a) => a.requirement_id === selected);
    const next = visible[Math.min(Math.max(i + delta, 0), visible.length - 1)];
    if (next) setSelected(next.requirement_id);
  }, [visible, selected]);

  /** After approving, jump to the next answer still waiting for a decision. */
  const acceptedNext = useCallback(() => {
    const i = list.findIndex((a) => a.requirement_id === selected);
    const rest = [...list.slice(i + 1), ...list.slice(0, i)];
    const next = rest.find((a) => a.review_status === "drafted" && matches(filter, a)) ?? rest.find((a) => a.review_status === "drafted");
    if (next) setSelected(next.requirement_id);
    else toast("ok", "Every drafted answer has been decided");
  }, [list, selected, filter, toast]);

  const start = useMutation({
    mutationFn: () => http.post<Job>(`/rfps/${rfpId}/draft`),
    onSuccess: (j) => { qc.setQueryData(["job", rfpId], j); toast("info", "Drafting started"); },
    onError: (e: Error) => toast("bad", e.message),
  });

  // keyboard: Tab = accept & next (only when no control is focused), J/K or arrows move, E edits
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (exportOpen || document.querySelector('[role="dialog"]')) return;
      const typing = /^(INPUT|TEXTAREA|SELECT)$/.test(t.tagName) || t.isContentEditable;
      if (e.key === "Enter" && (e.ctrlKey || e.metaKey) && !typing) { document.querySelector<HTMLButtonElement>('button[title^="Approve this answer"]')?.click(); return; }
      if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
      if (e.key === "Tab" && !e.shiftKey && !/^(BUTTON|A)$/.test(t.tagName)) {
        const b = document.querySelector<HTMLButtonElement>('button[title^="Approve this answer"]');
        if (b && !b.disabled) { e.preventDefault(); b.click(); }
      } else if (e.key === "j" || e.key === "ArrowDown") { e.preventDefault(); move(1); }
      else if (e.key === "k" || e.key === "ArrowUp") { e.preventDefault(); move(-1); }
      else if (e.key === "e") { const b = [...document.querySelectorAll<HTMLButtonElement>("button")].find((x) => /^(Edit|Write answer)$/.test(x.textContent?.trim() ?? "")); b?.click(); e.preventDefault(); }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [move, exportOpen]);

  if (rfp.isLoading || answers.isLoading) return <div className="flex justify-center py-24"><Spinner className="size-6" /></div>;
  if (rfp.error) return <ErrorNote error={rfp.error} />;
  if (!rfp.data) return <Empty title="RFP not found"><Link className="underline" to="/">Back to the list</Link></Empty>;

  const s = summary.data;
  const jobStatus = job.data?.status;

  return (
    <div className="flex h-[calc(100vh-5.5rem)] min-h-[560px] flex-col gap-3">
      <div className="card flex flex-wrap items-center gap-x-6 gap-y-2 px-4 py-3">
        <div className="min-w-0">
          <h1 className="truncate text-base font-bold">{rfp.data.name}</h1>
          <p className="text-xs text-muted">
            {rfp.data.filename}{rfp.data.deadline ? ` · due ${rfp.data.deadline}` : ""}
            {" · "}<Link className="underline" to={`/rfps/${rfpId}/clarifications`}>{(s?.clarify ?? 0)} to clarify</Link>{" · "}<Link className="underline" to={`/rfps/${rfpId}/analysis`}>Analysis</Link>
          </p>
        </div>
        {s && (
          <div className="min-w-48 flex-1 max-w-sm">
            <p className="mb-1 flex justify-between text-xs"><span><b>{s.drafted}</b>/{s.total} drafted · <b>{s.accepted}</b> approved</span><span className="text-muted">{s.total ? Math.round((s.accepted / s.total) * 100) : 0}% approved</span></p>
            <ProgressBar done={s.accepted} total={s.total} label="Approved answers" />
          </div>
        )}
        <div className="ml-auto flex flex-wrap items-center gap-2">
          {s && s.unanswered > 0 && jobStatus !== "running" && jobStatus !== "paused" && (
            <button className="btn btn-primary" onClick={() => start.mutate()} disabled={start.isPending}>
              {start.isPending ? <Spinner className="size-4" /> : <Play className="size-4" />} Draft {s.unanswered} answer{s.unanswered === 1 ? "" : "s"}
            </button>
          )}
          <button className="btn" onClick={() => setExportOpen(true)}><Download className="size-4" /> Export</button>
        </div>
        <div className="basis-full empty:hidden"><JobBanner job={job.data} onStart={() => start.mutate()} starting={start.isPending} /></div>
      </div>

      {list.length === 0 ? <Empty title="This RFP has no requirements">The file parsed but no requirements were found.</Empty> : (
        <>
          <div className="flex gap-1 lg:hidden" role="tablist" aria-label="Panels">
            {(["queue", "answer", "evidence"] as const).map((p) => (
              <button key={p} role="tab" aria-selected={pane === p} onClick={() => setPane(p)}
                className={cx("flex-1 rounded-lg px-3 py-1.5 text-sm font-medium capitalize", pane === p ? "bg-accent text-on-solid" : "bg-panel2 text-muted")}>{p}</button>
            ))}
          </div>
          <div className="grid min-h-[320px] flex-1 gap-3 lg:grid-cols-[minmax(260px,320px)_minmax(0,1fr)_minmax(300px,380px)]">
            <div className={cx("min-h-0", pane !== "queue" && "hidden lg:block")}>
              <Queue answers={list} filter={filter} onFilter={setFilter} selected={selected} onSelect={(id) => { setSelected(id); setPane("answer"); }} />
            </div>
            <div className={cx("min-h-0", pane !== "answer" && "hidden lg:block")}>
              <AnswerPane rfpId={rfpId} answer={current} rules={rfp.data.format_rules} activeMarker={marker} onMarker={(n) => { setMarker(n); if (n) setPane("evidence"); }} onAcceptedNext={acceptedNext} />
            </div>
            <div className={cx("min-h-0", pane !== "evidence" && "hidden lg:block")}>
              <Evidence rfpId={rfpId} answer={current} activeMarker={marker} onMarker={setMarker} />
            </div>
          </div>
        </>
      )}
      {list.length > 0 && <ChatDock rfpId={rfpId} answers={list} selected={selected} onSelect={(id) => { setSelected(id); setPane("answer"); }} />}
      {exportOpen && <ExportDialog rfpId={rfpId} rules={rfp.data.format_rules} onClose={() => setExportOpen(false)} />}
      <p className="sr-only"><HelpCircle /> Keyboard: Tab accepts and moves on when no control is focused; J and K move between questions; E edits.</p>
    </div>
  );
}
