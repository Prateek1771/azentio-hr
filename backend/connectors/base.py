"""Connector interface. A connector is a system candidate/employee records come from (source) or go to (target).

ponytail: the spec's `conn` and `customer_id` arguments are dropped — this app is a single workspace and talks to
FalkorDB through graph.q. Add them back when multi-tenancy lands.
"""
import json
import time
from dataclasses import asdict, dataclass

from graph import q, q1, new_id


@dataclass
class FieldSpec:
    key: str
    label: str
    type: str = "string"  # string | number | boolean | date | list
    required: bool = False
    description: str = ""
    is_custom: bool = False
    generated: bool = False

    def dict(self):
        return asdict(self)


class RecordRejected(Exception):
    """A target refused a record; the message is a reason HR can act on."""


class Connector:
    key: str = ""
    record_kind: str = ""  # candidate | employee
    FIELDS: list[FieldSpec] = []

    def describe_schema(self, instance_id: str) -> list[FieldSpec]:
        return list(self.FIELDS)

    def list_records(self, instance_id: str, page: int = 1, page_size: int = 25, q_: str = "") -> dict:
        rows = [self._row(r["r"]) for r in q("MATCH (r:Record {instance_id:$i}) RETURN r ORDER BY r.created DESC", i=instance_id)]
        if q_:
            needle = q_.lower()
            rows = [r for r in rows if needle in " ".join(str(v) for v in r["values"].values() if not isinstance(v, dict)).lower()]
        start = (max(page, 1) - 1) * page_size
        return {"items": rows[start:start + page_size], "total": len(rows), "page": page, "page_size": page_size}

    def get_record(self, instance_id: str, record_id: str) -> dict | None:
        row = q1("MATCH (r:Record {id:$id, instance_id:$i}) RETURN r", id=record_id, i=instance_id)
        return self._row(row["r"]) if row else None

    def count_records(self, instance_id: str) -> int:
        return q1("MATCH (r:Record {instance_id:$i}) RETURN count(r) AS n", i=instance_id)["n"]

    def upsert_record(self, instance_id: str, values: dict, duplicate_policy: str = "update") -> tuple[str, str]:
        raise RecordRejected(f"{self.key} is a read-only source")

    # ---- helpers shared by implementations ----
    @staticmethod
    def _row(r: dict) -> dict:
        return {"id": r["id"], "instance_id": r["instance_id"], "kind": r["kind"], "values": json.loads(r["values"]),
                "created": r.get("created"), "updated": r.get("updated")}

    def _write(self, instance_id: str, values: dict, record_id: str | None = None) -> str:
        now = time.time()
        if record_id:
            q("MATCH (r:Record {id:$id}) SET r.values=$v, r.updated=$t", id=record_id, v=json.dumps(values), t=now)
            return record_id
        rid = new_id("rec")
        q("""MATCH (i:ConnectorInstance {id:$i}) CREATE (r:Record {id:$id, instance_id:$i, kind:$k, values:$v, created:$t, updated:$t})
             CREATE (r)-[:IN]->(i)""", i=instance_id, id=rid, k=self.record_kind, v=json.dumps(values), t=now)
        return rid

    def _find(self, instance_id: str, field: str, value: str) -> dict | None:
        """ponytail: linear scan over JSON values; fine for demo-sized connectors, index a property if they grow."""
        for r in q("MATCH (r:Record {instance_id:$i}) RETURN r", i=instance_id):
            row = self._row(r["r"])
            if str(row["values"].get(field, "")).lower() == str(value).lower():
                return row
        return None
