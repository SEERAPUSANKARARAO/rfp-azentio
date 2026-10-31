// Typed client for the RFP AI backend. Everything goes through /api (proxied to :8000 in dev).

export class ApiError extends Error {
  constructor(public status: number, public detail: unknown, message: string) {
    super(message);
  }
}

function messageOf(detail: unknown, status: number): string {
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) return detail.map((d) => d?.msg ?? String(d)).join("; ");
  if (detail && typeof detail === "object" && "message" in detail) return String((detail as { message: unknown }).message);
  return `Request failed (${status})`;
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const isForm = typeof FormData !== "undefined" && body instanceof FormData;
  const res = await fetch(`/api${path}`, {
    method,
    headers: body !== undefined && !isForm ? { "Content-Type": "application/json" } : undefined,
    body: body === undefined ? undefined : isForm ? (body as FormData) : JSON.stringify(body),
  });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(res.status, detail, messageOf(detail, res.status));
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

export const http = {
  get: <T>(p: string) => request<T>("GET", p),
  post: <T>(p: string, b?: unknown) => request<T>("POST", p, b ?? {}),
  put: <T>(p: string, b: unknown) => request<T>("PUT", p, b),
  patch: <T>(p: string, b: unknown) => request<T>("PATCH", p, b),
  del: <T>(p: string) => request<T>("DELETE", p),
};

/** POST that returns a file. Returns the blob, the server's file name and whether it is a DRAFT copy. */
export async function download(path: string): Promise<{ blob: Blob; filename: string; draft: boolean }> {
  const res = await fetch(`/api${path}`, { method: "POST" });
  if (!res.ok) {
    let detail: unknown = null;
    try {
      detail = (await res.json()).detail;
    } catch {
      /* not JSON */
    }
    throw new ApiError(res.status, detail, messageOf(detail, res.status));
  }
  const cd = res.headers.get("content-disposition") ?? "";
  const m = /filename="?([^";]+)"?/.exec(cd);
  return { blob: await res.blob(), filename: m?.[1] ?? "response", draft: res.headers.get("x-export-draft") === "true" };
}

export async function downloadGet(path: string): Promise<{ blob: Blob; filename: string }> {
  const res = await fetch(`/api${path}`);
  if (!res.ok) throw new ApiError(res.status, null, `Request failed (${res.status})`);
  const m = /filename="?([^";]+)"?/.exec(res.headers.get("content-disposition") ?? "");
  return { blob: await res.blob(), filename: m?.[1] ?? "file" };
}

export function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 2000);
}

// ---------------------------------------------------------------- types
export type ReviewStatus = "queued" | "drafted" | "accepted";
export type ComplianceStatus = "Complied" | "Partially Complied" | "Not Complied" | "Cannot Confirm" | "Pending";

export interface Citation {
  marker: number;
  doc_id: number | null;
  chunk_id: number | null;
  doc_title: string;
  doc_date: string | null;
  location: string;
  quote: string;
}

export interface Answer {
  requirement_id: number;
  req_id: string;
  requirement: string;
  section_code: string;
  section_title: string;
  order: number;
  mandatory: boolean | null;
  review_status: ReviewStatus;
  triage: "auto" | "low_confidence" | "clarify" | "owner" | null;
  status: ComplianceStatus | null;
  confidence: number | null;
  confidence_breakdown: Record<string, any>;
  flags: string[];
  flag_details: Record<string, any>;
  text: string;
  draft_text: string;
  edited: boolean;
  needs_review: boolean;
  assigned_to: string | null;
  reasoning: string;
  error: string | null;
  citations: Citation[];
}

export interface Job {
  id: number | null;
  rfp_id: number;
  status: "none" | "running" | "paused" | "done" | "failed";
  total: number;
  done: number;
  message: string;
  resume_at: number | null;
}

