"""Quality dashboard: Ragas eval runs and guardrail hits."""
import asyncio
import json
import time

from fastapi import APIRouter, HTTPException

import evals
from graph import q

router = APIRouter(prefix="/api/quality")
_tasks: set[asyncio.Task] = set()  # keep a reference so the run isn't garbage-collected mid-flight


@router.get("")
def overview(days: int = 30):
    runs = [dict(r["r"], summary=json.loads(r["r"].get("summary") or "{}"))
            for r in q("MATCH (r:EvalRun) RETURN r ORDER BY r.ts DESC LIMIT 20")]
    latest = next((r for r in runs if r["status"] == "succeeded"), None)
    results = [dict(x["x"], sources=json.loads(x["x"].get("sources") or "[]"))
               for x in q("MATCH (x:EvalResult {run_id:$id}) RETURN x ORDER BY x.n", id=latest["id"])] if latest else []
    since = time.time() - days * 86400
    by_rule = q("""MATCH (g:GuardrailEvent) WHERE g.ts > $t
                   RETURN g.rule AS rule, g.action AS action, count(g) AS n ORDER BY n DESC""", t=since)
    recent = [r["g"] for r in q("MATCH (g:GuardrailEvent) RETURN g ORDER BY g.ts DESC LIMIT 25")]
    return {"runs": runs, "latest": latest, "results": results, "judge": evals.judge_model(),
            "guardrails": {"days": days, "by_rule": by_rule, "recent": recent}}


@router.post("/eval")
async def start_eval():
    if evals.running():
        raise HTTPException(409, "An evaluation is already running. Wait for it to finish.")
    rid = evals.start_run()
    task = asyncio.create_task(evals.run(rid))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return {"id": rid}
