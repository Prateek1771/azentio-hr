"""Connector interface. A connector is a system candidate/employee records come from (source) or go to (target).
Records live in Postgres (`connectors/store.py`, Phase 3 batch C).

ponytail: the spec's `customer_id` argument is dropped: this app is a single workspace. Add it back when
multi-tenancy lands.
"""
from dataclasses import asdict, dataclass

from . import store


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
        return store.records(instance_id, page, page_size, q_)

    def get_record(self, instance_id: str, record_id: str) -> dict | None:
        return store.record(instance_id, record_id)

    def count_records(self, instance_id: str) -> int:
        return store.count(instance_id)

    def upsert_record(self, instance_id: str, values: dict, duplicate_policy: str = "update") -> tuple[str, str]:
        raise RecordRejected(f"{self.key} is a read-only source")

    # ---- helpers shared by implementations ----
    def _write(self, instance_id: str, values: dict, record_id: str | None = None) -> str:
        return store.write(instance_id, self.record_kind, values, record_id)

    def _find(self, instance_id: str, field: str, value: str) -> dict | None:
        return store.find(instance_id, field, value)
