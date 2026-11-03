"""Architecture boundary guard. Fails (exit 1) when code crosses a boundary the hardening design relies on.

Rules (see AGENTS.md "Rules for changes"):
  1. No direct graph writes of Employee/Item *properties* outside backend/records.py.
     (Relationship writes to graph-owned nodes, i.e. documents and connector records, stay allowed until Phases 2-3.)
  2. No new user-role logic outside identity/perms.py. Known data-scoping checks are allowlisted by exact count.
  3. No edits to an already-applied migration: files in backend/migrations may only be added.
  4. Every work_queue.open_item(kind=...) uses a literal kind registered in work_queue.KINDS.
  5. No direct UPDATE/DELETE/TRUNCATE of audit_log and no disabling its trigger (tests excepted).
  6. No direct graph writes of Document nodes or their HAS_DOC / SUBMITTED / MISMATCH edges outside
     backend/documents/store.py (Postgres owns documents since Phase 2; the graph is a projection).
  7. No bulk status write for document items (records.set_items_status with a "document" key) outside
     backend/documents/. A document item's status is derived from a document decision; the database trigger
     items_status_from_document (migration 010) enforces it for every other write path.
  8. The graph is a read model (Phase 3 E): no Cypher in backend/ or scripts/ (tests included) writes a label outside
     Employee, Item, Document, Owner, PolicyChunk. Owner nodes are written only in backend/records.py and PolicyChunk
     only in backend/chat.py. graph.q() enforces the same at runtime (ReadModelViolation).

Usage:  python scripts/check_boundaries.py [--base <git-ref>]     # default base: merge-base with origin/main
        python scripts/check_boundaries.py --selftest             # proves each rule rejects a known-bad snippet
Stdlib only.
"""
import argparse
import ast
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
ROLES = "admin|hr_ops|approver|manager|auditor"

# Data-scoping role checks that are part of the design, by exact count per file. A new one anywhere fails.
ROLE_ALLOWLIST = {
    "backend/main.py": 1,          # _visible: managers see only their own reports
    "backend/work_queue.py": 2,    # admins/auditors see every item; only the assignee role (or admin) resolves
    "backend/policy/store.py": 1,  # only the author or an admin may discard a draft
    "backend/documents/service.py": 1,  # can_decide: hr_ops may decide only low-tier documents (the tier is data)
}


# ---------- helpers ----------

def py_files():
    for p in sorted(BACKEND.rglob("*.py")):
        rel = p.relative_to(ROOT).as_posix()
        if ".venv" in rel or "__pycache__" in rel:
            continue
        yield p, rel


def is_test(rel: str) -> bool:
    return Path(rel).name.startswith("test_")


