"""Ragas evaluation of the employee assistant against seed/eval_assistant.json.

Each question goes through the real retrieval, prompt and model (as the seeded employee), then a judge model
scores it with Ragas: faithfulness (answer sticks to the retrieved policy text), context precision and context
recall (the right sections were retrieved). Guardrails are scored without any model: the attack prompts must be
blocked and answers must pass the output checks. Results land in Postgres (`eval_runs`, `eval_results`) for the
Quality dashboard.

Judge: RAGAS_JUDGE_MODEL / RAGAS_JUDGE_BASE_URL / RAGAS_JUDGE_API_KEY, defaulting to the app's own LLM settings.
Free models make noisy judges; a stronger paid judge gives steadier scores.

CLI: uv run python evals.py
"""
import asyncio
import json
import os
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent / ".env")  # before llm reads its settings (CLI use)

from openai import AsyncOpenAI  # noqa: E402

import chat  # noqa: E402
import checklist  # noqa: E402
import guardrails  # noqa: E402
import llm  # noqa: E402
import logging_setup as ls  # noqa: E402
import graph_migration as gm  # noqa: E402
from graph import new_id  # noqa: E402
from shared import db  # noqa: E402

log = ls.get("evals")
GOLDEN = Path(__file__).parent / "seed" / "eval_assistant.json"
METRICS = ("faithfulness", "context_precision", "context_recall")
STALE_AFTER = 60 * 60
_NO_ANSWER = ("Sorry, the assistant is unavailable", "Sorry, I couldn't put an answer together")  # chat._stream fallbacks


def judge_model() -> str:
    return os.getenv("RAGAS_JUDGE_MODEL") or llm.MODEL


def _metrics():
    from ragas.llms import llm_factory  # ponytail: imported lazily, ragas + langchain add seconds to startup
    from ragas.metrics.collections import ContextPrecisionWithReference, ContextRecall, Faithfulness
    client = AsyncOpenAI(base_url=os.getenv("RAGAS_JUDGE_BASE_URL") or llm.BASE_URL,
                         api_key=os.getenv("RAGAS_JUDGE_API_KEY") or llm.API_KEY or "missing")
    judge = llm_factory(judge_model(), provider="openai", client=client)
    return Faithfulness(llm=judge), ContextPrecisionWithReference(llm=judge), ContextRecall(llm=judge)


async def _score(metric, **kw) -> float | None:
    try:
        return float((await metric.ascore(**kw)).value)
    except Exception as e:  # a judge that returns bad JSON costs one score, not the run
        ls.log_event(log, "eval.metric_failed", metric=metric.name, error=type(e).__name__)
        return None


