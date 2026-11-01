"""Gmail: send templated requests, poll replies, hand attachments to the intake pipeline."""
import asyncio
import base64
import os
import time
from email.mime.text import MIMEText
from email.utils import parseaddr
from functools import lru_cache

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from jinja2 import Environment
from markupsafe import Markup

import logging_setup as ls
import pipeline
import records
from graph import q1, event

LABEL = "azentio-processed"
_log = ls.get("gmail")
_env = Environment(autoescape=True)


def configured() -> bool:
    return all(os.getenv(k) for k in ("GMAIL_CLIENT_ID", "GMAIL_CLIENT_SECRET", "GMAIL_REFRESH_TOKEN"))


@lru_cache
def svc():
    creds = Credentials(None, refresh_token=os.environ["GMAIL_REFRESH_TOKEN"],
                        client_id=os.environ["GMAIL_CLIENT_ID"], client_secret=os.environ["GMAIL_CLIENT_SECRET"],
                        token_uri="https://oauth2.googleapis.com/token")
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def render(template_id: str, ctx: dict) -> tuple[str, str]:
    t = q1("MATCH (t:EmailTemplate {id:$id}) RETURN t", id=template_id)["t"]
    ctx = {k: Markup(v) if k in ("doc_list", "issues") else v for k, v in ctx.items()}
    return _env.from_string(t["subject"]).render(**ctx), _env.from_string(t["body"]).render(**ctx)


def employee_ctx(emp: dict) -> dict:
    base = os.getenv("PUBLIC_BASE_URL", "http://localhost:3000")
    return {**emp, "first_name": emp["name"].split()[0], "portal_link": f"{base}/portal/{emp['token']}"}


def _send_sync(to: str, subject: str, html: str, thread_id: str | None) -> dict:
    redirect = os.getenv("EMAIL_REDIRECT_TO", "").strip()
    if redirect and redirect.lower() != to.lower():  # demo mode: fake seed addresses go to one real inbox
        subject, to = f"[to {to}] {subject}", redirect
    msg = MIMEText(html, "html", "utf-8")
    msg["To"], msg["Subject"] = to, subject
    if os.getenv("HR_MAILBOX"):
        msg["From"] = f"Azentio People Team <{os.environ['HR_MAILBOX']}>"
    body = {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}
    if thread_id:
        body["threadId"] = thread_id
    return svc().users().messages().send(userId="me", body=body).execute()


async def send(to: str, subject: str, html: str, thread_id: str | None = None) -> dict:
    return await asyncio.to_thread(_send_sync, to, subject, html, thread_id)


async def send_request(eid: str, template_id: str) -> dict:
    emp = records.employee(eid)
    docs = [i for i in records.items(eid) if i["kind"] == "document" and i["status"] != "Accepted"]
    doc_list = "<ul>" + "".join(f"<li><b>{d['name']}</b> — by {d['due']}</li>" for d in docs) + "</ul>"
    subject, html = render(template_id, {**employee_ctx(emp), "doc_list": doc_list})
    sent = await send(emp["email"], subject, html)
    records.update_employee(eid, thread_id=sent["threadId"], request_sent=int(time.time() * 1000))
    event(eid, "email", f"Document request '{subject}' sent to {emp['email']}")
    return {"thread_id": sent["threadId"], "subject": subject}


async def send_correction(emp: dict, item: dict, issues: list[str]):
    issues_html = "".join(f"<li>{i}</li>" for i in issues)
    subject, html = render("correction", {**employee_ctx(emp), "item_name": item["name"], "issues": issues_html})
    await send(emp["email"], subject, html, emp.get("thread_id"))
    event(emp["id"], "email", f"Correction request for '{item['name']}' emailed to {emp['email']}")


def _label_id() -> str:
    labels = svc().users().labels().list(userId="me").execute().get("labels", [])
    for lb in labels:
        if lb["name"] == LABEL:
            return lb["id"]
    return svc().users().labels().create(userId="me", body={"name": LABEL}).execute()["id"]


def _attachments(msg_id: str, payload: dict) -> list[tuple[str, str, bytes]]:
    out, stack = [], [payload]
    while stack:
        p = stack.pop()
        stack.extend(p.get("parts", []) or [])
        att_id = (p.get("body") or {}).get("attachmentId")
        if p.get("filename") and att_id:
            data = svc().users().messages().attachments().get(userId="me", messageId=msg_id, id=att_id).execute()["data"]
            out.append((p["filename"], p.get("mimeType", ""), base64.urlsafe_b64decode(data)))
    return out


def _fetch_new(senders: list[str]) -> list[dict]:
    """Only mail from known employee addresses is read or labelled; the rest of the inbox is never touched."""
    if not senders:
        return []
    label = _label_id()
    frm = " ".join(f"from:{s}" for s in senders)
    res = svc().users().messages().list(userId="me", q=f"{{{frm}}} has:attachment -label:{LABEL} newer_than:14d",
                                        maxResults=25).execute()
    out = []
    for m in res.get("messages", []):
        full = svc().users().messages().get(userId="me", id=m["id"]).execute()
        headers = {h["name"].lower(): h["value"] for h in full["payload"].get("headers", [])}
        out.append({"id": m["id"], "thread_id": full["threadId"], "from": parseaddr(headers.get("from", ""))[1].lower(),
                    "subject": headers.get("subject", ""), "attachments": _attachments(m["id"], full["payload"])})
        svc().users().messages().modify(userId="me", id=m["id"], body={"addLabelIds": [label]}).execute()
    return out


async def poll() -> int:
    """Process unread reply attachments. Matches by Gmail thread, falls back to sender address."""
    senders = sorted(records.emails())
    if os.getenv("EMAIL_REDIRECT_TO"):
        senders = sorted({*senders, os.environ["EMAIL_REDIRECT_TO"].strip().lower()})
    messages = await asyncio.to_thread(_fetch_new, senders)
    processed = 0
    ls.log_event(_log, "gmail.poll", senders=len(senders), messages=len(messages))
    for m in messages:
        emp = records.employee_by(thread_id=m["thread_id"]) or records.employee_by(email=m["from"])
        if not emp:
            event(None, "email", f"Unmatched email from {m['from']} ('{m['subject']}') ignored")
            continue
        eid = emp["id"]
        event(eid, "email", f"Reply received from {m['from']} with {len(m['attachments'])} attachment(s) — running verification")
        for filename, mime, data in m["attachments"]:
            await pipeline.intake(eid, filename, data, mime, "email")
            processed += 1
    return processed


async def poll_forever(interval: int = 60):
    while True:
        try:
            if configured():
                await poll()
        except Exception as e:
            event(None, "error", f"Gmail poll failed: {e}")
        await asyncio.sleep(interval)
