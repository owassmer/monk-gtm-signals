"""Agent judgments via the local Claude Code CLI (OAuth session; no API key).

Every judgment is cached in the `judgments` table keyed by (task, input), so each
question is asked once, the reasoning is auditable, and reruns are free."""

import hashlib
import json
import os
import subprocess
import tempfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from .util import now_iso

MODEL = os.environ.get("MONK_AGENT_MODEL", "sonnet")

DDL = """CREATE TABLE IF NOT EXISTS judgments (
  task TEXT, input_key TEXT, input TEXT, output TEXT, model TEXT, created TEXT,
  PRIMARY KEY (task, input_key))"""


def _key(obj):
    return hashlib.sha1(json.dumps(obj, sort_keys=True).encode()).hexdigest()


def ask(system, prompt, schema, model=None, timeout=300, tools=None):
    """One structured call. Returns the parsed object matching `schema`."""
    cmd = ["claude", "-p", "--model", model or MODEL, "--output-format", "json",
           "--tools", ",".join(tools or []), *(["--allowedTools", ",".join(tools)] if tools else []),
           "--strict-mcp-config", "--setting-sources", "",
           "--no-session-persistence", "--system-prompt", system,
           "--json-schema", json.dumps(schema)]
    with tempfile.TemporaryDirectory() as cwd:          # no project context leaks in
        p = subprocess.run(cmd, input=prompt, capture_output=True, text=True, timeout=timeout, cwd=cwd)
    if p.returncode != 0:
        raise RuntimeError(f"claude exited {p.returncode}: {p.stderr[-400:]}")
    out = json.loads(p.stdout)
    if out.get("is_error") or out.get("structured_output") is None:
        raise RuntimeError(f"claude returned no structured output: {str(out.get('result'))[:400]}")
    return out["structured_output"]


def judge_many(con, task, items, build, schema, system, batch=60, workers=4, model=None, tools=None, timeout=300):
    """Judge `items` (list of dicts) in batches, caching per item.
    build(batch_items) -> prompt; schema must return {"results": [{"id": ..., ...}]}.
    Returns {item_id: judgment}."""
    con.execute(DDL)
    out, todo = {}, []
    for it in items:
        k = _key({"task": task, "item": it})
        row = con.execute("SELECT output FROM judgments WHERE task=? AND input_key=?", (task, k)).fetchone()
        if row:
            out[it["id"]] = json.loads(row[0])
        else:
            todo.append((k, it))
    batches = [todo[i:i + batch] for i in range(0, len(todo), batch)]

    def run(b):
        res = ask(system, build([it for _, it in b]), schema, model=model, tools=tools, timeout=timeout)
        return b, {str(r["id"]): r for r in res.get("results", [])}

    with ThreadPoolExecutor(workers) as ex:
        futs = [ex.submit(run, b) for b in batches]
        for n, fut in enumerate(as_completed(futs), 1):
            try:
                b, res = fut.result()
            except Exception as e:
                print(f"    {task}: batch failed ({str(e)[:120]}); rerun to retry", flush=True)
                continue
            for k, it in b:
                j = res.get(str(it["id"]))
                if j is None:
                    continue
                out[it["id"]] = j
                con.execute("INSERT OR REPLACE INTO judgments VALUES (?,?,?,?,?,?)",
                            (task, k, json.dumps(it), json.dumps(j), model or MODEL, now_iso()))
            con.commit()
            print(f"    {task}: batch {n}/{len(batches)} done", flush=True)
    return out
