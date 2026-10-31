"""Upload the live-test knowledge base through the same API the Knowledge Base page uses.

    cd backend && .venv/bin/python ../live_test/load_kb.py [--api http://localhost:8000] [--no-conflicts]

Each file goes up with the metadata in live_test/kb_metadata.json (the guide lists it too, so you can do
the same by hand in the UI). Unless --no-conflicts is given, conflict detection runs afterwards; that
step calls the models and so uses a little quota.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", default="http://localhost:8000")
    ap.add_argument("--no-conflicts", action="store_true")
    args = ap.parse_args()

    meta = json.loads((HERE / "kb_metadata.json").read_text(encoding="utf-8"))
    base = args.api.rstrip("/") + "/api"
    with httpx.Client(timeout=120) as http:
        have = {d["title"] for d in http.get(f"{base}/kb/documents").json()}
        for m in meta:
            if m["title"] in have or (m["customer"] and "[Customer]" in " ".join(have)
                                     and m["title"].replace(m["customer"], "[Customer]") in have):
                print(f"  already loaded  {m['file']}")
                continue
            form = {"title": m["title"], "kind": m["kind"], "version": m["version"], "doc_date": m["doc_date"],
                    "owner": m["owner"], "customer": m["customer"],
                    "reuse_permitted": str(m["reuse_permitted"]).lower(), "answerable": str(m["answerable"]).lower(),
                    "reviewer": "live-test"}
            path = HERE / "kb" / m["file"]
            r = http.post(f"{base}/kb/documents", data=form, files={"file": (m["file"], path.read_bytes())})
            if r.status_code != 201:
                print(f"  FAILED          {m['file']}: {r.status_code} {r.text[:200]}")
                return 1
            out = r.json()
            note = f"  ({'; '.join(out['warnings'])})" if out["warnings"] else ""
            print(f"  loaded          {m['file']}: {out['chunks']} passages{note}")
        status = http.get(f"{base}/kb/status").json()
        print(f"Knowledge base: {status['documents']} sources, {status['chunks']} passages, "
              f"search mode {status['retrieval_mode']}")
        if not args.no_conflicts:
            print("Detecting conflicts (uses the models)...")
            res = http.post(f"{base}/kb/detect-conflicts").json()
            print(f"  {res}")
            for c in http.get(f"{base}/kb/conflicts").json():
                print(f"  [{c['status']}] {c['topic']}: {c['older']['title']} vs {c['newer']['title']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
