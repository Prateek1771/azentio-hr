"""Quality dashboard: Ragas eval runs and guardrail hits."""
import asyncio
import time

from fastapi import APIRouter, HTTPException

import evals
import guardrails

router = APIRouter(prefix="/api/quality")
_tasks: set[asyncio.Task] = set()  # keep a reference so the run isn't garbage-collected mid-flight


@router.get("")
def overview(days: int = 30):
    runs = evals.runs(20)
    latest = next((r for r in runs if r["status"] == "succeeded"), None)
    results = evals.results(latest["id"]) if latest else []
    by_rule = guardrails.by_rule(time.time() - days * 86400)
    recent = guardrails.recent(25)
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
