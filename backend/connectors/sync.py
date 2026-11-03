"""Field-mapping helpers for moving records between connectors. (The old `run`/`runs` sync, superseded by the
integrations engine, was removed in Phase 3 batch C; its IntegrationRun nodes are archived in `legacy_sync_runs`.)"""

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
