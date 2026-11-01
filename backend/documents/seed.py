"""Dev only, once: one document in each review state, so the review screens and AI metrics aren't empty.

Each document goes through the real routing (`route.decide` + `service.apply_route`) with a canned extraction labelled
model 'seed-demo', so its decision row and provenance are produced exactly as in production. Files are small generated
PNGs written to the uploads folder.
"""
import io

import records
from documents import route, service, store
from shared import db

SPECS = [  # employee, item key, filename, extraction overrides, AI available
    ("E1006", "photo_id", "kabir_passport.png", {"confidence": 0.97}, True),               # high risk -> review
    ("E1004", "address_proof", "meera_utility_bill.png", {"confidence": 0.95}, True),      # auto-accept, spot-checked
    ("E1002", "bank_details", "diya_cancelled_cheque.png", {"confidence": 0.72}, True),    # low confidence -> review
    ("E1003", "pan_card", "rohan_pan.png", {"confidence": 0.96, "date_of_birth": "1995-01-19"}, True),  # DOB mismatch
    ("E1001", "education_certificate", "aarav_degree.png", {"confidence": 0.81}, True),    # review -> accepted by approver
    ("E1001", "experience_letter", "aarav_relieving_letter.png", {}, False),               # AI down -> review
]


def _png(title: str, lines: list[str]) -> bytes:
    from PIL import Image, ImageDraw
    img = Image.new("RGB", (900, 560), "#f8fafc")
    d = ImageDraw.Draw(img)
    d.rectangle([20, 20, 880, 540], outline="#334155", width=3)
    d.rectangle([20, 20, 880, 90], fill="#1e3a8a")
    d.text((40, 45), title.upper(), fill="white")
    for i, line in enumerate(lines):
        d.text((50, 130 + i * 40), line, fill="#0f172a")
    d.text((50, 500), "SAMPLE DOCUMENT - generated for the demo", fill="#94a3b8")
    out = io.BytesIO()
    img.save(out, "PNG")
    return out.getvalue()


def accept_for_demo(eid: str, key: str, conn=None) -> str | None:
    """Demo/test data only: an accepted sample document behind a document item, recorded as decision kind 'demo_seed'
    (never presented as an AI or human decision; excluded from AI metrics). The item status is derived as usual."""
    item = service._live_item(eid, key, conn=conn)
    if not item or item["kind"] != "document":
        return None
    from graph import new_id
    doc_id = new_id("doc")
    data = _png(key.replace("_", " "), [f"Sample {key.replace('_', ' ')} for {eid}"])
    path = service.UPLOADS / f"{doc_id}_demo_{key}.png"
    path.write_bytes(data)

    def write(c):
        store.insert({"id": doc_id, "employee_id": eid, "item_id": item["id"], "filename": f"demo_{key}.png", "path": str(path),
                      "mime": "image/png", "size": len(data), "source": "portal", "status": "Submitted", "doc_type": key,
                      "seed": "demo_progress"}, c)
        store.add_decision({"document_id": doc_id, "kind": "demo_seed", "reason_code": "demo_seed", "status_after": "Accepted",
                            "reason_text": "Sample data so the demo shows progress; not a real check",
                            "idempotency_key": f"demo_seed:{doc_id}"}, c)
        service._sync_item(item, "Accepted", c)
    if conn is not None:
        write(conn)
    else:
        with db.tx() as c:
            write(c)
        records.project(eid)
    return doc_id


def seed_demo_documents() -> int:
    if db.fetch1("SELECT 1 AS x FROM documents WHERE extra->>'seed' = 'demo'"):
        return 0
    approver = db.fetch1("SELECT id, email, role::text AS role FROM users WHERE email='approver@azentio.local'")
    vid, policy, doc_types = service.active_policy()
    made = 0
    for eid, key, filename, over, ai_ok in SPECS:
        emp = records.employee(eid)
        item = service._live_item(eid, key)
        if not emp or not item:
            continue
        ex = {"doc_type": key, "legible": True, "full_name": emp["name"], "date_of_birth": emp.get("date_of_birth") or None,
              "document_number": "DEMO-" + eid, "issue_date": "2021-06-01", "address": f"{emp['location']}, India",
              "expiry_date": "2034-06-01", "issuer": "Demo issuer", "confidence": 0.9,
              "_model": "seed-demo", "_prompt_version": "2026-11-01", **over}
        data = _png(doc_types.get(key, {}).get("name", key), [f"{k.replace('_', ' ')}: {v}" for k, v in ex.items()
                                                             if not k.startswith("_") and k not in ("legible", "confidence", "doc_type")])
        from graph import new_id
        doc_id = new_id("doc")
        path = service.UPLOADS / f"{doc_id}_{filename}"
        path.write_bytes(data)
        with db.tx() as c:
            store.insert({"id": doc_id, "employee_id": eid, "item_id": item["id"], "filename": filename, "path": str(path),
                          "mime": "image/png", "size": len(data), "source": "portal", "status": "Submitted", "doc_type": key,
                          "item_key": key, "seed": "demo"}, c)
        tier, threshold = route.tier_for(key, policy)
        if ai_ok:
            import validate
            check = validate.evaluate(ex, emp, doc_types.get(key, {}))
        else:
            ex, check = {}, {"issues": [], "mismatches": [], "checks": []}
        r = route.decide(file_issues=[], issues=check["issues"], extraction_ok=ai_ok, on_checklist=True, tier=tier,
                         confidence=float(ex.get("confidence") or 0), threshold=threshold, doc_id=doc_id,
                         spot_check_rate=1.0 if key == "address_proof" else 0.0)
        service.apply_route(doc_id, r, key=key, item=item, ex=ex, check=check, file_issues=[], tier=tier,
                            threshold=threshold, policy_version_id=vid)
        if key == "education_certificate" and approver:
            service.decide_human(doc_id, approver, "accept", "verified_against_original",
                                 "Degree certificate matches the university's verification letter")
        made += 1
    return made


if __name__ == "__main__":
    assert route.decide(file_issues=[], issues=[], extraction_ok=False, on_checklist=True, tier="low", confidence=0,
                        threshold=0.9).reason_code == "ai_unavailable"
    print("seed: ok")
