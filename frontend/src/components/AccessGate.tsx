import { useCallback, useEffect, useState, type ReactNode } from "react";
import { KeyRound, RefreshCw, ServerCrash } from "lucide-react";
import { API_BASE, apiUrl, getAccessKey, setAccessKey } from "../api";
import { Spinner } from "../ui";

type State = "checking" | "open" | "ask" | "down";

/** Shown before anything else. If the backend wants an access key it asks for it once; if the backend is asleep (a free
 *  host sleeps when idle and takes up to a minute to wake) it says so instead of showing broken pages. */
export default function AccessGate({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>("checking");
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const verify = useCallback(async (key: string) => {
    const r = await fetch(apiUrl("/llm/status"), { headers: key ? { "X-Access-Key": key } : undefined });
    return r.ok;
  }, []);

  const check = useCallback(async () => {
    setState("checking");
    try {
      const health = await (await fetch(apiUrl("/health"))).json();
      if (!health.access_key_required) return setState("open");
      const key = getAccessKey();
      setState(key && (await verify(key)) ? "open" : "ask");
    } catch {
      setState("down");
    }
  }, [verify]);

  useEffect(() => {
    void check();
    const again = () => { setAccessKey(""); setError("The access key was refused. Enter it again."); setState("ask"); };
    window.addEventListener("rfpai:auth", again);
    return () => window.removeEventListener("rfpai:auth", again);
  }, [check]);

  const submit = async () => {
    setBusy(true); setError("");
    try {
      if (await verify(value.trim())) { setAccessKey(value.trim()); setState("open"); }
      else setError("That key is not right.");
    } catch {
      setState("down");
    } finally {
      setBusy(false);
    }
  };

  if (state === "open") return <>{children}</>;
  return (
    <div className="grid min-h-screen place-items-center px-4">
      <div className="card w-full max-w-sm space-y-4 p-6 text-center" role="dialog" aria-label="RFP AI access">
        <p className="text-lg font-bold">RFP AI</p>
        {state === "checking" && (
          <>
            <Spinner className="mx-auto size-6" />
            <p className="text-sm text-muted">Connecting to the server. If it has been idle this can take up to a minute while it wakes up.</p>
          </>
        )}
        {state === "down" && (
          <>
            <ServerCrash className="mx-auto size-6 text-bad" />
            <p className="text-sm">The server did not answer{API_BASE ? ` at ${API_BASE}` : ""}.</p>
            <p className="text-xs text-muted">It may still be starting. Try again in a moment.</p>
            <button className="btn btn-primary" onClick={() => void check()}><RefreshCw className="size-4" /> Try again</button>
          </>
        )}
        {state === "ask" && (
          <form onSubmit={(e) => { e.preventDefault(); void submit(); }} className="space-y-3 text-left">
            <p className="flex items-center gap-2 text-sm"><KeyRound className="size-4" /> This site asks for an access key.</p>
            <div>
              <label className="label" htmlFor="access-key">Access key</label>
              <input id="access-key" type="password" autoComplete="off" className="input" value={value} onChange={(e) => setValue(e.target.value)} autoFocus />
            </div>
            {error && <p role="alert" className="text-sm text-bad">{error}</p>}
            <button className="btn btn-primary w-full" disabled={!value.trim() || busy}>{busy && <Spinner className="size-4" />} Continue</button>
          </form>
        )}
      </div>
    </div>
  );
}
