import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, RefreshCw } from "lucide-react";
import { http } from "../api";
import { useKbStatus, useLlmStatus } from "../hooks";
import { Chip, ErrorNote, Spinner, cx, useToast } from "../ui";

const TONE = { ready: "ok", cooldown: "warn", exhausted: "bad", disabled: "bad" } as const;

export default function Settings() {
  const qc = useQueryClient();
  const toast = useToast();
  const { data, isLoading, error, dataUpdatedAt } = useLlmStatus();
  const kb = useKbStatus();
  const reset = useMutation({
    mutationFn: () => http.post("/llm/reset-keys"),
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["llm"] }); toast("ok", "Key state cleared"); },
  });
  return (
    <div className="mx-auto max-w-5xl space-y-5">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div>
          <h1 className="text-xl font-bold">Settings</h1>
          <p className="text-sm text-muted">Model access and quota. Keys live only in the backend <code>.env</code>; this page shows aliases, never keys.</p>
        </div>
        <button className="btn" onClick={() => reset.mutate()} disabled={reset.isPending} title="Forget cooldowns, exhaustion and disabled keys, for example after replacing a bad key"><RefreshCw className="size-4" /> Reset key state</button>
      </div>

      {isLoading ? <Spinner /> : error ? <ErrorNote error={error} /> : data && (
        <>
          <section aria-label="Model quota" className="space-y-3">
            <h2 className="text-sm font-semibold">Model quota <span className="font-normal text-muted">· updated {new Date(dataUpdatedAt).toLocaleTimeString()}</span></h2>
            {data.providers.map((p) => (
              <div key={p.provider} className="card p-4">
                <h3 className="mb-2 flex items-center gap-2 font-semibold capitalize"><KeyRound className="size-4 text-accent" /> {p.provider}
                  <span className="text-xs font-normal text-muted">{p.configured_keys} key{p.configured_keys === 1 ? "" : "s"}</span></h3>
                {p.keys.length === 0 ? <p className="text-sm text-muted">No keys configured. Add them to <code>.env</code> and restart.</p> : (
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm">
                      <thead><tr className="text-left text-xs text-muted"><th className="py-1 pr-3">Key</th><th className="py-1 pr-3">Last 24h</th><th className="py-1">Models</th></tr></thead>
                      <tbody>
                        {p.keys.map((k) => (
                          <tr key={k.alias} className="border-t border-line/60 align-top">
                            <td className="py-2 pr-3 font-mono text-xs">{k.alias}</td>
                            <td className="py-2 pr-3 text-xs text-muted whitespace-nowrap">{k.calls_24h} calls · {k.ok_24h} ok · {k.tokens_24h.toLocaleString()} tokens</td>
                            <td className="py-2">
                              <div className="flex flex-wrap gap-1">
                                {k.disabled ? <Chip tone="bad" title={k.disabled_reason ?? ""}>key rejected</Chip> : k.models.map((m) => (
                                  <Chip key={m.model} tone={TONE[m.status]} title={m.last_error ?? m.model}>
                                    {m.model.split("/").pop()}: {m.status}{m.seconds_left > 0 ? ` ${m.seconds_left}s` : ""}
                                  </Chip>
                                ))}
                              </div>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            ))}
          </section>

          <section aria-label="Task chains" className="card p-4">
            <h2 className="mb-1 text-sm font-semibold">How a request finds a model</h2>
            <p className="mb-3 text-xs text-muted">Each task tries its list in order. A rate-limited key rotates to the next key, then the next model. Repeat prompts are served from the cache at no quota cost ({data.cache_entries} cached).</p>
            <div className="grid gap-3 md:grid-cols-3">
              {Object.entries(data.tasks).map(([task, chain]) => (
                <div key={task}>
                  <h3 className="h-section mb-1">{task}</h3>
                  <ol className="list-decimal space-y-0.5 pl-4 text-xs">
                    {chain.map((c) => <li key={c.provider + c.model}><span className="font-medium capitalize">{c.provider}</span> <span className="text-muted">{c.model.split("/").pop()}</span></li>)}
                  </ol>
                </div>
              ))}
            </div>
          </section>
        </>
      )}

      <section className="card p-4 text-sm">
        <h2 className="mb-1 text-sm font-semibold">Search</h2>
        <p className="text-muted">Mode: <b className={cx(kb.data?.retrieval_mode === "hybrid" ? "text-ok" : "text-warn")}>{kb.data?.retrieval_mode ?? "-"}</b>.{" "}
          {kb.data?.retrieval_mode === "keyword" && "The local embedding model is not downloaded, so only keyword search runs. Fetch it with scripts/fetch_embedding_model.py."}</p>
      </section>
    </div>
  );
}
