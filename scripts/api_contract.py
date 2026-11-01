"""API contract check for Phase 3: the storage moves must not change any response shape.

Builds a deterministic fixture in a throwaway database and graph (seed data plus one integration that has actually run,
LLM off, simulated delivery), calls about 20 read endpoints, and records the recursive key structure with value types.

Usage (from the repo root):
  backend/.venv/Scripts/python scripts/api_contract.py --capture   # write backend/tests_snapshots/api_contract.json
  backend/.venv/Scripts/python scripts/api_contract.py --check     # diff against it; exit 1 on any change
Values are never stored, only shapes: {key: type} for objects, [merged item shape] for lists.
"""
import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
SNAPSHOT = BACKEND / "tests_snapshots" / "api_contract.json"
sys.path.insert(0, str(BACKEND))
os.chdir(BACKEND)
os.environ["FALKORDB_GRAPH"] = "contract"
for _k in ("GMAIL_REFRESH_TOKEN", "GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "LLM_API_KEY", "OPENROUTER_API_KEY",
           "OPENAI_API_KEY", "GROQ_API_KEY", "GEMINI_API_KEY", "CEREBRAS_API_KEY", "EMAIL_REDIRECT_TO"):
    os.environ[_k] = ""  # present-but-empty: main's load_dotenv can't refill them
os.environ.setdefault("LLM_LOCAL_FALLBACK", "0")
os.environ.setdefault("LLM_KEYLESS_FALLBACK", "0")


# ---------- shapes ----------

