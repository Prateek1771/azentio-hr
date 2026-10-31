"""Connector key → implementation. Every key in the catalog needs an entry here."""
from graph import q1
from .base import Connector
from .candidates import CandidateImport, CandidatePortal
from .hr_portal import HrPortal

_REGISTRY: dict[str, type[Connector]] = {
    CandidatePortal.key: CandidatePortal,
    CandidateImport.key: CandidateImport,
    HrPortal.key: HrPortal,
}


def instance(instance_id: str) -> dict | None:
    row = q1("""MATCH (i:ConnectorInstance {id:$id})-[:OF]->(c:Connector)
                RETURN i.id AS id, i.name AS name, c.key AS key, c.role AS role, c.record_kind AS record_kind,
                       c.demo AS demo, c.description AS description, c.name AS type_name""", id=instance_id)
    return row


def get(instance_id: str) -> tuple[dict, Connector] | None:
    """(instance metadata, implementation) or None — callers turn None into a 404."""
    inst = instance(instance_id)
    if not inst or inst["key"] not in _REGISTRY:
        return None
    return inst, _REGISTRY[inst["key"]]()
