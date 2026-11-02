"""Grounded onboarding assistant: policy chunks (vector search) + the employee's own checklist."""
import json
from datetime import date
import re

import guardrails
import llm
import logging_setup as ls
from graph import q, new_id, event


def chunk_markdown(source: str, text: str) -> list[dict]:
    """Split on '## ' headings; each chunk keeps the doc title for context."""
    title = next((l[2:].strip() for l in text.splitlines() if l.startswith("# ")), source)
    parts = re.split(r"^## ", text, flags=re.M)
    chunks = []
    for p in parts[1:] or parts:
        heading, _, body = p.partition("\n")
        chunks.append({"source": source, "title": title, "section": heading.strip(),
                       "text": f"{title} — {heading.strip()}\n{body.strip()}"})
    return chunks


async def embed(texts: list[str]) -> list[list[float]]:
    if not llm.EMBED_MODEL:
        raise RuntimeError("no embedding model configured")
    res = await llm.client.embeddings.create(model=llm.EMBED_MODEL, input=texts)
    return [d.embedding for d in res.data]


async def index_policy(source: str, text: str):
    """Store an uploaded policy (Postgres is the record), then rebuild its retrieval chunks."""
    import policies
    policies.upsert(source, text)
    await index_chunks(source, text)


async def reindex_policies() -> int:
    """Rebuild the whole PolicyChunk index from the policies table (the graph is a recoverable index)."""
    import policies
    rows = policies.all()
    q("MATCH (c:PolicyChunk) DELETE c")
    for p in rows:
        await index_chunks(p["source"], p["text"])
    return len(rows)


async def index_chunks(source: str, text: str):
    q("MATCH (c:PolicyChunk {source:$s}) DELETE c", s=source)
    chunks = chunk_markdown(source, text)
    try:
        vecs = await embed([c["text"] for c in chunks])
    except Exception:  # no embedding model / outage: keyword retrieval covers it
        vecs = [None] * len(chunks)
    for c, vec in zip(chunks, vecs):
        q("CREATE (c:PolicyChunk {id:$id, source:$s, title:$t, section:$sec, text:$x})",
          id=new_id("pc"), s=c["source"], t=c["title"], sec=c["section"], x=c["text"])
        if vec:
            q("MATCH (c:PolicyChunk {source:$s, section:$sec}) SET c.embedding = vecf32($v)", s=c["source"], sec=c["section"], v=vec)


def keyword_retrieve(question: str, k: int) -> list[dict]:
    """ponytail: bag-of-words overlap fallback; fine for a few dozen policy chunks."""
    words = {w for w in re.findall(r"[a-z0-9]+", question.lower()) if len(w) > 2}
    rows = q("MATCH (c:PolicyChunk) RETURN c.source AS source, c.title AS title, c.section AS section, c.text AS text")
    for r in rows:
        r["score"] = -len(words & set(re.findall(r"[a-z0-9]+", r["text"].lower())))
    return sorted(rows, key=lambda r: r["score"])[:k]


async def retrieve(question: str, k: int = 5) -> list[dict]:
    if not llm.EMBED_MODEL:
        return keyword_retrieve(question, k)
    try:
        [vec] = await embed([question])
        rows = q("""CALL db.idx.vector.queryNodes('PolicyChunk', 'embedding', $k, vecf32($v)) YIELD node, score
                    RETURN node.source AS source, node.title AS title, node.section AS section, node.text AS text, score
                    ORDER BY score""", k=k, v=vec)
        if rows:
            return rows
    except Exception:
        pass
    return keyword_retrieve(question, k)


SYSTEM = """You are the Azentio onboarding assistant for a new employee.
Answer ONLY from the CONTEXT below (approved policies and this employee's personal checklist).
- Be specific to this employee: their role, location, grade and current item statuses.
- Cite sources inline as [policy-file.md § Section] or [checklist] for every requirement you state.
- Never invent documents, dates, people or requirements. If the context does not answer the question,
  say you don't have that information and suggest contacting their HR partner ({hr}).
- Be warm and concise. Use short bullet lists for lists of documents or steps."""


