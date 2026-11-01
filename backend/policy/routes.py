from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from identity.service import current_user
from policy import service, store
from policy.store import PolicyError

router = APIRouter(prefix="/api/policy")


def _call(fn, *a):
    try:
        return fn(*a)
    except PolicyError as e:
        raise HTTPException(e.status, str(e))


class RuleBody(BaseModel):
    id: str = Field(min_length=1, max_length=40, pattern=r"^[A-Za-z0-9_.-]+$")
    item: str = Field(min_length=1, max_length=80)
    name: str = ""
    kind: str = Field(pattern=r"^(document|task|ack)$")
    category: str = Field(min_length=1, max_length=40)
    match: dict = {}
    offset_days: int = Field(0, ge=-365, le=365)
    depends_on: list[str] = []
    description: str = Field(min_length=1, max_length=1000)
    policy_ref: str = ""
    auto: str = ""
    compliance: bool = False


class Decision(BaseModel):
    reason: str = Field("", max_length=2000)


class SubmitBody(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    reason: str = Field(min_length=3, max_length=2000)


@router.get("/versions")
def versions():
    act, opn = store.active_version(), store.open_version()
    return {"versions": store.versions(), "active_id": act["id"] if act else None, "open_id": opn["id"] if opn else None}


@router.get("/versions/{vid}")
def version(vid: int):
    return _call(service.version_detail, vid)


@router.get("/versions/{vid}/diff")
def version_diff(vid: int, against: int | None = None):
    def run():
        if against is None:
            act = store.active_version()
            base = act["id"] if act and act["id"] != vid else (store.version(vid) or {}).get("based_on")
        else:
            base = against
        return store.diff(store.rules_of(base) if base else [], store.rules_of(vid))
    return _call(run)


@router.get("/versions/{vid}/preview")
def version_preview(vid: int, department: str = "", role: str = "", location: str = "", join_from: str = "",
                    join_to: str = "", status: str = ""):
    return _call(service.preview, vid, {"department": department, "role": role, "location": location,
                                        "join_from": join_from, "join_to": join_to, "status": status})


@router.post("/drafts", status_code=201)
def open_draft():
    return _call(lambda: service.version_detail(store.open_draft(current_user.get()["id"])))


@router.put("/drafts/{vid}/rules/{rid}")
def upsert_rule(vid: int, rid: str, body: RuleBody):
    if body.id != rid:
        raise HTTPException(422, "Rule id in the path and body differ")
    _call(store.upsert_draft_rule, vid, body.model_dump())
    return _call(service.version_detail, vid)


class DocPolicyBody(BaseModel):
    default: dict = Field(default_factory=dict)
    types: dict = Field(default_factory=dict)
    spot_check_rate: float | None = None
    extraction_deadline_minutes: float | None = None


@router.put("/drafts/{vid}/doc-policy")
def set_doc_policy(vid: int, body: DocPolicyBody):
    _call(store.set_draft_doc_policy, vid, body.model_dump(exclude_none=True))
    return _call(service.version_detail, vid)


@router.delete("/drafts/{vid}/rules/{rid}")
def delete_rule(vid: int, rid: str):
    _call(store.delete_draft_rule, vid, rid)
    return _call(service.version_detail, vid)


@router.delete("/drafts/{vid}", status_code=204)
def discard(vid: int):
    _call(store.discard_draft, vid, current_user.get())


@router.post("/drafts/{vid}/submit")
def submit(vid: int, body: SubmitBody):
    return _call(service.submit, vid, current_user.get(), body.title, body.reason)


@router.post("/versions/{vid}/approve")
def approve(vid: int, body: Decision):
    return _call(service.approve, vid, current_user.get(), body.reason)


@router.post("/versions/{vid}/reject")
def reject(vid: int, body: Decision):
    return _call(service.reject, vid, current_user.get(), body.reason)


@router.post("/versions/{vid}/rollback")
def rollback(vid: int, body: Decision):
    return _call(service.rollback, vid, current_user.get(), body.reason)
