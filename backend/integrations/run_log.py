"""What goes into the integration log, and how HR's exclusions apply to it.

Exclusions apply twice: excluded values are never written, and reading applies the current exclusions
too, so excluding a field also hides what older runs recorded. The audit log is never affected.
"""
LOG_SECTIONS = ("mapping", "documents", "approvals", "offers")
_NAME_FIELDS = {"first_name", "last_name"}
_MAX_VALUE = 500


def exclusions_of(integration: dict) -> tuple[set[str], set[str]]:
    ex = integration.get("log_exclusions") or {}
    return set(ex.get("fields") or []), set(ex.get("sections") or [])


def snapshot_mapping(rules: list[dict], source: dict, excluded_fields: set[str], excluded_sections: set[str]) -> list[dict]:
    """How each attribute was filled for one candidate, minus anything HR excluded."""
    if "mapping" in excluded_sections:
        return []
    out = []
    for rule in sorted(rules, key=lambda r: r["target_field"]):
        src = rule.get("source_field")
        raw = source.get(src) if src else None
        from_default = raw in (None, "", [])
        value = rule.get("default_value") if from_default else raw
        if isinstance(value, list):
            value = ", ".join(map(str, value))
        hidden = rule["target_field"] in excluded_fields or (src in excluded_fields if src else False)
        out.append({"source_field": src, "target_field": rule["target_field"], "transform": rule.get("transform", "none"),
                    "from_default": from_default, "excluded": hidden,
                    "value": None if hidden or value in (None, "") else str(value)[:_MAX_VALUE]})
    return out


def display_name(name: str | None, reference: str | None, excluded_fields: set[str]) -> str:
    if name and name.strip() and not (excluded_fields & _NAME_FIELDS):
        return name
    return reference or "Candidate"


def visible_actions(actions: list[dict], sections: set[str]) -> list[dict]:
    return [a for a in actions if a.get("section") not in sections]


def mask_record(record: dict, fields: set[str], sections: set[str]) -> dict:
    """Apply the integration's current exclusions to a stored log entry."""
    mapping = []
    if "mapping" not in sections:
        for entry in record.get("mapping") or []:
            hidden = entry.get("excluded") or entry["target_field"] in fields or entry.get("source_field") in fields
            mapping.append({**entry, "excluded": bool(hidden), "value": None if hidden else entry.get("value")})
    return {**record, "candidate_name": display_name(record.get("candidate_name"), record.get("candidate_ref"), fields),
            "actions": visible_actions(record.get("actions") or [], sections), "mapping": mapping,
            "mapping_hidden": "mapping" in sections}