def build_context(emp: dict, items: list[dict], chunks: list[dict]) -> str:
    lines = [f"EMPLOYEE: {emp['name']}, {emp['role']} ({emp['grade']}), {emp['department']}, {emp['location']}, "
             f"joining {emp['joining_date']}, manager {emp.get('manager_email', '')}", "", "CHECKLIST [checklist]:"]
    for i in items:
        lines.append(f"- {i['name']} | {i['kind']} | status: {i['status']} | due {i['due']} | owner {i['owner_name']} | why: {i['reason']}")
    lines += ["", "POLICIES:"]
    for c in chunks:
        lines.append(f"[{c['source']} § {c['section']}]\n{c['text']}\n")
    return "\n".join(lines)


async def _stream(messages: list, fallback_contact: str, log_to: str | None, label: str, chunks: list[dict] = (), log_activity: bool = True):
    """Stream an answer; yields text. Logs model + tokens to the activity feed, then runs the output guardrails."""
    try:
        stream, model = await llm.complete(messages, max_tokens=2500, name=f"assistant:{label}", stream=True, stream_options={"include_usage": True})
    except Exception as e:
        yield f"Sorry, the assistant is unavailable right now ({type(e).__name__}). Please try again shortly or contact {fallback_contact}."
        return
    tokens, parts = None, []
    async for chunk in stream:
        if chunk.usage:
            tokens = chunk.usage.total_tokens
        if chunk.choices and chunk.choices[0].delta.content:
            parts.append(chunk.choices[0].delta.content)
            yield parts[-1]
    if not parts:
        yield f"Sorry, I couldn't put an answer together just now. Please try rephrasing, or contact {fallback_contact}."
    # ponytail: output is checked after streaming, so a bad answer gets a visible note rather than being withheld
    issues = guardrails.check_output("".join(parts), list(chunks), messages[0]["content"]) if parts else []
    for rule in issues:
        guardrails.record(label, rule, "flagged")
    if issues:
        yield guardrails.OUTPUT_NOTE.format(contact=fallback_contact)
    if log_activity:
        event(log_to, "ai", f"{label} answered via {model} ({tokens or '?'} tokens)")
    ls.log_event(ls.get("assistant"), "assistant.answered", assistant=label.replace(" ", "_"), model=model, tokens=tokens)


def _convo(history: list[dict]) -> list[dict]:
    return [{"role": h["role"], "content": h["content"]} for h in history[-6:] if h.get("role") in ("user", "assistant")]


def _sources(chunks: list[dict]) -> str:
    return "__SOURCES__" + json.dumps([{"source": c["source"], "section": c["section"], "title": c["title"]} for c in chunks[:3]]) + "\n"


def _blocked(label: str, message: str, history: list[dict]) -> str | None:
    hit = guardrails.check_input(message, history)
    if hit:
        guardrails.record(label, hit[0], "blocked")
        return hit[1]
    return None


async def answer_stream(emp: dict, items: list[dict], message: str, history: list[dict]):
    if reply := _blocked("Assistant", message, history):
        yield _sources([]) + reply
        return
    chunks = await retrieve(message, k=4)
    yield _sources(chunks)
    hr = next((i["owner_email"] for i in items if i["category"] == "HR" and i.get("owner_email")), "your HR partner")
    messages = [{"role": "system", "content": SYSTEM.format(hr=hr) + "\n\nCONTEXT:\n" + build_context(emp, items, chunks)},
                *_convo(history), {"role": "user", "content": message}]
    async for part in _stream(messages, hr, emp["id"], "Assistant", chunks):
        yield part


# ---------- HR assistant: whole-organisation view ----------

HR_SYSTEM = """You are the Azentio HR onboarding co-pilot for the People Team (today is {today}).
Answer ONLY from the CONTEXT: live onboarding data for every new joiner, owners, recent activity and policies.
- Be concrete: names, dates, statuses, owners, what is blocking what. Lead with the answer, then short bullets.
- Cite inline as [joiner: Name], [activity] or [policy-file.md § Section].
- Prioritise risk: overdue items, items blocking provisioning, document mismatches, joiners starting soon.
- If the data does not contain the answer, say so plainly. Never invent people, dates or statuses."""


