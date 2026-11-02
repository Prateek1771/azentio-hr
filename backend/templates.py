"""Email templates: the only way to read or write them (Phase 3 batch A1). Postgres only; the graph no longer has them."""
import graph_migration as gm
from shared import db

COLS = ("id", "name", "subject", "body")


def all() -> list[dict]:  # noqa: A001 - read as templates.all()
    return db.fetch("SELECT * FROM email_templates ORDER BY id")


def get(tid: str) -> dict:
    row = db.fetch1("SELECT * FROM email_templates WHERE id=%s", (tid,))
    if row is None:
        raise KeyError(f"email template '{tid}' not found")
    return row


def upsert(t: dict, conn=None) -> None:
    db.execute("""INSERT INTO email_templates (id, name, subject, body) VALUES (%s, %s, %s, %s)
                  ON CONFLICT (id) DO UPDATE SET name=EXCLUDED.name, subject=EXCLUDED.subject, body=EXCLUDED.body""",
               (t["id"], t["name"], t["subject"], t["body"]), conn=conn)


def _canon(r: dict) -> dict:
    return {k: (r.get(k) or "") for k in COLS}


gm.register(gm.LabelSpec(
    label="EmailTemplate", batch="A1", table="email_templates",
    to_row=_canon, canonical=_canon, from_row=_canon,
    mapped_keys=frozenset(COLS)))
