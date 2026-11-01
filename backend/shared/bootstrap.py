"""Self-bootstrap on startup. Every step is idempotent: run it once or ten times, same result.

No manual setup: no hand-run SQL, no wizard. Missing required config stops startup with a clear message.
"""
import os

import logging_setup as ls
from shared import db

_log = ls.get("bootstrap")

DEV_USERS = [  # (email, name, role, dev password)
    ("hrops@azentio.local", "Harper Ops", "hr_ops", "hrops-dev-pass"),
    ("approver@azentio.local", "Avery Approver", "approver", "approver-dev-pass"),
    ("approver2@azentio.local", "Blake Compliance", "approver", "approver2-dev-pass"),  # second sign-off for compliance rules
    ("manager@azentio.local", "Morgan Manager", "manager", "manager-dev-pass"),
    ("auditor@azentio.local", "Ari Auditor", "auditor", "auditor-dev-pass"),
]
DEV_ADMIN_PASSWORD = "admin-dev-pass"
DEV_MANAGED = ("E1006", "E1002")  # seeded joiners reporting to the dev manager, so that view isn't empty


def is_production() -> bool:
    return os.getenv("ENV", "development").lower() == "production"


def check_config() -> None:
    required = ["DATABASE_URL", "ADMIN_EMAIL", "ADMIN_PASSWORD"] if is_production() else []
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        raise SystemExit(f"bootstrap: missing required config: {', '.join(missing)} (ENV=production)")


def connect(wait: float = 20.0) -> None:
    try:
        db.init(wait=wait)
    except Exception as e:
        raise SystemExit(f"bootstrap: cannot reach Postgres at {db.url().split('@')[-1]} after {wait:.0f}s "
                         f"({type(e).__name__}). Start it with `docker compose up -d postgres`.") from None


def ensure_users() -> list[str]:
    from identity.service import ensure_user
    created = []
    admin_email = os.getenv("ADMIN_EMAIL") or "admin@azentio.local"
    if ensure_user(admin_email, "Administrator", "admin", os.getenv("ADMIN_PASSWORD") or DEV_ADMIN_PASSWORD):
        created.append(admin_email)
    if not is_production():
        for email, name, role, pw in DEV_USERS:
            if ensure_user(email, name, role, pw):
                created.append(email)
    return created


def ensure_dev_manager_reports() -> None:
    if is_production():
        return
    import records
    for eid in DEV_MANAGED:
        e = records.employee(eid)
        if e and e.get("manager_email") != "manager@azentio.local":
            records.update_employee(eid, manager_email="manager@azentio.local")


def migrate_records() -> dict:
    """Employee/Item graph -> Postgres, once: backfill, then switch reads only if parity passes (else refuse to start)."""
    from shared import bridge
    import records
    bridge.load()
    todo = [t for t in ("Employee", "Item") if bridge.source(t) != "postgres"]
    if not todo:
        return {"records": "postgres"}
    if not graph_ok():
        raise SystemExit("bootstrap: the graph is unreachable, so employees/items can't be migrated to Postgres yet. "
                         "Start FalkorDB (`docker compose up -d falkordb`) and restart.")
    report = records.backfill()
    for t in todo:
        g, p = records.parity_rows(t)
        try:
            report[t] = bridge.switch(t, g, p)
        except bridge.ParityError as e:
            raise SystemExit(f"bootstrap: {e}. Reads stay off Postgres; fix the data and restart.") from None
    records.rebuild_graph()  # normalise the projection once (edges written by older code paths)
    ls.log_event(_log, "records.migrated", employees=report["employees"], items=report["items"],
                 skipped=len(report["skipped_items"]), extra_keys=",".join(report["extra_keys"]))
    return report


def graph_ok() -> bool:
    try:
        from graph import q
        q("RETURN 1 AS ok")
        return True
    except Exception:
        return False


def run() -> dict:
    """Synchronous part of startup. The job worker is started by the app lifespan afterwards."""
    check_config()
    connect()
    applied = db.migrate()
    from audit_log.service import import_graph_rows
    imported = import_graph_rows() if graph_ok() else 0  # before anything else writes audit rows: history stays first in the chain
    created = ensure_users()
    migrate_records()
    state = {"migrations": applied, "users_created": created, "audit_imported": imported, "graph": graph_ok()}
    ls.log_event(_log, "done", **{k: (len(v) if isinstance(v, list) else v) for k, v in state.items()})
    if created and not is_production():
        print("\n  Dev sign-in accounts (ENV != production):")
        print(f"    {(os.getenv('ADMIN_EMAIL') or 'admin@azentio.local'):<28} admin      "
              f"{'(ADMIN_PASSWORD)' if os.getenv('ADMIN_PASSWORD') else DEV_ADMIN_PASSWORD}")
        for email, _, role, pw in DEV_USERS:
            print(f"    {email:<28} {role:<10} {pw}")
        print()
    return state
