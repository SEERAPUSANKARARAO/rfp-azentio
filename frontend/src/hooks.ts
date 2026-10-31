import { useEffect, useState, useSyncExternalStore } from "react";
import { QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  http, type Checklist, type GapsSummary, type MatrixResponse, type Readiness, type Workflow, type Answer, type AuditEvent, type Clarification, type Conflict, type Job, type KbDoc, type KbStatus,
  type LlmStatus, type RfpDetail, type RfpSummary, type Summary,
} from "./api";

export const queryClient = new QueryClient({
  defaultOptions: { queries: { staleTime: 2000, retry: 1, refetchOnWindowFocus: false } },
});

// ------------------------------------------------------------ small persisted preferences (never required)
function readPref(key: string, fallback: string): string {
  try {
    return localStorage.getItem(key) ?? fallback;
  } catch {
    return fallback;
  }
}
function writePref(key: string, value: string) {
  try {
    localStorage.setItem(key, value);
  } catch {
    /* private mode or blocked storage: the app works without it */
  }
}

const listeners = new Set<() => void>();
const subscribe = (cb: () => void) => (listeners.add(cb), () => void listeners.delete(cb));

export function useReviewer(): [string, (v: string) => void] {
  const name = useSyncExternalStore(subscribe, () => readPref("rfpai.reviewer", "se"));
  return [name, (v) => (writePref("rfpai.reviewer", v.trim() || "se"), listeners.forEach((l) => l()))];
}

export function useTheme(): ["light" | "dark" | "system", (t: "light" | "dark" | "system") => void] {
  const theme = useSyncExternalStore(subscribe, () => readPref("rfpai.theme", "system")) as "light" | "dark" | "system";
  useEffect(() => {
    const root = document.documentElement;
    if (theme === "system") root.removeAttribute("data-theme");
    else root.setAttribute("data-theme", theme);
  }, [theme]);
  return [theme, (t) => (writePref("rfpai.theme", t), listeners.forEach((l) => l()))];
}

export const lastRfp = {
  get: () => Number(readPref("rfpai.lastRfp", "0")) || null,
  set: (id: number) => writePref("rfpai.lastRfp", String(id)),
};

// ------------------------------------------------------------ queries
export const useRfps = () => useQuery({ queryKey: ["rfps"], queryFn: () => http.get<RfpSummary[]>("/rfps") });
export const useRfp = (id: number) => useQuery({ queryKey: ["rfp", id], queryFn: () => http.get<RfpDetail>(`/rfps/${id}`) });
export const useAnswers = (id: number) =>
  useQuery({ queryKey: ["answers", id], queryFn: () => http.get<Answer[]>(`/rfps/${id}/answers`) });
export const useSummary = (id: number) =>
  useQuery({ queryKey: ["summary", id], queryFn: () => http.get<Summary>(`/rfps/${id}/summary`) });
export const useClarifications = (id: number) =>
  useQuery({ queryKey: ["clar", id], queryFn: () => http.get<Clarification[]>(`/rfps/${id}/clarifications`) });
export const useAudit = (rfpId: number | null, limit = 200) =>
  useQuery({
    queryKey: ["audit", rfpId, limit],
    queryFn: () => http.get<AuditEvent[]>(rfpId ? `/rfps/${rfpId}/audit?limit=${limit}` : `/audit?limit=${limit}`),
    refetchInterval: 5000,
  });
export const useKbDocs = () => useQuery({ queryKey: ["kbdocs"], queryFn: () => http.get<KbDoc[]>("/kb/documents") });
export const useKbStatus = () => useQuery({ queryKey: ["kbstatus"], queryFn: () => http.get<KbStatus>("/kb/status") });
export const useConflicts = () => useQuery({ queryKey: ["conflicts"], queryFn: () => http.get<Conflict[]>("/kb/conflicts") });
export const useLlmStatus = () =>
  useQuery({ queryKey: ["llm"], queryFn: () => http.get<LlmStatus>("/llm/status"), refetchInterval: 4000 });
export const useAssignees = (id: number) =>
  useQuery({ queryKey: ["assignees", id], queryFn: () => http.get<string[]>(`/rfps/${id}/assignees`), staleTime: Infinity });

/** Drafting progress. Server-sent events when available; polling is the fallback and the safety net. */
export function useJob(rfpId: number) {
  const qc = useQueryClient();
  const q = useQuery({
    queryKey: ["job", rfpId],
    queryFn: () => http.get<Job>(`/rfps/${rfpId}/job`),
    refetchInterval: (query) => (query.state.data?.status === "running" ? 2500 : false),
  });
  const [live, setLive] = useState(false);
  const status = q.data?.status;

  useEffect(() => {
    if (status !== "running" || typeof EventSource === "undefined") return;
    const es = new EventSource(`/api/rfps/${rfpId}/events`);
    es.onopen = () => setLive(true);
    es.onmessage = (e) => {
      try {
        qc.setQueryData(["job", rfpId], JSON.parse(e.data) as Job);
        qc.invalidateQueries({ queryKey: ["answers", rfpId] });
        qc.invalidateQueries({ queryKey: ["summary", rfpId] });
      } catch {
        /* ignore a malformed event; polling still covers it */
      }
    };
    es.onerror = () => {
      setLive(false);
      es.close();
    };
    return () => es.close();
  }, [rfpId, status, qc]);

  // when a run ends, refresh everything it touched once more
  useEffect(() => {
    if (status && status !== "running") {
      qc.invalidateQueries({ queryKey: ["answers", rfpId] });
      qc.invalidateQueries({ queryKey: ["summary", rfpId] });
      qc.invalidateQueries({ queryKey: ["clar", rfpId] });
      qc.invalidateQueries({ queryKey: ["rfps"] });
    }
  }, [status, rfpId, qc]);
  return { ...q, live };
}

export function replaceAnswer(qc: QueryClient, rfpId: number, updated: Answer) {
  qc.setQueryData<Answer[]>(["answers", rfpId], (old) => old?.map((a) => (a.requirement_id === updated.requirement_id ? updated : a)));
  qc.invalidateQueries({ queryKey: ["summary", rfpId] });
  qc.invalidateQueries({ queryKey: ["audit"] });
}


// ------------------------------------------------------------ Level 2
export const useMatrix = (rfpId: number) => useQuery({
  queryKey: ["matrix", rfpId], queryFn: () => http.get<MatrixResponse>(`/rfps/${rfpId}/assessments`),
  refetchInterval: (q) => (q.state.data?.assessing ? 2500 : false),
});
export const useGaps = (rfpId: number) => useQuery({ queryKey: ["gaps", rfpId], queryFn: () => http.get<GapsSummary>(`/rfps/${rfpId}/gaps`) });
export const useReadiness = (rfpId: number) => useQuery({ queryKey: ["readiness", rfpId], queryFn: () => http.get<Readiness>(`/rfps/${rfpId}/readiness`) });
export const useChecklist = (rfpId: number) => useQuery({ queryKey: ["checklist", rfpId], queryFn: () => http.get<Checklist>(`/rfps/${rfpId}/checklist`) });
export const useWorkflow = (rfpId: number) => useQuery({ queryKey: ["workflow", rfpId], queryFn: () => http.get<Workflow>(`/rfps/${rfpId}/workflow`) });

/** Everything Level 2 shows is derived from the same answers, so refresh it all together. */
export function refreshAnalysis(qc: QueryClient, rfpId: number) {
  ["matrix", "gaps", "readiness", "checklist", "workflow", "clar", "answers"].forEach((k) => qc.invalidateQueries({ queryKey: [k, rfpId] }));
}