def strings(tree: ast.AST):
    """Every string literal (f-strings flattened) with its line number."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            yield node.lineno, node.value
        elif isinstance(node, ast.JoinedStr):
            yield node.lineno, "".join(v.value for v in node.values if isinstance(v, ast.Constant) and isinstance(v.value, str))


# ---------- rule 1: Employee/Item property writes ----------

def node_violations(text: str, labels: str = "Employee|Item") -> list[str]:
    """Writes to nodes with one of `labels`: create/merge, property SET, or DELETE of a bound variable."""
    out, what = [], labels.replace("|", "/")
    if re.search(rf"\b(CREATE|MERGE)\s*\(\s*\w*\s*:\s*({labels})\b", text):
        out.append(f"creates a {what} node")
    for var in {m.group(1) for m in re.finditer(rf"\(\s*(\w+)\s*:\s*({labels})\b", text)}:
        if re.search(rf"\bSET\s+[^;]*?\b{var}\s*(\.\w+\s*=|\+=|=)", text):
            out.append(f"sets properties on {what} variable '{var}'")
        if re.search(rf"\bDELETE\b[^;]*?\b{var}\b(?!\s*\.)", text):
            out.append(f"deletes {what} node '{var}'")
    return out


def edge_violations(text: str, rels: str) -> list[str]:
    """Creating, merging or deleting relationships of the given types."""
    out, what = [], rels.replace("|", "/")
    if re.search(rf"\b(CREATE|MERGE)\b[^;]*?\[\s*\w*\s*:\s*({rels})\b", text):
        out.append(f"creates a {what} edge")
    for var in {m.group(1) for m in re.finditer(rf"\[\s*(\w+)\s*:\s*({rels})\b", text)}:
        if re.search(rf"\bDELETE\b[^;]*?\b{var}\b", text):
            out.append(f"deletes {what} edge '{var}'")
    return out


def cypher_violations(text: str) -> list[str]:
    return node_violations(text)


DOC_RELS = "HAS_DOC|SUBMITTED|MISMATCH"


def document_violations(text: str) -> list[str]:
    return node_violations(text, "Document") + edge_violations(text, DOC_RELS)


def check_graph_writes(files) -> list[str]:
    errs = []
    for path, rel in files:
        if rel == "backend/records.py" or is_test(rel):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, s in strings(tree):
            if not re.search(r"\b(MATCH|MERGE|CREATE)\b", s):
                continue
            for v in cypher_violations(s):
                errs.append(f"{rel}:{line}: {v}. No direct graph writes of Employee/Item properties outside "
                            f"backend/records.py; use records.update_employee/update_item (relationship writes to "
                            f"graph-owned nodes are fine)")
    return errs


def check_document_writes(files) -> list[str]:
    errs = []
    for path, rel in files:
        if rel == "backend/documents/store.py" or is_test(rel):
            continue
        for line, s in strings(ast.parse(path.read_text(encoding="utf-8"))):
            if not re.search(r"\b(MATCH|MERGE|CREATE)\b", s):
                continue
            for v in document_violations(s):
                errs.append(f"{rel}:{line}: {v}. No direct graph writes of Documents or their edges outside "
                            f"backend/documents/store.py; write through documents.store (the graph is a projection)")
    return errs


# ---------- rule 8: the graph is a read model ----------

READ_MODEL = {"Employee", "Item", "Document", "Owner", "PolicyChunk"}
WRITE_KW = re.compile(r"\b(CREATE|MERGE|SET|DELETE|REMOVE)\b")
NODE_LABELS = re.compile(r"\(\s*\w*\s*((?::\s*\w+)+)")
TEST_LABEL = re.compile(r"\b(?:WHERE|AND|OR|XOR|NOT)\s+\(?\s*\w+:(\w+)")
SET_LABEL = re.compile(r"\b(?:SET|REMOVE)\s+\w+((?::\s*\w+)+)")
INDEX_DDL = re.compile(r"^\s*CREATE\s+(VECTOR\s+)?INDEX\b")
# The Phase 3 inventory of retired record labels. Tests may write synthetic labels (engine probes), never these.
RETIRED = {"EmailTemplate", "Config", "Policy", "Rule", "Event", "GuardrailEvent", "EvalRun",
           "EvalResult", "Connector", "ConnectorInstance", "Record", "CustomField", "ImportRun", "IntegrationRun",
           "Integration", "IntegrationCandidate", "WorkflowRun", "IntegrationLogRecord", "OutboundMessage",
           "AgentMessage", "AuditLog"}
# label writer -> the one file allowed to write it (Employee/Item and Document are rules 1 and 6)
LABEL_OWNERS = {"Owner": "backend/records.py", "PolicyChunk": "backend/chat.py"}


def read_model_violations(text: str, only: set[str] | None = None) -> list[str]:
    """A Cypher write naming any label outside the read model (or, with `only`, one of those labels)."""
    if not WRITE_KW.search(text) or not re.search(r"\b(MATCH|MERGE|CREATE)\b", text) or INDEX_DDL.match(text):
        return []
    text = text.replace("`", "")
    labels = {lbl for rx in (NODE_LABELS, SET_LABEL) for m in rx.finditer(text) for lbl in re.findall(r"\w+", m.group(1))}
    bad = (labels | set(TEST_LABEL.findall(text))) - READ_MODEL
    if only is not None:
        bad &= only
    return [f"writes {', '.join(sorted(bad))}, outside the graph read model"] if bad else []


def check_read_model(files) -> list[str]:
    errs = []
    for path, rel in files:
        if rel == "scripts/check_boundaries.py":  # its selftest samples
            continue
        for line, s in strings(ast.parse(path.read_text(encoding="utf-8"))):
            for v in read_model_violations(s, RETIRED if is_test(rel) else None):
                errs.append(f"{rel}:{line}: {v}. The record lives in Postgres; the graph holds only the projection "
                            f"and the policy index (graph.q refuses this at runtime too)")
            if not re.search(r"\b(MATCH|MERGE|CREATE)\b", s):
                continue
            for label, owner in LABEL_OWNERS.items():
                if rel != owner and not is_test(rel):
                    for v in node_violations(s, label):
                        errs.append(f"{rel}:{line}: {v}. Only {owner} writes {label} nodes")
    return errs


# ---------- rule 2: user-role logic ----------

ROLE_CHECK = re.compile(
    rf"""(\[["']role["']\]|\brole\b)\s*(==|!=|\bin\b|\bnot in\b)\s*[\(\[{{]?\s*["']({ROLES})["']"""
    rf"""|["']({ROLES})["']\s*(==|!=)\s*\S*role""")


def check_roles(files) -> list[str]:
    errs = []
    for path, rel in files:
        if rel.startswith("backend/identity/") or is_test(rel):
            continue
        hits = [i for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
                if ROLE_CHECK.search(line) and not line.lstrip().startswith("#")]
        allowed = ROLE_ALLOWLIST.get(rel, 0)
        if len(hits) > allowed:
            errs.append(f"{rel}:{hits[allowed] if allowed < len(hits) else hits[-1]}: user-role check outside "
                        f"identity/perms.py ({len(hits)} found, {allowed} allowlisted). Add a rule to identity/perms.py "
                        f"instead, or document a data-scoping exception in scripts/check_boundaries.py ROLE_ALLOWLIST")
    return errs


# ---------- rule 3: applied migrations are immutable ----------

def default_base() -> str | None:
    try:
        return subprocess.run(["git", "merge-base", "HEAD", "origin/main"], cwd=ROOT, capture_output=True,
                              text=True, check=True).stdout.strip() or None
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def check_migrations(base: str | None) -> list[str]:
    if not base:
        return []
    out = subprocess.run(["git", "diff", "--name-status", base, "--", "backend/migrations"], cwd=ROOT,
                         capture_output=True, text=True)
    errs = []
    for line in out.stdout.splitlines():
        status, *paths = line.split("\t")
        if status[0] in "MDR":
            errs.append(f"{paths[0]}: an already-applied migration was {'modified' if status[0] == 'M' else 'removed or renamed'}. "
                        f"Migrations are append-only: add the next numbered file instead")
    return errs


# ---------- rule 4: work_queue kinds ----------

def registered_kinds() -> set[str]:
    tree = ast.parse((BACKEND / "work_queue.py").read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        target = node.target if isinstance(node, ast.AnnAssign) else (node.targets[0] if isinstance(node, ast.Assign) else None)
        if isinstance(target, ast.Name) and target.id == "KINDS" and isinstance(node.value, ast.Dict):
            return {k.value for k in node.value.keys if isinstance(k, ast.Constant)}
    return set()


def check_kinds(files, kinds: set[str]) -> list[str]:
    errs = []
    for path, rel in files:
        if is_test(rel) or rel == "backend/work_queue.py":
            continue
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == "open_item":
                arg = node.args[0] if node.args else next((k.value for k in node.keywords if k.arg == "kind"), None)
                if not (isinstance(arg, ast.Constant) and isinstance(arg.value, str)):
                    errs.append(f"{rel}:{node.lineno}: work_queue.open_item kind must be a string literal")
                elif arg.value not in kinds:
                    errs.append(f"{rel}:{node.lineno}: work_queue kind '{arg.value}' isn't registered in work_queue.KINDS "
                                f"(each kind needs an SLA, an escalation role and resolution steps)")
    return errs


# ---------- rule 7: document item status only from decisions ----------

def bulk_doc_status_calls(tree: ast.AST) -> list[int]:
    """Lines calling set_items_status(...) with a dict literal that has a "document" key."""
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "attr", getattr(node.func, "id", None)) == "set_items_status":
            for arg in [*node.args, *(k.value for k in node.keywords)]:
                if isinstance(arg, ast.Dict) and any(isinstance(k, ast.Constant) and k.value == "document" for k in arg.keys):
                    out.append(node.lineno)
    return out


def check_doc_item_status(files) -> list[str]:
    errs = []
    for path, rel in files:
        if rel.startswith("backend/documents/") or is_test(rel):
            continue
        for line in bulk_doc_status_calls(ast.parse(path.read_text(encoding="utf-8"))):
            errs.append(f"{rel}:{line}: sets document item status directly. A document item's status comes only from a "
                        f"document decision (documents.service; documents.seed.accept_for_demo for demo data)")
    return errs


# ---------- rule 5: audit_log is append-only ----------

AUDIT_MUTATION = re.compile(r"\bUPDATE\s+audit_log\b|\bDELETE\s+FROM\s+audit_log\b|\bTRUNCATE\s+(TABLE\s+)?audit_log\b"
                            r"|\bDISABLE\s+TRIGGER\s+audit_log", re.I)


def check_audit(paths) -> list[str]:
    errs = []
    for path, rel in paths:
        if is_test(rel):
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if AUDIT_MUTATION.search(line):
                errs.append(f"{rel}:{i}: mutates audit_log. The audit log is append-only; write with audit.record_audit")
    return errs


# ---------- run ----------

def run(base: str | None) -> list[str]:
    files = list(py_files())
    sql = [(p, p.relative_to(ROOT).as_posix()) for p in sorted((BACKEND / "migrations").glob("*.sql"))]
    scripts = [(p, p.relative_to(ROOT).as_posix()) for p in sorted((ROOT / "scripts").glob("*.py"))]
    return (check_graph_writes(files) + check_document_writes(files) + check_doc_item_status(files) + check_roles(files) + check_migrations(base)
            + check_kinds(files, registered_kinds()) + check_audit(files + sql) + check_read_model(files + scripts))


def selftest() -> None:
    bad_cypher = [
        "MATCH (e:Employee {id:$id}) SET e.thread_id=$t",
        "MATCH (e:Employee {id:$id}) SET e += $p",
        "MATCH (i:Item {id:$id}) SET i.status='Done'",
        "CREATE (e:Employee) SET e = $p",
        "MERGE (i:Item {id:$id})",
        "MATCH (i:Item {id:$id}) DETACH DELETE i",
    ]
    ok_cypher = [
        "MATCH (e:Employee {id:$e}), (d:Document {id:$id}) CREATE (e)-[:SUBMITTED]->(d)",
        "MATCH (e:Employee {id:$e})-[s:SYNCED_TO]->() DELETE s",
        "MATCH (i:Item {id:$id})-[:HAS_DOC]->(d:Document) WITH d ORDER BY d.ts DESC LIMIT 1 SET d.status=$s",
        "MATCH (e:Employee {id:$e}), (c:Contact {id:$r}) MERGE (e)-[:KNOWS]->(c)",
    ]
    for c in bad_cypher:
        assert cypher_violations(c), f"rule 1 missed: {c}"
    for c in ok_cypher:
        assert not cypher_violations(c), f"rule 1 false positive: {c} -> {cypher_violations(c)}"
    bad_docs = [
        "CREATE (d:Document) SET d = $p",
        "MATCH (d:Document {id:$id}) SET d.status='Accepted'",
        "MATCH (i:Item {id:$i}), (d:Document {id:$d}) CREATE (i)-[:HAS_DOC]->(d)",
        "MATCH (e:Employee {id:$e}), (d:Document {id:$id}) CREATE (e)-[:SUBMITTED]->(d)",
        "MATCH (d:Document {id:$id}), (e:Employee {id:$e}) CREATE (d)-[m:MISMATCH]->(e) SET m = $p",
        "MATCH ()-[h:HAS_DOC]->(d:Document {id:$id}) DELETE h",
    ]
    ok_docs = [
        "MATCH (i:Item)-[:HAS_DOC]->(d:Document) WHERE i.id IN $ids RETURN i.id AS item, d",
        "MATCH (e:Employee {id:$e})<-[m:MISMATCH]-(d:Document) RETURN m.field AS f",
    ]
    for c in bad_docs:
        assert document_violations(c), f"rule 6 missed: {c}"
    for c in ok_docs:
        assert not document_violations(c), f"rule 6 false positive: {c} -> {document_violations(c)}"
    for line in ['if user["role"] == "admin":', "if role in ('admin', 'auditor'):", 'ok = u["role"] != "manager"']:
        assert ROLE_CHECK.search(line), f"rule 2 missed: {line}"
    for line in ['if c["role"] == role:', 'job_title = e["role"]', 'if s_inst["role"] != "source":']:
        assert not ROLE_CHECK.search(line), f"rule 2 false positive: {line}"
    bad8 = ["CREATE (r:Record {id:$id})", "MATCH (i:ConnectorInstance {id:$id}) SET i.secret=$s",
            "MATCH (n) WHERE n:Event DETACH DELETE n", "MERGE (n:AuditLog {id:$k}) SET n = $p",
            "MATCH (e:Employee {id:$e}), (r:Record {id:$r}) CREATE (e)-[:SYNCED_TO]->(r)", "MATCH (i:Integration) REMOVE i.x",
            "MATCH (n {id:$id}) SET n:Event", "CREATE (r:`Record` {id:1})", "MATCH (n) WHERE n:Owner XOR n:Rule DELETE n"]
    ok8 = ["MATCH (r:Record) RETURN r", "CREATE INDEX FOR (n:Rule) ON (n.id)", "MATCH (n) DETACH DELETE n",
           "MATCH (e:Employee {id:$id}), (m) WHERE (m:Owner OR m:Employee) AND m <> e CREATE (e)-[:REPORTS_TO]->(m)",
           "UNWIND $rows AS r MERGE (o:Owner {email:r.email}) SET o = r", "MATCH (c:PolicyChunk {source:$s}) DELETE c"]
    for c in bad8:
        assert read_model_violations(c), f"rule 8 missed: {c}"
    for c in ok8:
        assert not read_model_violations(c), f"rule 8 false positive: {c} -> {read_model_violations(c)}"
    assert read_model_violations("CREATE (n:Event {id:1})", RETIRED), "rule 8 missed a retired label in a test"
    assert not read_model_violations("CREATE (n:SyntheticProbe {id:1})", RETIRED), "rule 8 flagged a synthetic test label"
    assert node_violations("MERGE (o:Owner {email:$e}) SET o.name=$n", "Owner"), "rule 8 missed an Owner write"
    assert node_violations("CREATE (c:PolicyChunk {id:$id})", "PolicyChunk"), "rule 8 missed a PolicyChunk write"
    bad7 = ast.parse('records.set_items_status(e, keys, {"document": "Accepted", "task": "Completed"})')
    ok7 = ast.parse('records.set_items_status(e, keys, {"ack": "Acknowledged", "task": "Completed"})')
    assert bulk_doc_status_calls(bad7), "rule 7 missed a bulk document status write"
    assert not bulk_doc_status_calls(ok7), "rule 7 false positive"
    for line in ["UPDATE audit_log SET summary='x'", "DELETE FROM audit_log", "TRUNCATE audit_log",
                 "ALTER TABLE audit_log DISABLE TRIGGER audit_log_no_update"]:
        assert AUDIT_MUTATION.search(line), f"rule 5 missed: {line}"
    assert not AUDIT_MUTATION.search("CREATE TRIGGER audit_log_no_update BEFORE UPDATE OR DELETE ON audit_log")
    tree = ast.parse("import work_queue\nwork_queue.open_item('nope', 'x', 'y', 'r', 'admin')\nwork_queue.open_item(k, 'x', 'y', 'r', 'admin')")
    tmp = ROOT / "backend" / "_selftest_tmp.py"
    try:
        tmp.write_text(ast.unparse(tree), encoding="utf-8")
        errs = check_kinds([(tmp, "backend/_selftest_tmp.py")], {"ops"})
        assert len(errs) == 2, f"rule 4 should flag an unknown kind and a non-literal kind: {errs}"
    finally:
        tmp.unlink(missing_ok=True)
    assert registered_kinds() >= {"ops", "policy.approval", "policy.near_start"}, "could not read work_queue.KINDS"
    print("boundary guard selftest: ok (rules 1, 2, 4, 5, 6, 7, 8 reject known-bad code; rule 3 is a git diff)")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", help="git ref to compare migrations against (default: merge-base with origin/main)")
    ap.add_argument("--selftest", action="store_true")
    a = ap.parse_args()
    if a.selftest:
        selftest()
        sys.exit(0)
    problems = run(a.base or default_base())
    for p in problems:
        print(f"BOUNDARY: {p}")
    print(f"boundary guard: {'ok' if not problems else f'{len(problems)} problem(s)'}")
    sys.exit(1 if problems else 0)
