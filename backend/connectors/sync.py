"""Integrations: move records from a source connector to a target connector."""
import json
import time

from graph import q, new_id, event
from .base import RecordRejected
from . import registry

# target field -> source field candidates (first that exists wins)
ALIASES = {
    "work_email": ["email", "work_email"],
    "job_title": ["designation", "job_title", "role"],
    "start_date": ["joining_date", "start_date"],
    "department_name": ["department_name", "department"],
    "employee_number": ["employee_number", "employee_id"],
    "manager_name": ["manager_name", "manager"],
}


def suggest_mapping(source_keys: list[str], target_keys: list[str]) -> dict[str, str]:
    """Pure: {target_field: source_field} using aliases, then exact key matches."""
    out = {}
    for t in target_keys:
        for s in ALIASES.get(t, []) + [t]:
            if s in source_keys:
                out[t] = s
                break
    return out


def apply_mapping(values: dict, mapping: dict[str, str]) -> dict:
    out = {}
    for t, s in mapping.items():
        v = values.get(s)
        if isinstance(v, list):
            v = ", ".join(map(str, v))
        if v not in (None, ""):
            out[t] = v
    return out


def run(source_id: str, target_id: str, mapping: dict[str, str], policy: str = "update", statuses: list[str] | None = None) -> dict:
    src, tgt = registry.get(source_id), registry.get(target_id)
    if not src or not tgt:
        raise LookupError("Connector not found")
    (s_inst, s_impl), (t_inst, t_impl) = src, tgt
    if s_inst["role"] != "source" or t_inst["role"] != "target":
        raise ValueError("Integrations move records from a source connector to a target connector")
    if policy not in ("update", "skip"):
        raise ValueError("Duplicate policy must be 'update' or 'skip'")
    records = s_impl.list_records(source_id, 1, 100000)["items"]
    if statuses:
        records = [r for r in records if r["values"].get("status") in statuses]
    counts = {"created": 0, "updated": 0, "skipped": 0, "rejected": 0}
    results = []
    for r in records:
        label = " ".join(filter(None, [r["values"].get("first_name"), r["values"].get("last_name")])) or r["id"]
        try:
            rid, outcome = t_impl.upsert_record(target_id, apply_mapping(r["values"], mapping), policy)
            counts[outcome] += 1
            results.append({"record": label, "outcome": outcome, "target_id": rid})
        except RecordRejected as ex:
            counts["rejected"] += 1
            results.append({"record": label, "outcome": "rejected", "reason": str(ex)})
    run_row = {"id": new_id("run"), "source_id": source_id, "target_id": target_id, "source_name": s_inst["name"],
               "target_name": t_inst["name"], "mapping": json.dumps(mapping), "policy": policy,
               "counts": json.dumps(counts), "results": json.dumps(results), "ts": time.time()}
    q("CREATE (r:IntegrationRun) SET r = $p", p=run_row)
    event(None, "connector", f"Sync {s_inst['name']} → {t_inst['name']}: " + ", ".join(f"{v} {k}" for k, v in counts.items()))
    return {**run_row, "mapping": mapping, "counts": counts, "results": results}


def runs(limit: int = 20) -> list[dict]:
    rows = q("MATCH (r:IntegrationRun) RETURN r ORDER BY r.ts DESC LIMIT $l", l=limit)
    return [{**r["r"], "mapping": json.loads(r["r"]["mapping"]), "counts": json.loads(r["r"]["counts"]),
             "results": json.loads(r["r"]["results"])} for r in rows]
