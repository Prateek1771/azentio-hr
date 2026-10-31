"""Applying an integration's field mapping to a source record."""
from connectors.base import RecordRejected

TRANSFORMS = ("none", "trim", "lowercase", "uppercase", "first_word", "after_first_word")


def transform(value, kind: str):
    if value is None:
        return None
    if isinstance(value, list):
        value = ", ".join(map(str, value))
    s = str(value)
    match kind:
        case "trim":
            return s.strip()
        case "lowercase":
            return s.lower()
        case "uppercase":
            return s.upper()
        case "first_word":
            return s.strip().split(" ")[0] if s.strip() else ""
        case "after_first_word":
            parts = s.strip().split(" ", 1)
            return parts[1] if len(parts) > 1 else ""
    return value


def apply_mapping(rules: list[dict], source: dict) -> dict:
    """target_field -> value, using the source field (transformed) or the default value."""
    out = {}
    for r in rules:
        raw = source.get(r["source_field"]) if r.get("source_field") else None
        value = transform(raw, r.get("transform") or "none") if raw not in (None, "", []) else None
        if value in (None, "") and r.get("default_value"):
            value = r["default_value"]
        if value not in (None, ""):
            out[r["target_field"]] = value
    return out


def check_required(rules: list[dict], target_schema, values: dict) -> None:
    missing = [f.label for f in target_schema if f.required and not f.generated and not values.get(f.key)]
    if missing:
        raise RecordRejected(f"No value for required field(s): {', '.join(missing)}. Map a source field or set a default.")
