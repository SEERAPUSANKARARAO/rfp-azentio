import { useMemo, useState } from "react";
import { useRfps, useAudit } from "../hooks";
import { Chip, Empty, ErrorNote, Spinner, fmtTime } from "../ui";

const TONE: Record<string, "ok" | "warn" | "bad" | "info" | "muted"> = {
  accepted: "ok", exported: "info", flagged: "warn", edited: "info", drafted: "muted", regenerated: "info", conflict_resolved: "ok",
};

function detail(p: Record<string, any>): string {
  const bits: string[] = [];
  if (p.req_id) bits.push(`requirement ${p.req_id}`);
  if (p.status) bits.push(String(p.status));
  if (p.confidence !== undefined) bits.push(`confidence ${Math.round(p.confidence * 100)}%`);
  if (p.assignee) bits.push(`to ${p.assignee}`);
  if (p.instruction) bits.push(`"${p.instruction}"`);
  if (p.file) bits.push(p.file);
  if (p.topic) bits.push(p.topic);
  if (p.title) bits.push(p.title);
  if (p.after && typeof p.after === "string") bits.push(`"${p.after.slice(0, 80)}${p.after.length > 80 ? "…" : ""}"`);
  if (p.approval_withdrawn) bits.push("approval withdrawn");
  return bits.join(" · ");
}

export default function Audit() {
  const { data: rfps } = useRfps();
  const [rfp, setRfp] = useState<number | null>(null);
  const [action, setAction] = useState("");
  const { data, isLoading, error } = useAudit(rfp, 500);
  const actions = useMemo(() => [...new Set((data ?? []).map((e) => e.action))].sort(), [data]);
  const rows = (data ?? []).filter((e) => !action || e.action === action);
  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Audit log</h1>
          <p className="text-sm text-muted">Every draft, edit, approval, hand-off and export, with who did it. Nothing here can be edited.</p>
        </div>
        <div className="flex gap-2">
          <select className="input !w-auto" aria-label="RFP" value={rfp ?? ""} onChange={(e) => setRfp(e.target.value ? Number(e.target.value) : null)}>
            <option value="">All RFPs</option>
            {(rfps ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
          <select className="input !w-auto" aria-label="Action" value={action} onChange={(e) => setAction(e.target.value)}>
            <option value="">All actions</option>
            {actions.map((a) => <option key={a} value={a}>{a.replace(/_/g, " ")}</option>)}
          </select>
        </div>
      </div>
      {isLoading ? <Spinner /> : error ? <ErrorNote error={error} /> : rows.length === 0 ? <Empty title="No activity yet" /> : (
        <div className="card overflow-x-auto">
          <table className="w-full text-sm">
            <thead><tr className="border-b border-line text-left text-xs text-muted"><th className="px-3 py-2">When</th><th className="px-3 py-2">Who</th><th className="px-3 py-2">Action</th><th className="px-3 py-2">Detail</th></tr></thead>
            <tbody>
              {rows.map((e) => (
                <tr key={e.id} className="border-b border-line/60 last:border-0">
                  <td className="px-3 py-2 whitespace-nowrap text-muted">{fmtTime(e.ts)}</td>
                  <td className="px-3 py-2">{e.actor}</td>
                  <td className="px-3 py-2"><Chip tone={TONE[e.action] ?? "muted"}>{e.action.replace(/_/g, " ")}</Chip></td>
                  <td className="px-3 py-2 text-muted">{detail(e.payload)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
