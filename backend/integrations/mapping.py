"""Applying an integration's field mapping to a source record."""
from typing import Callable

from connectors.base import RecordRejected
from .expr import ExpressionError, evaluate, unique_email

TRANSFORMS = ("none", "trim", "lowercase", "uppercase", "first_word", "after_first_word", "expression", "unique_email")


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


def apply_mapping(rules: list[dict], source: dict, email_taken: Callable[[str], bool] | None = None,
                  generated: dict | None = None) -> dict:
    """target_field -> value. Plain rules use the source field (transformed) or the default value.

    `expression` rules evaluate HR's expression over the source record. `unique_email` rules build an address
    from the expression and the domain; `generated` holds addresses already given to this candidate so a
    repeat run reuses them instead of minting john.doe2.
    """
    out = {}
    for r in rules:
        kind = r.get("transform") or "none"
        try:
            if kind == "expression":
                value = evaluate(r.get("expression") or "", source)
                value = "" if value is None else value
            elif kind == "unique_email":
                value = (generated or {}).get(r["target_field"]) or unique_email(
                    r.get("expression") or "", r.get("email_domain") or "", source, email_taken or (lambda _e: False))
            else:
                raw = source.get(r["source_field"]) if r.get("source_field") else None
                value = transform(raw, kind) if raw not in (None, "", []) else None
        except ExpressionError as e:
            raise RecordRejected(f"{r['target_field']}: {e}")
        if value in (None, "") and r.get("default_value"):
            value = r["default_value"]
        if value not in (None, ""):
            out[r["target_field"]] = value
    return out


def check_required(rules: list[dict], target_schema, values: dict) -> None:
    missing = [f.label for f in target_schema if f.required and not f.generated and not values.get(f.key)]
    if missing:
        raise RecordRejected(f"No value for required field(s): {', '.join(missing)}. Map a source field or set a default.")
