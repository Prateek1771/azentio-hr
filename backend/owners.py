"""Owners: the directory of people who own onboarding work (managers, and `owner:<category>` task owners).

The only way to read or write owners (Phase 3 batch A1). Postgres is the record; graph `Owner` nodes are a projection
refreshed by `records.project_owners()` after every write, so REPORTS_TO / OWNED_BY edges keep working.
"""
import graph_migration as gm
from shared import db

COLS = ("email", "name", "department", "type")


def all() -> list[dict]:  # noqa: A001 - module-level name, read as owners.all()
    return db.fetch("SELECT * FROM owners ORDER BY type, email")


def get(email: str) -> dict | None:
    return db.fetch1("SELECT * FROM owners WHERE email=lower(%s)", (email or "",))


def emails(type: str | None = None) -> set[str]:
    if type:
        return {r["email"] for r in db.fetch("SELECT email FROM owners WHERE type=%s", (type,))}
    return {r["email"] for r in db.fetch("SELECT email FROM owners")}


def by_category() -> dict:
    """{'IT': owner, ...} for the `owner:<category>` rows."""
    return {r["type"].split(":", 1)[1]: r for r in db.fetch("SELECT * FROM owners WHERE type LIKE 'owner:%%' ORDER BY email")}


def manager_for(department: str) -> str:
    row = db.fetch1("SELECT email FROM owners WHERE type='manager' AND department=%s ORDER BY email LIMIT 1",
                    (department or "",))
    return row["email"] if row else ""


def upsert(rows: list[dict]) -> int:
    with db.tx() as c:
        for r in rows:
            c.execute("""INSERT INTO owners (email, name, department, type) VALUES (lower(%s), %s, %s, %s)
                         ON CONFLICT (email) DO UPDATE SET name=EXCLUDED.name, department=EXCLUDED.department,
                         type=EXCLUDED.type""", (r["email"], r["name"], r.get("department") or "", r["type"]))
    import records
    records.project_owners()
    return len(rows)


def _canon(r: dict) -> dict:
    return {k: (r.get(k) or "") for k in COLS} | {"email": (r.get("email") or "").lower()}


gm.register(gm.LabelSpec(
    label="Owner", batch="A1", table="owners", key="email", projection=True,
    to_row=_canon, canonical=_canon, from_row=_canon,
    mapped_keys=frozenset(COLS),
    allowed_readers=("backend/records.py",)))
