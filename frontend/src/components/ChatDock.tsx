import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { ChevronDown, ChevronUp, MessageSquare, Send, Trash2 } from "lucide-react";
import { http, type Answer, type ChatMsg } from "../api";
import { useReviewer } from "../hooks";
import { Chip, Spinner, cx, useToast } from "../ui";

const PROMPTS = ["Summarise this RFP", "What needs review?", "What is the progress?", "Show the clarification questions", "Why?"];
const CHANGES = new Set(["rewrite", "accept", "fact"]);

export default function ChatDock({ rfpId, answers, selected, onSelect }: {
  rfpId: number; answers: Answer[]; selected: number | null; onSelect: (requirementId: number) => void;
}) {
  const qc = useQueryClient();
  const toast = useToast();
  const [reviewer] = useReviewer();
  const [open, setOpen] = useState(false);
  const [text, setText] = useState("");
  const end = useRef<HTMLDivElement>(null);
  const key = ["chat", rfpId];
  const history = useQuery({ queryKey: key, queryFn: () => http.get<ChatMsg[]>(`/rfps/${rfpId}/chat`) });
  const msgs = history.data ?? [];

  const send = useMutation({
    mutationFn: (message: string) => http.post<{ user: ChatMsg; assistant: ChatMsg }>(`/rfps/${rfpId}/chat`, { message, focus_requirement_id: selected, reviewer }),
    onMutate: (message) => {
      qc.setQueryData<ChatMsg[]>(key, (old = []) => [...old, { id: -Date.now(), role: "user", content: message, meta: {}, ts: new Date().toISOString() }]);
      setText("");
    },
    onSuccess: ({ assistant }) => {
      qc.invalidateQueries({ queryKey: key });
      if (CHANGES.has(assistant.meta.intent ?? "")) {
        ["answers", "summary", "audit"].forEach((k) => qc.invalidateQueries({ queryKey: [k] }));
      }
      if (assistant.meta.select) onSelect(assistant.meta.select);
    },
    onError: (e: Error) => { toast("bad", e.message); qc.invalidateQueries({ queryKey: key }); },
  });
  const clear = useMutation({ mutationFn: () => http.del(`/rfps/${rfpId}/chat`), onSuccess: () => qc.invalidateQueries({ queryKey: key }) });

  useEffect(() => { if (open) end.current?.scrollIntoView({ block: "end" }); }, [msgs.length, open, send.isPending]);

  const byReq = new Map(answers.map((a) => [a.req_id, a.requirement_id]));
  const submit = (m: string) => m.trim() && !send.isPending && (setOpen(true), send.mutate(m.trim()));

  return (
    <section aria-label="Chat with RFP AI" className="card flex flex-col">
      <div className="flex items-center gap-2 px-3 py-2">
        <button className="flex items-center gap-2 text-sm font-semibold" onClick={() => setOpen((o) => !o)} aria-expanded={open}>
          <MessageSquare className="size-4 text-accent" /> Ask RFP AI
          {msgs.length > 0 && <span className="text-xs font-normal text-muted">{Math.ceil(msgs.length / 2)} exchange{msgs.length > 2 ? "s" : ""}</span>}
          {open ? <ChevronDown className="size-4" /> : <ChevronUp className="size-4" />}
        </button>
        {!open && <div className="hidden min-w-0 flex-1 gap-1.5 overflow-x-auto md:flex">
          {PROMPTS.map((p) => <button key={p} className="btn btn-sm whitespace-nowrap" onClick={() => submit(p)}>{p}</button>)}
        </div>}
        {open && msgs.length > 0 && <button className="btn btn-ghost btn-sm ml-auto" onClick={() => clear.mutate()} title="Clear this conversation"><Trash2 className="size-3.5" /></button>}
      </div>

      {open && (
        <div className="h-56 overflow-y-auto border-t border-line px-3 py-2" role="log" aria-live="polite">
          {msgs.length === 0 && !send.isPending && (
            <p className="text-sm text-muted">Ask me about this RFP or about what your approved sources say. Type <b>help</b> to see what I can do. I never change an answer unless you name it.</p>
          )}
          {msgs.map((m) => (
            <div key={m.id} className={cx("mb-2 flex", m.role === "user" ? "justify-end" : "justify-start")}>
              <div className={cx("max-w-[85%] rounded-xl px-3 py-2 text-sm", m.role === "user" ? "bg-accent text-on-solid" : "bg-panel2")}>
                <p className="whitespace-pre-wrap">{m.content}</p>
                {m.role === "assistant" && (m.meta.refs?.length ?? 0) > 0 && (
                  <div className="mt-1.5 flex flex-wrap gap-1">
                    {m.meta.refs!.filter((r) => byReq.has(r)).map((r) => <button key={r} className="chip chip-info hover:opacity-80" onClick={() => onSelect(byReq.get(r)!)}>{r}</button>)}
                  </div>
                )}
                {m.role === "assistant" && (m.meta.sources?.length ?? 0) > 0 && (
                  <ul className="mt-2 space-y-1 border-t border-line pt-1.5 text-xs text-muted">
                    {m.meta.sources!.map((s) => <li key={s.marker}><Chip tone="info">{s.marker}</Chip> {s.doc_title}, {s.location}</li>)}
                  </ul>
                )}
              </div>
            </div>
          ))}
          {send.isPending && <p className="flex items-center gap-2 text-sm text-muted"><Spinner className="size-3.5" /> Thinking…</p>}
          <div ref={end} />
        </div>
      )}

      <form className="flex gap-2 border-t border-line p-2" onSubmit={(e) => { e.preventDefault(); submit(text); }}>
        <label className="sr-only" htmlFor="chat-in">Message</label>
        <input id="chat-in" className="input" placeholder={selected ? "Ask, or say “rewrite it shorter” for the selected question…" : "Ask about this RFP…"}
          value={text} onChange={(e) => setText(e.target.value)} onFocus={() => setOpen(true)} maxLength={2000} autoComplete="off" />
        <button className="btn btn-primary" disabled={!text.trim() || send.isPending} aria-label="Send"><Send className="size-4" /></button>
      </form>
    </section>
  );
}
