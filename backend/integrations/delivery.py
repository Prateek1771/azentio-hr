"""Outbound messages for integrations: real Gmail when configured, otherwise recorded as simulated.

Every message is stored as an (:OutboundMessage) exactly as sent, so the integration's "Sent messages"
tab shows what went out and through which channel.
"""
import time

import gmail
import logging_setup as ls
from graph import q, new_id

log = ls.get("delivery")


def is_simulated() -> bool:
    return not gmail.configured()


def record(iid: str, item_id: str | None, kind: str, to: str, subject: str, content: str, attempt: int,
           delivered_by: str, provider_ref: str, candidate: str = "") -> None:
    q("CREATE (m:OutboundMessage) SET m = $p", p={
        "id": new_id("msg"), "integration_id": iid, "item_id": item_id or "", "kind": kind, "to": to, "subject": subject,
        "content": content, "attempt": attempt, "delivered_by": delivered_by, "provider_ref": provider_ref,
        "candidate": candidate, "ts": time.time()})
    ls.log_event(log, "delivery.sent", kind=kind, attempt=attempt, item=item_id, via=delivered_by, ref=provider_ref)


async def send_email(iid: str, item_id: str | None, kind: str, to: str, subject: str, html: str, attempt: int = 1,
                     candidate: str = "", thread_id: str | None = None) -> str:
    """Send through Gmail, or record a simulated send. Returns the provider reference."""
    if is_simulated():
        ref = f"sim-{new_id('x')[2:14]}"
        record(iid, item_id, kind, to, subject, html, attempt, "simulated", ref, candidate)
        return ref
    sent = await gmail.send(to, subject, html, thread_id)
    record(iid, item_id, kind, to, subject, html, attempt, "gmail", sent.get("id", ""), candidate)
    return sent.get("id", "")


def messages(iid: str) -> list[dict]:
    return [r["m"] for r in q("MATCH (m:OutboundMessage {integration_id:$i}) RETURN m ORDER BY m.ts DESC", i=iid)]
