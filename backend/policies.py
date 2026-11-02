"""Policies: the uploaded policy and process documents (Phase 3 batch A2). The only way to read or write them.

Postgres is the record. The graph keeps only `PolicyChunk`, the retrieval index that `chat.index_chunks` builds from
these rows (and `chat.reindex_policies` rebuilds in full).
"""
import graph_migration as gm
from shared import db

COLS = ("source", "title", "text")


def title_of(source: str, text: str) -> str:
    return next((line[2:].strip() for line in text.splitlines() if line.startswith("# ")), source)


def all() -> list[dict]:  # noqa: A001 - read as policies.all()
    return db.fetch("SELECT * FROM policies ORDER BY source")


def texts() -> dict[str, str]:
    return {r["source"]: r["text"] for r in db.fetch("SELECT source, text FROM policies")}


def upsert(source: str, text: str) -> str:
    title = title_of(source, text)
    db.execute("""INSERT INTO policies (source, title, text) VALUES (%s, %s, %s)
                  ON CONFLICT (source) DO UPDATE SET title=EXCLUDED.title, text=EXCLUDED.text""", (source, title, text))
    return title


def _canon(r: dict) -> dict:
    return {k: (r.get(k) or "") for k in COLS}


gm.register(gm.LabelSpec(
    label="Policy", batch="A2", table="policies", key="source",
    to_row=_canon, canonical=_canon, from_row=_canon,
    mapped_keys=frozenset(COLS)))