def hr_context(question: str, chunks: list[dict], focus_id: str | None) -> str:
    import checklist
    today = date.today()
    import records
    emps = records.employees()
    qlow = question.lower()
    # ponytail: full item lists for everyone up to 40 joiners; beyond that only joiners named in the question,
    # starting within 14 days, or in focus. Upgrade path: retrieve per-joiner digests by embedding.
    many = len(emps) > 40
    lines = [f"JOINERS ({len(emps)}):"]
    for e in emps:
        items = checklist.employee_items(e["id"])
        by_key = {i["key"]: i for i in items}
        for i in items:
            i["state"] = checklist.view_state(i, by_key, today)
        done = sum(i["state"] == "completed" for i in items)
        days = (date.fromisoformat(e["joining_date"]) - today).days
        cnt = {s: sum(i["state"] == s for i in items) for s in ("overdue", "blocked", "pending")}
        corr = sum(i["status"] == "Correction Requested" for i in items)
        lines.append(f"\n## {e['name']} [joiner: {e['name']}] — {e['role']} {e['grade']}, {e['department']}, {e['location']}; "
                     f"joins {e['joining_date']} ({days:+d} days); {round(100 * done / max(len(items), 1))}% ready "
                     f"({done}/{len(items)}); overdue {cnt['overdue']}, blocked {cnt['blocked']}, corrections {corr}; "
                     f"email request {'sent' if e.get('thread_id') else 'NOT sent'}; email {e['email']}; manager {e['manager_email']}")
        if many and e["id"] != focus_id and e["name"].split()[0].lower() not in qlow and days > 14:
            continue
        for i in items:
            if i["state"] == "completed":
                continue
            waiting = [by_key[d]["name"] for d in i.get("depends", []) if d in by_key and by_key[d]["status"] not in checklist.DONE]
            lines.append(f"- {i['name']} ({i['kind']}): {i['status']}, {i['state']}, due {i['due']}, owner {i['owner_name'] or i['category']}"
                         + (f", waiting on {', '.join(waiting)}" if waiting else ""))
        from documents import store as docs
        for m in docs.mismatches(e["id"]):
            lines.append(f"- FLAG: {m['field']} mismatch on {m['filename']}: profile '{m['expected']}' vs document '{m['found']}'")
        if e["id"] == focus_id:
            lines.append("  Documents received:")
            for d in docs.for_employee(e["id"]):
                ex = {k: v for k, v in (d.get("extracted") or {}).items() if v and not k.startswith("_")}
                lines.append(f"  - {d['filename']} via {d['source']}: {d['status']}; extracted {ex}; issues {d.get('issues')}")
    lines.append("\nOWNERS:")
    import owners
    for o in owners.all():
        lines.append(f"- {o['name']} ({o['type']}, {o['department']}): {o['email']}")
    lines.append("\nRECENT ACTIVITY [activity]:")
    import events
    for ev in events.recent(15, exclude_kind="ai"):
        lines.append(f"- {ev['employee_name'] + ': ' if ev['employee_name'] else ''}{ev['message']}")
    lines.append("\nPOLICIES:")
    for c in chunks:
        lines.append(f"[{c['source']} § {c['section']}]\n{c['text']}\n")
    return "\n".join(lines)


async def hr_answer_stream(message: str, history: list[dict], focus_id: str | None):
    if reply := _blocked("HR co-pilot", message, history):
        yield _sources([]) + reply
        return
    chunks = await retrieve(message, k=4)
    yield _sources(chunks)
    ctx = hr_context(message, chunks, focus_id)
    import records
    fe = records.employee(focus_id) if focus_id else None
    focus = [{"n": fe["name"]}] if fe else []
    system = HR_SYSTEM.format(today=date.today().isoformat())
    if focus:
        system += f"\nHR is currently viewing {focus[0]['n']}'s record; 'they' or 'this person' refers to them."
    messages = [{"role": "system", "content": system + "\n\nCONTEXT:\n" + ctx}, *_convo(history), {"role": "user", "content": message}]
    async for part in _stream(messages, "the onboarding admin", focus_id, "HR co-pilot", chunks):
        yield part
