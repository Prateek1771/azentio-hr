"""Shared document intake: email attachments and portal uploads both land here."""
import json
import mimetypes
import time
from pathlib import Path

import checklist
import records
import extract
import logging_setup as ls
import validate
from graph import q, q1, new_id, event

UPLOADS = Path(__file__).parent / "uploads"
UPLOADS.mkdir(exist_ok=True)


def guess_mime(filename: str, given: str | None) -> str:
    if given and given != "application/octet-stream":
        return given
    return mimetypes.guess_type(filename)[0] or "application/octet-stream"


async def intake(eid: str, filename: str, data: bytes, mime: str | None, source: str,
                 item_key: str | None = None, notify=None) -> dict:
    """Save → extract → validate → update graph. `notify(emp, item, issues)` sends a correction email."""
    emp = records.employee(eid)
    mime = guess_mime(filename, mime)
    doc_id = new_id("doc")
    path = UPLOADS / f"{doc_id}_{Path(filename).name}"
    path.write_bytes(data)
    doc = {"id": doc_id, "employee_id": eid, "filename": filename, "path": str(path), "mime": mime,
           "size": len(data), "source": source, "status": "Submitted", "ts": time.time(),
           "extracted": "{}", "issues": "[]", "confidence": 0.0, "doc_type": item_key or ""}
    q("CREATE (d:Document) SET d = $p", p=doc)
    event(eid, "document", f"Received '{filename}' via {source}")

    doc_types = checklist.config("doc_types") or {}
    file_issues = validate.check_file(mime, len(data))
    if file_issues:
        ex, result = {}, {"status": "Correction Requested", "issues": file_issues, "mismatches": []}
    else:
        q("MATCH (d:Document {id:$id}) SET d.status='Under Review'", id=doc_id)
        try:
            ex = await extract.extract(data, mime, filename, doc_types, hint=doc_types.get(item_key, {}).get("name"))
            event(eid, "ai", f"Extracted '{filename}' via {ex.get('_model')} ({ex.get('_tokens') or '?'} tokens), confidence {ex.get('confidence')}")
        except Exception as e:  # LLM outage shouldn't lose the document; a human reviews it
            event(eid, "error", f"Extraction failed for '{filename}': {e}")
            ex = {"doc_type": item_key or "unknown", "confidence": 0.0, "legible": True}
        key = item_key or ex.get("doc_type")
        result = validate.evaluate(ex, emp, doc_types.get(key, {})) if ex.get("confidence") else \
            {"status": "Under Review", "issues": [], "mismatches": []}
        if result["status"] == "Under Review" and ex.get("confidence"):
            verified = await extract.human_verify(str(path), ex, f"Verify the {key} of {emp['name']}")
            if verified:
                ex = verified
                result = validate.evaluate(ex, emp, doc_types.get(key, {}))

    key = item_key or ex.get("doc_type") or "unknown"
    item = records.item_by_key(eid, key)
    if item and (item.get("cancelled_by_version") or item.get("superseded_by_version")):
        item = None  # no longer on the live checklist
    if item is None and not file_issues:
        result = {"status": "Under Review", "issues": [f"Document classified as '{key}', which is not on this checklist."],
                  "mismatches": result["mismatches"]}

    q("""MATCH (d:Document {id:$id}) SET d.status=$s, d.extracted=$x, d.issues=$iss,
         d.confidence=$c, d.doc_type=$k""",
      id=doc_id, s=result["status"], x=json.dumps(ex), iss=json.dumps(result["issues"]),
      c=float(ex.get("confidence") or 0), k=key)
    q("MATCH (e:Employee {id:$e}), (d:Document {id:$id}) CREATE (e)-[:SUBMITTED]->(d)", e=eid, id=doc_id)
    ls.log_event(ls.get("documents"), "document.checked", doc=doc_id, employee=eid, source=source, status=result["status"],
                 issues=len(result["issues"]), model=ex.get("_model"), tokens=ex.get("_tokens"))
    for m in result["mismatches"]:
        q("MATCH (e:Employee {id:$e}), (d:Document {id:$id}) CREATE (d)-[m:MISMATCH]->(e) SET m = $p", e=eid, id=doc_id, p=m)

    if item:
        q("MATCH (i:Item {id:$i}), (d:Document {id:$d}) CREATE (i)-[:HAS_DOC]->(d)", i=item["id"], d=doc_id)
        if item["status"] != "Accepted":  # a later bad upload never downgrades an accepted item
            records.update_item(item["id"], status=result["status"])
        label = item["name"]
    else:
        label = key
    event(eid, "verification", f"{label}: {result['status']}" + (f" — {'; '.join(result['issues'])}" if result["issues"] else ""))

    if result["status"] == "Correction Requested" and notify:
        try:
            await notify(emp, item or {"name": filename}, result["issues"])
        except Exception as e:
            event(eid, "error", f"Could not send correction email: {e}")
    checklist.recompute(eid)
    try:  # tell an integration waiting on this joiner's documents, like a document-service webhook
        from integrations.engine import on_documents_changed
        on_documents_changed(eid)
    except Exception as e:
        event(eid, "error", f"Integration update after document check failed: {e}")
    return {"document_id": doc_id, "item": label, **result, "extracted": ex}