def _mean(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def running() -> dict | None:
    return db.fetch1("SELECT id FROM eval_runs WHERE status='running' AND ts > %s", (time.time() - STALE_AFTER,))


def start_run() -> str:
    rid = new_id("eval")
    db.execute("INSERT INTO eval_runs (id, ts, status, judge, model) VALUES (%s, %s, 'running', %s, %s)",
               (rid, time.time(), judge_model() or "", llm.MODEL or ""))
    return rid


def _present(row: dict) -> dict:
    """FalkorDB never stored NULL; readers keep the graph's shape (a missing metric is an absent key)."""
    return {k: v for k, v in row.items() if v is not None}


def runs(limit: int = 20) -> list[dict]:
    return [_present(r) for r in db.fetch("SELECT * FROM eval_runs ORDER BY ts DESC LIMIT %s", (limit,))]


def results(run_id: str) -> list[dict]:
    return [_present(r) for r in db.fetch("SELECT * FROM eval_results WHERE run_id=%s ORDER BY n", (run_id,))]


def _set(rid: str, **cols) -> None:
    sets = ", ".join(f"{k}=%s" for k in cols)
    db.execute(f"UPDATE eval_runs SET {sets} WHERE id=%s",
               tuple(db.Json(v) if isinstance(v, dict) else v for v in cols.values()) + (rid,))


async def run(rid: str) -> dict:
    golden = json.loads(GOLDEN.read_text(encoding="utf-8"))
    questions, attacks = golden["questions"], golden["attacks"]
    _set(rid, total=len(questions))
    try:
        import records
        emp = records.employee_by(email=golden["employee_email"])
        if not emp:
            raise RuntimeError(f"seed employee {golden['employee_email']} not found")
        items = checklist.employee_items(emp["id"])
        hr = next((i["owner_email"] for i in items if i["category"] == "HR" and i.get("owner_email")), "your HR partner")
        faith, precision, recall = _metrics()
        rows = []
        for n, item in enumerate(questions, 1):
            question, reference = item["question"], item["reference"]
            with ls.Timer() as t:
                chunks = await chat.retrieve(question, k=4)
                messages = [{"role": "system", "content": chat.SYSTEM.format(hr=hr) + "\n\nCONTEXT:\n" + chat.build_context(emp, items, chunks)},
                            {"role": "user", "content": question}]
                answer = "".join([p async for p in chat._stream(messages, hr, None, "Eval", chunks, log_activity=False)])
            note = guardrails.OUTPUT_NOTE.format(contact=hr)
            flagged = answer.endswith(note)
            answer = answer.removesuffix(note)
            contexts = [c["text"] for c in chunks]
            answered = not answer.startswith(_NO_ANSWER)
            f, p, r = await asyncio.gather(
                _score(faith, user_input=question, response=answer, retrieved_contexts=contexts),
                _score(precision, user_input=question, reference=reference, retrieved_contexts=contexts),
                _score(recall, user_input=question, retrieved_contexts=contexts, reference=reference)) if answered else (None, None, None)
            row = {"question": question, "reference": reference, "answer": answer, "ms": t.ms, "guardrail_ok": not flagged, "answered": answered,
                   "sources": [f"{c['source']} § {c['section']}" for c in chunks],
                   "faithfulness": f, "context_precision": p, "context_recall": r}
            rows.append(row)
            _insert_result({"id": new_id("er"), "run_id": rid, "n": n, **row})
            _set(rid, done=n)
        answered = [r for r in rows if r["answered"]]
        if not answered:
            raise RuntimeError("The assistant model didn't answer any question (outage or rate limit)")
        blocked = sum(guardrails.check_input(a, []) is not None for a in attacks)
        summary = {m: _mean([r[m] for r in answered]) for m in METRICS}
        summary |= {"answered": len(answered), "scored": sum(r["faithfulness"] is not None for r in answered)}
        summary |= {"attacks_blocked": blocked, "attacks": len(attacks),
                    "output_guardrail_pass": round(sum(r["guardrail_ok"] for r in rows) / max(len(rows), 1), 3),
                    "avg_ms": _mean([r["ms"] for r in rows])}
        _set(rid, status="succeeded", summary=summary, finished=time.time())
        ls.log_event(log, "eval.finished", run=rid, **summary)
        return summary
    except Exception as e:
        _set(rid, status="failed", error=f"{type(e).__name__}: {e}"[:300], finished=time.time())
        ls.log_event(log, "eval.failed", run=rid, error=type(e).__name__)
        raise


RESULT_COLS = ("id", "run_id", "n", "question", "reference", "answer", "ms", "guardrail_ok", "answered", "faithfulness",
               "context_precision", "context_recall", "sources")
RUN_COLS = ("id", "ts", "status", "judge", "model", "summary", "done", "total", "finished", "error")


def _insert_result(r: dict, conn=None) -> None:
    vals = [db.Json(r.get(c) or []) if c == "sources" else r.get(c) for c in RESULT_COLS]
    db.execute(f"INSERT INTO eval_results ({', '.join(RESULT_COLS)}) VALUES ({', '.join(['%s'] * len(RESULT_COLS))})",
               vals, conn=conn)


def _num(v):
    return None if v is None else float(v)


def _canon_run(r: dict) -> dict:
    return {"id": r["id"], "ts": gm.epoch(r.get("ts")), "status": r.get("status") or "", "judge": r.get("judge") or "",
            "model": r.get("model") or "", "summary": gm.jsonv(r.get("summary"), {}), "done": int(r.get("done") or 0),
            "total": int(r.get("total") or 0), "finished": gm.epoch(r.get("finished")), "error": r.get("error")}


def _canon_result(r: dict) -> dict:
    return {"id": r["id"], "run_id": r.get("run_id"), "n": int(r.get("n") or 0), "question": r.get("question") or "",
            "reference": r.get("reference") or "", "answer": r.get("answer") or "", "ms": _num(r.get("ms")),
            "guardrail_ok": r.get("guardrail_ok"), "answered": r.get("answered"),
            **{m: _num(r.get(m)) for m in ("faithfulness", "context_precision", "context_recall")},
            "sources": gm.jsonv(r.get("sources"), [])}


gm.register(gm.LabelSpec(
    label="EvalRun", batch="B", table="eval_runs",
    to_row=lambda p: {**_canon_run(p), "ts": float(p.get("ts") or 0), "finished": _num(p.get("finished"))},
    canonical=_canon_run,
    from_row=lambda r: _present({**r, "summary": json.dumps(r["summary"] or {})}),
    mapped_keys=frozenset(RUN_COLS)))

gm.register(gm.LabelSpec(
    label="EvalResult", batch="B", table="eval_results",
    to_row=_canon_result, canonical=_canon_result,
    from_row=lambda r: _present({**r, "sources": json.dumps(r["sources"] or [])}),
    mapped_keys=frozenset(RESULT_COLS)))


if __name__ == "__main__":
    ls.setup()
    print(json.dumps(asyncio.run(run(start_run())), indent=2))
