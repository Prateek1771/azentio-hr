"""Connector key → implementation. Every key in the catalog needs an entry here."""
from . import store
from .apps import APPS
from .base import Connector
from .candidates import CandidateImport, CandidatePortal
from .hr_portal import HrPortal

_REGISTRY: dict[str, type[Connector]] = {
    CandidatePortal.key: CandidatePortal,
    CandidateImport.key: CandidateImport,
    HrPortal.key: HrPortal,
    **APPS,
}


def instance(instance_id: str) -> dict | None:
    return store.instance(instance_id)


def get(instance_id: str) -> tuple[dict, Connector] | None:
    """(instance metadata, implementation) or None — callers turn None into a 404."""
    inst = instance(instance_id)
    if not inst or inst["key"] not in _REGISTRY:
        return None
    return inst, _REGISTRY[inst["key"]]()