export interface Summary {
  total: number;
  drafted: number;
  accepted: number;
  needs_review: number;
  clarify: number;
  owner: number;
  flagged: number;
  conflicting: number;
  stale: number;
  unanswered: number;
}

export interface FormatRules {
  word_limit?: number | null;
  compliance_codes?: string[];
  compliance_map?: Record<string, string>;
  compliance_legend?: string;
  file_name?: string | null;
  submission_format?: string | null;
  keep_ids?: boolean;
  restate_question?: boolean;
  cite_sources?: boolean;
  deadline?: string | null;
  clarification_deadline?: string | null;
  confirmed?: boolean;
  detected_by?: string;
}

export interface ParseReport {
  requirements: number;
  sections: { code: string; title: string; count: number }[];
  mandatory: number;
  optional: number;
  unknown_priority: number;
  low_confidence: string[];
  warnings: string[];
}

export interface RfpSummary {
  id: number;
  name: string;
  customer: string;
  filename: string;
  file_type: "xlsx" | "docx" | "pdf";
  status: string;
  deadline: string | null;
  requirement_count: number;
  parse_report: ParseReport;
  created_at: string;
}

export interface RfpDetail extends RfpSummary {
  submission_instructions: string;
  format_rules: FormatRules;
}

export interface Clarification {
  id: number;
  text: string;
  reason: string;
  requirement_ids: string[];
  status: "open" | "approved" | "dismissed";
}

export interface AuditEvent {
  id: number;
  ts: string;
  rfp_id: number | null;
  entity: string;
  entity_id: number | null;
  action: string;
  actor: string;
  payload: Record<string, any>;
}

export interface KbDoc {
  id: number;
  title: string;
  kind: string;
  version: string | null;
  doc_date: string | null;
  status: string;
  canonical: boolean;
  reuse_permitted: boolean;
  answerable: boolean;
  label: string;
  owner: string | null;
  customer_redacted: boolean;
  chunks: number;
  stale_reasons: string[];
}

export interface ConflictSide {
  doc_id: number;
  title: string;
  date: string | null;
  version: string | null;
  location: string;
  text: string;
  stale_reasons: string[];
}

export interface Conflict {
  id: number;
  topic: string;
  detail: string;
  status: "open" | "resolved";
  canonical_doc_id: number | null;
  older: ConflictSide;
  newer: ConflictSide;
  affected: { rfp_id: number; requirement_id: number; req_id: string; review_status: string }[];
}

export interface ExportIssue {
  severity: "blocking" | "warning" | "info";
  code: string;
  message: string;
  requirements: string[];
}
export interface ExportCheck {
  ready: boolean;
  issues: ExportIssue[];
  counts: { requirements: number; included: number; approved: number; words_total: number };
}

export interface LlmStatus {
  providers: {
    provider: string;
    configured_keys: number;
    keys: {
      alias: string;
      disabled: boolean;
      disabled_reason: string | null;
      calls_24h: number;
      ok_24h: number;
      tokens_24h: number;
      models: { model: string; status: "ready" | "cooldown" | "exhausted" | "disabled"; seconds_left: number; last_error: string | null }[];
    }[];
  }[];
  tasks: Record<string, { provider: string; model: string }[]>;
  cache_entries: number;
}

export interface KbStatus {
  documents: number;
  chunks: number;
  retrieval_mode: "hybrid" | "keyword";
}

export const FLAG_LABEL: Record<string, string> = {
  missing: "Evidence missing",
  conflicting: "Conflicting sources",
  stale: "Stale source",
  unsupported: "Unsupported statement removed",
  commercial: "Commercial: needs owner",
  legal: "Legal: needs owner",
  se_provided: "SE-provided fact",
  injection: "Embedded instruction ignored",
};

export interface ChatMsg {
  id: number;
  role: "user" | "assistant";
  content: string;
  meta: { intent?: string; refs?: string[]; select?: number | null; sources?: { marker: number; doc_title: string; location: string; doc_date: string | null; quote: string }[] };
  ts: string;
}
