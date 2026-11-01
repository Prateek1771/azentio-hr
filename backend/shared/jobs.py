"""The transactional outbox + short-task queue, on Postgres (ADR 0007, narrowed by ADR 0011).

- Outbox: `enqueue(..., conn=c)` inside the caller's transaction commits the job with the business change,
  so the follow-up can't be lost. From Phase 4 the kinds `temporal.start` / `temporal.signal` relay
  committed changes to Temporal (idempotency key = workflow id); long-running workflows live there.
- Short tasks: single side effects and local ops work run here directly.

- enqueue(kind, payload, idempotency_key=...) is a no-op if that key already exists.
- Workers claim with FOR UPDATE SKIP LOCKED, so no job is claimed twice.
- Failure -> retry with exponential backoff; after max_attempts -> 'dead' + an 'ops' exception in work_queue.
- Handlers must key external side effects on job["idempotency_key"] so a retry can't repeat them.
"""
import asyncio
import inspect
import os
import socket
import traceback
from datetime import datetime, timedelta, timezone

import logging_setup as ls
from shared import db

_log = ls.get("jobs")
HANDLERS: dict[str, callable] = {}
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"
STUCK_AFTER = timedelta(minutes=10)


def handler(kind: str):
    def deco(fn):
        HANDLERS[kind] = fn
        return fn
    return deco


def backoff(attempts: int) -> timedelta:
    return timedelta(seconds=min(5 * 2 ** attempts, 3600))


def enqueue(kind: str, payload: dict | None = None, idempotency_key: str | None = None,
            run_at: datetime | None = None, max_attempts: int = 5, conn=None) -> int | None:
    """Queue a job. Returns its id, or None when the idempotency key was already used.
    Pass `conn` to enqueue inside the caller's transaction (job and change commit together)."""
    sql = """INSERT INTO jobs (kind, payload, idempotency_key, run_at, max_attempts)
             VALUES (%s, %s, %s, coalesce(%s, now()), %s)
             ON CONFLICT (idempotency_key) DO NOTHING RETURNING id"""
    row = db.fetch1(sql, (kind, db.Json(payload or {}), idempotency_key, run_at, max_attempts), conn=conn)
    return row["id"] if row else None


def claim(worker: str = WORKER_ID) -> dict | None:
    return db.fetch1("""UPDATE jobs SET state='running', locked_by=%s, locked_at=now(), attempts=attempts+1
                        WHERE id = (SELECT id FROM jobs WHERE state='queued' AND run_at <= now()
                                    ORDER BY run_at, id FOR UPDATE SKIP LOCKED LIMIT 1)
                        RETURNING *""", (worker,))


def _finish(job: dict) -> None:
    db.execute("UPDATE jobs SET state='done', finished_at=now(), locked_by=NULL, last_error=NULL WHERE id=%s", (job["id"],))


def _fail(job: dict, err: str) -> None:
    if job["attempts"] >= job["max_attempts"]:
        with db.tx() as c:
            c.execute("UPDATE jobs SET state='dead', finished_at=now(), locked_by=NULL, last_error=%s WHERE id=%s",
                      (err[-4000:], job["id"]))
            import work_queue
            work_queue.open_item("ops", "job", str(job["id"]), f"Job '{job['kind']}' failed {job['attempts']} times",
                                 assignee_role="admin", detail={"kind": job["kind"], "error": err[-1000:]},
                                 dedupe_key=f"job-dead:{job['id']}", conn=c)
        ls.log_event(_log, "job.dead", id=job["id"], kind=job["kind"])
        return
    db.execute("UPDATE jobs SET state='queued', run_at=%s, locked_by=NULL, last_error=%s WHERE id=%s",
               (datetime.now(timezone.utc) + backoff(job["attempts"]), err[-4000:], job["id"]))
    ls.log_event(_log, "job.retry", id=job["id"], kind=job["kind"], attempt=job["attempts"])


async def run_job(job: dict) -> bool:
    fn = HANDLERS.get(job["kind"])
    try:
        if fn is None:
            raise LookupError(f"no handler for job kind '{job['kind']}'")
        res = fn(job)
        if inspect.isawaitable(res):
            await res
    except Exception:
        await asyncio.to_thread(_fail, job, traceback.format_exc())
        return False
    await asyncio.to_thread(_finish, job)
    return True


async def run_once(worker: str = WORKER_ID) -> bool:
    """Claim and run one job. Returns False when nothing was ready."""
    job = await asyncio.to_thread(claim, worker)
    if not job:
        return False
    await run_job(job)
    return True


def requeue_stuck() -> int:
    """Jobs left 'running' by a crashed worker go back to the queue (the attempt still counts)."""
    return db.execute("UPDATE jobs SET state='queued', locked_by=NULL WHERE state='running' AND locked_at < now() - %s",
                      (STUCK_AFTER,))


def heartbeat(worker: str = WORKER_ID) -> None:
    db.execute("""INSERT INTO worker_heartbeat (worker, seen_at) VALUES (%s, now())
                  ON CONFLICT (worker) DO UPDATE SET seen_at = now()""", (worker,))


def worker_alive(within: timedelta = timedelta(seconds=30)) -> bool:
    return bool(db.fetch1("SELECT 1 AS x FROM worker_heartbeat WHERE seen_at > now() - %s", (within,)))


async def worker(idle: float = 2.0) -> None:
    """Long-running loop started from the app lifespan."""
    ls.log_event(_log, "worker.start", worker=WORKER_ID)
    tick = 0
    while True:
        try:
            if tick % 5 == 0:
                await asyncio.to_thread(heartbeat)
                await asyncio.to_thread(requeue_stuck)
            tick += 1
            if not await run_once():
                await asyncio.sleep(idle)
        except asyncio.CancelledError:
            raise
        except Exception as e:  # DB blip: keep the worker alive, /ready reports the outage
            ls.log_event(_log, "worker.error", error=type(e).__name__)
            await asyncio.sleep(idle * 2)