def shape(v):
    if isinstance(v, dict):
        return {k: shape(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        merged = None
        for x in v:
            merged = merge(merged, shape(x))
        return [merged] if merged is not None else []
    if v is None:
        return "null"
    return {bool: "bool", int: "int", float: "float", str: "str"}.get(type(v), type(v).__name__)


def merge(a, b):
    if a is None:
        return b
    if isinstance(a, dict) and isinstance(b, dict):
        return {k: merge(a.get(k), b.get(k)) if k in a and k in b else (a.get(k) if k in a else b.get(k))
                for k in sorted(set(a) | set(b))}
    if isinstance(a, list) and isinstance(b, list):
        return [merge(a[0] if a else None, b[0] if b else None)] if (a or b) else []
    if a == b:
        return a
    return "|".join(sorted(set(str(a).split("|")) | set(str(b).split("|"))))


def diff(old, new, path="") -> list[str]:
    out = []
    if isinstance(old, dict) and isinstance(new, dict):
        for k in sorted(set(old) | set(new)):
            p = f"{path}.{k}" if path else k
            if k not in new:
                out.append(f"- missing   {p}  (was {json.dumps(old[k])[:80]})")
            elif k not in old:
                out.append(f"+ added     {p}  ({json.dumps(new[k])[:80]})")
            else:
                out += diff(old[k], new[k], p)
    elif isinstance(old, list) and isinstance(new, list):
        if old and new:
            out += diff(old[0], new[0], path + "[]")
        elif old and not new:
            out.append(f"- emptied   {path}[]  (the fixture no longer produces items here)")
        elif new and not old:
            out.append(f"+ items     {path}[]")
    elif old != new:
        # 'null|str' vs 'str' style widening is still a change a client might notice
        out.append(f"~ retyped   {path}: {json.dumps(old)} -> {json.dumps(new)}")
    return out


# ---------- fixture ----------

def build() -> dict:
    from shared import db
    db.use_test_database("azentio_contract")
    from graph import q, q1
    q("MATCH (n) DETACH DELETE n")
    import llm
    llm.API_KEY = ""
    llm.PROVIDERS = []
    import tempfile
    from documents import service as docsvc
    docsvc.UPLOADS = Path(tempfile.mkdtemp(prefix="azentio-contract-"))
    import ingest
    from identity import service as idsvc
    from shared import bridge
    for t in ("Employee", "Item", "Document"):
        bridge.SOURCE[t] = "postgres"  # the post-cutover state the running app is in
    asyncio.run(ingest.seed())
    idsvc.create_user("admin@contract.io", "Admin", "admin", "admin-pass-12345", actor="test")
    idsvc.create_user("hr@contract.io", "HR", "hr_ops", "hr-pass-12345", actor="test")

    import guardrails
    guardrails.record("employee", "prompt_injection", "blocked")

    # one integration, set up through the rule-based agent and run once (simulated delivery)
    from integrations import chat as wf_chat, engine as wf_engine, service as wf_service
    from integrations import store as wf_store
    cp_id = q1("MATCH (i:ConnectorInstance {key:'candidate_portal'}) RETURN i.id AS id")["id"]
    hr_id = q1("MATCH (i:ConnectorInstance {key:'hr_portal'}) RETURN i.id AS id")["id"]

    async def setup():
        it = wf_service.create("Contract integration", "", cp_id, hr_id)
        for msg in ["Hired candidates in Engineering, update existing employees", "approve", "Notify HR Operations about failures",
                    "No, skip verification", "No offer letter", "No app access", "Only when I run it"]:
            await wf_chat.run_chat_turn(it["id"], msg, "UTC")
        await wf_chat.run_chat_turn(it["id"], "activate", "UTC")
        run = await wf_engine.run_integration(it["id"])
        return wf_store.get(it["id"]), run
    it, run = asyncio.run(setup())

    # a second integration with document verification, offer letter and app access, driven to HR approval
    import checklist
    from documents.seed import accept_for_demo

    async def setup2():
        it2 = wf_service.create("Contract integration with documents", "", cp_id, hr_id)
        wf_service.save_records(it2["id"], ["offer_accepted"], ["Engineering"], "update")
        wf_service.save_mapping(it2["id"], await wf_service.suggest_mapping(it2["id"]))
        wf_service.approve_mapping(it2["id"])
        wf_service.save_notifications(it2["id"], [], [], False, False, False)
        wf_service.save_documents(it2["id"], True)
        wf_service.save_role_documents(it2["id"], "all roles", ["photo_id", "address_proof"])
        await wf_service.save_offer_choice(it2["id"], True)
        wf_service.decide_offer(it2["id"], True)
        wf_service.save_access_rules(it2["id"], wf_service.suggested_access(wf_store.get(it2["id"])))
        wf_service.save_schedule(it2["id"], "manual")
        wf_service.activate(it2["id"])
        await wf_engine.run_integration(it2["id"])
        asked = [i for i in wf_store.items(it2["id"]) if i["stage"] == "documents_requested"]
        for i in checklist.employee_items(asked[0]["joiner_id"]):
            if i["kind"] == "document":
                accept_for_demo(asked[0]["joiner_id"], i["key"])
        wf_engine.on_documents_changed(asked[0]["joiner_id"])
        return it2
    it2 = asyncio.run(setup2())

    # a CSV import into the candidate import connector
    from connectors import registry
    ci_id = q1("MATCH (i:ConnectorInstance {key:'candidate_import'}) RETURN i.id AS id")["id"]
    csv = "\n".join([
        "candidate_id,first_name,last_name,email,designation,department_name,grade,location,joining_date,status",
        "C900,Asha,Rao,asha.rao@example.com,Analyst,Finance,G2,Bengaluru,2026-12-01,hired",
        "C901,,Bad,bad@example.com,Analyst,Finance,G2,Bengaluru,2026-12-01,hired",  # missing first name: an import error
    ]).encode()
    registry.get(ci_id)[1].import_file(ci_id, "candidates.csv", csv)
    return {"cp_id": cp_id, "hr_id": hr_id, "ci_id": ci_id, "integration_id": it["id"], "integration2_id": it2["id"],
            "run_id": run["id"]}


ENDPOINTS = [  # (name, path template)
    ("employees", "/api/employees"), ("employee_detail", "/api/employees/E1006"),     ("events", "/api/events"), ("stats", "/api/stats"), ("templates", "/api/templates"), ("rules", "/api/rules"),
    ("policies", "/api/policies"), ("quality", "/api/quality"),
    ("connectors", "/api/connectors"), ("connector_detail", "/api/connectors/{cp_id}"),
    ("connector_schema", "/api/connectors/{hr_id}/schema"), ("connector_records", "/api/connectors/{cp_id}/records"),
    ("connector_connection", "/api/connectors/{hr_id}/connection"), ("connector_imports", "/api/connectors/{ci_id}/imports"),
    ("integrations", "/api/integrations"), ("integration_detail", "/api/integrations/{integration_id}"),
    ("integration_candidates", "/api/integrations/{integration_id}/candidates"),
    ("integration_messages", "/api/integrations/{integration2_id}/messages"),
    ("integration2_detail", "/api/integrations/{integration2_id}"),
    ("integration2_candidates", "/api/integrations/{integration2_id}/candidates"),
    ("approvals", "/api/approvals"), ("logs_integration", "/api/logs/integration"),
    ("logs_integration_records", "/api/logs/integration/{run_id}/records"),
]


def capture() -> dict:
    ids = build()
    from fastapi.testclient import TestClient
    import main
    client = TestClient(main.app)
    r = client.post("/api/auth/login", json={"email": "admin@contract.io", "password": "admin-pass-12345"})
    assert r.status_code == 200, r.text
    # onboarding a candidate creates the employee <-> connector record links
    cand = client.get(f"/api/connectors/{ids['cp_id']}/records").json()["items"][0]
    joiner = client.post(f"/api/connectors/{ids['cp_id']}/records/{cand['id']}/onboard", json={}).json()
    ids["onboarded"] = joiner.get("employee", joiner).get("id") or joiner["id"]
    out = {}
    for name, tmpl in ENDPOINTS + [("employee_links", "/api/employees/{onboarded}/links")]:
        res = client.get(tmpl.format(**ids))
        assert res.status_code == 200, f"{name}: {res.status_code} {res.text[:200]}"
        out[name] = shape(res.json())
    return out


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--capture", action="store_true")
    g.add_argument("--check", action="store_true")
    a = ap.parse_args()
    now = capture()
    if a.capture:
        SNAPSHOT.parent.mkdir(exist_ok=True)
        SNAPSHOT.write_text(json.dumps(now, indent=1, sort_keys=True) + "\n", encoding="utf-8")
        print(f"api contract: captured {len(now)} endpoints -> {SNAPSHOT.relative_to(ROOT)}")
        sys.exit(0)
    old = json.loads(SNAPSHOT.read_text(encoding="utf-8"))
    problems = []
    for name in sorted(set(old) | set(now)):
        if name not in now:
            problems.append(f"{name}: endpoint no longer checked")
            continue
        if name not in old:
            problems.append(f"{name}: new endpoint (re-capture to accept)")
            continue
        problems += [f"{name}: {d}" for d in diff(old[name], now[name])]
    for line in problems:
        print("API CONTRACT", line)
    print(f"api contract: {'ok' if not problems else f'{len(problems)} difference(s)'} across {len(now)} endpoints")
    sys.exit(1 if problems else 0)
