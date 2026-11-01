"""Shared document intake: email attachments and portal uploads both land here.

Since Phase 2 this only *receives*: the file is saved, the document row is inserted as Submitted, and extraction is
queued as a job (documents.service). Routing, the decision and its side effects happen in that job, so an LLM outage
or a slow model never blocks an upload or the mail poller.
"""
from documents import service

UPLOADS = service.UPLOADS
guess_mime = service.guess_mime


async def intake(eid: str, filename: str, data: bytes, mime: str | None, source: str,
                 item_key: str | None = None, notify=None) -> dict:
    """Returns {document_id, status: 'Submitted'}. `notify` is ignored: correction emails are an outbox job now."""
    return service.receive(eid, filename, data, mime, source, item_key)
