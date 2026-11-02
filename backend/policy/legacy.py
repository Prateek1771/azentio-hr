"""Phase 3 batch A3: the graph's legacy `Rule` and `Config` nodes, archived for rollback only.

Rule-set versions own the rules and the engine config (ADR 0009); the requirements import writes versions directly.
The cutover copies the old nodes into `legacy_rules` / `legacy_config`, and `graph_migration rollback A3` writes them
back for the previous release. Nothing reads these tables at runtime.
"""
import json

import graph_migration as gm

RULE_KEYS = ("id", "item", "name", "kind", "category", "match", "offset_days", "depends_on", "description",
             "policy_ref", "auto", "ord")


def _rule_props(p: dict) -> dict:
    """Graph props as they were (match stays the graph's JSON string), with absent keys made explicit."""
    return {k: p.get(k) for k in RULE_KEYS}


gm.register(gm.LabelSpec(
    label="Rule", batch="A3", table="legacy_rules",
    to_row=lambda p: {"id": p["id"], "props": _rule_props(p)},
    canonical=lambda r: {"id": r["id"], "props": json.loads(json.dumps(r["props"], sort_keys=True))},
    from_row=lambda r: {k: v for k, v in r["props"].items() if v is not None},
    mapped_keys=frozenset(RULE_KEYS)))

gm.register(gm.LabelSpec(
    label="Config", batch="A3", table="legacy_config", key="key",
    to_row=lambda p: {"key": p["key"], "value": p.get("value") or ""},
    canonical=lambda r: {"key": r["key"], "value": r["value"] or ""},
    from_row=lambda r: {"key": r["key"], "value": r["value"]},
    mapped_keys=frozenset({"key", "value"})))
