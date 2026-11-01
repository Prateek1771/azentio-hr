"""Shared document intake: email attachments and portal uploads both land here."""
import mimetypes
from pathlib import Path

import checklist
import records
import extract
import logging_setup as ls
import validate
from documents import store as docs
from graph import new_id, event
from shared import db

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
    with db.tx() as c:
        docs.insert({"id": doc_id, "employee_id": eid, "filename": filename, "path": str(path), "mime": mime,
                     "size": len(data), "source": source, "status": "Submitted", "doc_type": item_key or ""}, c)
    event(eid, "document", f"Received '{filename}' via {source}")

    doc_types = checklist.config("doc_types") or {}
    file_issues = validate.check_file(mime, len(data))
    if file_issues:
        ex, result = {}, {"status": "Correction Requested", "issues": file_issues, "mismatches": []}
    else:
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

    with db.tx() as c:
        docs.update(doc_id, c, status=result["status"], extracted=ex, issues=result["issues"],
                    confidence=float(ex.get("confidence") or 0), doc_type=key, model=ex.get("_model"),
                    item_id=item["id"] if item else None)
        docs.set_mismatches(doc_id, result["mismatches"], c)
    ls.log_event(ls.get("documents"), "document.checked", doc=doc_id, employee=eid, source=source, status=result["status"],
                 issues=len(result["issues"]), model=ex.get("_model"), tokens=ex.get("_tokens"))
    if item:
        if item["status"] != "Accepted":  # a later bad upload never downgrades an accepted item
            records.update_item(item["id"], status=result["status"])
        label = item["name"]
    else:
        label = key
    records.project(eid)  # refresh the graph view of this document
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
