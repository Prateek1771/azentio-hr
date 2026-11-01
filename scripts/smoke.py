"""UI smoke test: for every role, sign in through the real login form, check the navigation, check that allowed
pages render and forbidden ones are gated, run one representative workflow, and save screenshots.

Drives headless Chrome over the DevTools protocol (websockets ships with uvicorn[standard]; no Playwright).
Needs the stack running (docker compose, backend, frontend) with the dev seed users.

Usage:  backend/.venv/Scripts/python scripts/smoke.py [--base http://localhost:3000] [--shots docs/screenshots]
"""
import argparse
import asyncio
import base64
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

CHROME_CANDIDATES = [
    os.getenv("CHROME", ""),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    "/usr/bin/google-chrome", "/usr/bin/chromium", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
]

NAV_ALL = ["Dashboard", "Add joiners", "Email templates", "Requirement rules", "Connectors", "Integrations",
           "Document review", "Approvals", "Logs", "AI quality", "Activity", "Users", "System"]
STAFF_NAV = [n for n in NAV_ALL if n not in ("Add joiners", "Users", "System")]

# role -> (email, password, nav labels expected, allowed pages, a forbidden page)
ROLES = {
    "admin": ("admin@azentio.local", "admin-dev-pass", STAFF_NAV + ["Users", "System"], ["/dashboard", "/connectors", "/users", "/system"], "/import"),
    "hr_ops": ("hrops@azentio.local", "hrops-dev-pass", STAFF_NAV[:1] + ["Add joiners"] + STAFF_NAV[1:], ["/dashboard", "/rules", "/import"], "/users"),
    "approver": ("approver@azentio.local", "approver-dev-pass", STAFF_NAV, ["/dashboard", "/approvals"], "/users"),
    "manager": ("manager@azentio.local", "manager-dev-pass", ["Dashboard", "Activity"], ["/dashboard", "/activity"], "/rules"),
    "auditor": ("auditor@azentio.local", "auditor-dev-pass", STAFF_NAV + ["System"], ["/dashboard", "/logs", "/system"], "/users"),
}
GATED = "have access to this page"


class Page:
    def __init__(self, ws):
        self.ws, self.ids = ws, itertools.count(1)

    async def send(self, method, **params):
        i = next(self.ids)
        await self.ws.send(json.dumps({"id": i, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == i:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})

    async def js(self, expr):
        r = await self.send("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
        if "exceptionDetails" in r:
            raise RuntimeError(f"js failed: {expr[:80]}: {r['exceptionDetails'].get('text')}")
        return r["result"].get("value")

    async def goto(self, url):
        await self.send("Page.navigate", url=url)
        await self.settle()

    async def settle(self, timeout=20.0):
        """Wait until the document is loaded and no skeletons are left (client fetches done)."""
        end = time.time() + timeout
        while time.time() < end:
            ready = await self.js("document.readyState === 'complete' && !document.querySelector('[data-slot=skeleton]')")
            if ready:
                await asyncio.sleep(0.6)
                return
            await asyncio.sleep(0.25)

    async def wait_for(self, expr, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            if await self.js(expr):
                return True
            await asyncio.sleep(0.25)
        return False

    async def text(self):
        return await self.js("document.body.innerText")

    async def fill(self, selector, value):
        """Set a React-controlled input/textarea (native setter + input event)."""
        await self.js(f"""(() => {{ const el = document.querySelector({json.dumps(selector)});
            const proto = el.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto, 'value').set.call(el, {json.dumps(str(value))});
            el.dispatchEvent(new Event('input', {{ bubbles: true }})); return true; }})()""")

    async def click_text(self, text, scope="button, a"):
        """Click the first visible element in `scope` whose text includes `text`."""
        ok = await self.js(f"""(() => {{ const el = [...document.querySelectorAll({json.dumps(scope)})]
            .filter(e => e.getClientRects().length > 0 && !e.disabled)  /* offsetParent is null inside fixed dialogs */.sort((a, b) => (b.innerText.trim() === {json.dumps(text)}) - (a.innerText.trim() === {json.dumps(text)}))
            .find(e => e.innerText.includes({json.dumps(text)}));  /* exact text first: "Approver" contains "Approve" */
            if (!el) return false; el.click(); return true; }})()""")
        if not ok:
            raise RuntimeError(f"no clickable '{text}'")

    async def api(self, method, path, body=None):
        """Call the API from the page (same origin, session cookie)."""
        return await self.js(f"""fetch({json.dumps(path)}, {{ method: {json.dumps(method)}, headers: {{'content-type': 'application/json'}},
            body: {json.dumps(json.dumps(body)) if body is not None else 'undefined'} }}).then(r => r.json())""")

    async def shot(self, path: Path):
        data = (await self.send("Page.captureScreenshot", format="png"))["data"]
        path.write_bytes(base64.b64decode(data))


def find_chrome():
    for c in CHROME_CANDIDATES:
        if c and Path(c).exists():
            return c
    sys.exit("smoke: Chrome/Edge not found; set CHROME=/path/to/chrome")


async def run(base: str, shots: Path) -> int:
    shots.mkdir(parents=True, exist_ok=True)
    profile = tempfile.mkdtemp(prefix="smoke-")
    port = 9333
    chrome_log = Path(profile) / "chrome-stderr.log"

    def launch():
        return subprocess.Popen([find_chrome(), "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                                 "--window-size=1440,900", "--hide-scrollbars", "--no-first-run",
                                 *(["--no-sandbox"] if os.getenv("CI") else []), "about:blank"],  # CI runners restrict user namespaces
                                stdout=subprocess.DEVNULL, stderr=open(chrome_log, "ab"))

    proc = launch()
    failures = []
    try:
        # A cold runner can take a while to start Chrome: wait up to 60s, relaunch once if it died during startup,
        # and if it still isn't reachable say why (its own stderr) instead of failing blind.
        ws_url, relaunched, deadline = None, False, time.time() + 60
        while time.time() < deadline:
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json", timeout=2))
                ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
                break
            except Exception:
                if proc.poll() is not None and not relaunched:
                    relaunched, proc = True, launch()
                time.sleep(0.25)
        if not ws_url:
            tail = chrome_log.read_text(errors="replace")[-2000:] if chrome_log.exists() else "(no output)"
            sys.exit(f"smoke: Chrome's DevTools endpoint didn't come up within 60s (exit code {proc.poll()}). Chrome said:\n{tail}")
        async with websockets.connect(ws_url, max_size=50_000_000) as ws:
            p = Page(ws)
            await p.send("Page.enable")
            await p.send("Network.enable")
            await p.send("Emulation.setDeviceMetricsOverride", width=1440, height=900, deviceScaleFactor=1, mobile=False)

            def expect(cond, what):
                print(f"  {'ok  ' if cond else 'FAIL'} {what}")
                if not cond:
                    failures.append(what)

            # Signed out: protected pages bounce to /login
            await p.send("Network.clearBrowserCookies")
            await p.goto(f"{base}/dashboard")
            expect(await p.wait_for("location.pathname === '/login'"), "signed out: /dashboard redirects to /login")
            await p.shot(shots / "login.png")

            async def sign_in(email, pw):
                await p.send("Network.clearBrowserCookies")
                await p.goto(f"{base}/login?next=/dashboard")
                await p.fill("#email", email)
                await p.fill("#password", pw)
                await asyncio.sleep(0.2)
                await p.js("document.querySelector('form button[type=submit]').click()")
                signed = await p.wait_for("location.pathname === '/dashboard'")
                await p.settle()
                return signed

            for role, (email, pw, nav, allowed, forbidden) in ROLES.items():
                print(f"[{role}]")
                expect(await sign_in(email, pw), f"{role}: login form signs in")

                got = await p.js("[...document.querySelectorAll('aside nav a')].map(a => a.innerText.trim().split(String.fromCharCode(10))[0])")  # label, not its count badge
                expect(got == nav, f"{role}: nav is {nav}" + ("" if got == nav else f" (got {got})"))

                for path in allowed:
                    await p.goto(f"{base}{path}")
                    t = await p.text()
                    expect(GATED not in t and "Application error" not in t and "Sign in" not in t[:40], f"{role}: {path} renders")
                    await p.shot(shots / f"{role}{path.replace('/', '-')}.png")

                await p.goto(f"{base}{forbidden}")
                expect(GATED in await p.text(), f"{role}: {forbidden} is gated")

                # One representative workflow per role
                if role == "hr_ops":
                    await p.goto(f"{base}/employees/E1006")
                    await p.js("[...document.querySelectorAll('button')].find(b => b.innerText.includes('Send document request')).click()")
                    ok = await p.wait_for("!!document.querySelector('[role=dialog]') && document.querySelector('[role=dialog]').innerText.length > 40")
                    expect(ok, "hr_ops: employee E1006 opens the document-request email preview")
                    await p.shot(shots / "hr_ops-email-preview.png")
                elif role == "admin":
                    await p.goto(f"{base}/users")
                    n = await p.js("document.querySelectorAll('tbody tr').length")
                    expect(n >= 5, f"admin: Users lists the 5 seed accounts (got {n})")
                    await p.shot(shots / "users.png")
                elif role == "manager":
                    await p.goto(f"{base}/dashboard")
                    rows = await p.js("document.querySelectorAll('tbody tr').length")
                    expect(rows == 2, f"manager: dashboard shows only their 2 reports (got {rows})")
                    await p.goto(f"{base}/employees/E1001")
                    expect("not found" in (await p.text()).lower(), "manager: someone else's report is not visible")
                elif role == "auditor":
                    await p.goto(f"{base}/logs")
                    await p.js("[...document.querySelectorAll('[role=tab]')].find(t => t.innerText.includes('Audit')).click()")
                    ok = await p.wait_for("document.body.innerText.includes('signed in')")
                    expect(ok, "auditor: audit log shows sign-in entries with real users")
                    await p.shot(shots / "audit-log.png")
                elif role == "approver":
                    await p.goto(f"{base}/approvals")
                    expect("Approval Center" in await p.text(), "approver: Approval Center loads")

                # Sign out from the user menu
                # Sign out from a clean page (an open dialog would cover the menu).
                # base-ui menus open on pointer events, so use real mouse input via CDP.
                await p.goto(f"{base}/dashboard")
                box = await p.js("(() => { const r = document.querySelector('[aria-label=\"Account menu\"]').getBoundingClientRect();"
                                 " return [r.x + r.width / 2, r.y + r.height / 2]; })()")
                for kind in ("mousePressed", "mouseReleased"):
                    await p.send("Input.dispatchMouseEvent", type=kind, x=box[0], y=box[1], button="left", clickCount=1)
                await p.wait_for("[...document.querySelectorAll('[role=menuitem]')].some(m => m.innerText.includes('Sign out'))", 5)
                await p.js("[...document.querySelectorAll('[role=menuitem]')].find(m => m.innerText.includes('Sign out')).click()")
                expect(await p.wait_for("location.pathname === '/login'"), f"{role}: sign out returns to /login")

            # Policy governance (Phase 1): draft -> preview -> submit -> approve -> published
            print("[policy]")
            approvers = [("approver@azentio.local", "approver-dev-pass"), ("approver2@azentio.local", "approver2-dev-pass")]
            await sign_in("hrops@azentio.local", "hrops-dev-pass")
            vs = await p.api("GET", "/api/policy/versions")
            pending = next((v for v in vs["versions"] if v["state"] == "pending_approval"), None)
            if pending:  # finish whatever is waiting first, so the flow below has the draft slot
                for em, pw2 in approvers:
                    await sign_in(em, pw2)
                    await p.api("POST", f"/api/policy/versions/{pending['id']}/approve", {"reason": "smoke: clear queue"})
                for _ in range(40):
                    if (await p.api("GET", f"/api/policy/versions/{pending['id']}"))["state"] == "active":
                        break
                    await asyncio.sleep(1)
                await sign_in("hrops@azentio.local", "hrops-dev-pass")
            before = (await p.api("GET", "/api/rules"))["version"]["number"]
            await p.goto(f"{base}/rules")
            expect(f"Version {before} active" in await p.text(), f"policy: rules page shows active version {before}")
            await p.shot(shots / "rules.png")
            pan = next(r for r in (await p.api("GET", "/api/rules"))["rules"] if r["id"] == "R-PAN")
            new_offset = -8 if pan["offset_days"] != -8 else -7
            await p.click_text("Continue draft" if "Continue draft" in await p.text() else "Propose changes")
            expect(await p.wait_for("location.pathname === '/rules/draft' && !!document.querySelector('[aria-label=\"Edit R-PAN\"]')"),
                   "policy: HR Ops opens a draft")
            await p.js("document.querySelector('[aria-label=\"Edit R-PAN\"]').click()")
            await p.wait_for("!!document.querySelector('#rf-due')")
            await p.fill("#rf-due", new_offset)
            await p.click_text("Save to draft", "[role=dialog] button")
            expect(await p.wait_for("document.body.innerText.includes('Changed')"), "policy: rule edit lands in the draft diff")
            await p.shot(shots / "rules-draft.png")
            await p.click_text("Preview impact")
            expect(await p.wait_for("location.pathname === '/rules/draft/preview' && document.body.innerText.includes('Joiners affected')"),
                   "policy: impact preview renders")
            await p.settle()
            await p.shot(shots / "rules-preview.png")
            await p.fill("#sub-title", f"PAN deadline {abs(new_offset)} days before joining")
            await p.fill("#sub-reason", "Smoke test: payroll needs PAN slightly later.")
            await p.click_text("Submit for approval", "button")
            expect(await p.wait_for("location.pathname === '/rules/history'"), "policy: submit goes to version history")
            await p.settle()
            await p.shot(shots / "rules-history.png")
            vs = await p.api("GET", "/api/policy/versions")
            vid = vs["open_id"]
            expect(vid is not None, "policy: submitted version is open")

            await sign_in(*approvers[0])
            await p.goto(f"{base}/approvals?tab=policy")
            await p.wait_for("document.body.innerText.includes('Rule set v')")
            await p.settle()
            await p.shot(shots / "approvals-policy.png")
            await p.click_text("Approve", "button")
            await p.wait_for("!!document.querySelector('[role=dialog]')")
            await p.click_text("Approve", "[role=dialog] button")
            published = False
            for _ in range(40):
                v = await p.api("GET", f"/api/policy/versions/{vid}")
                if v["state"] == "active":
                    published = True
                    break
                await asyncio.sleep(1)
            expect(published, "policy: approver's approval publishes the version")
            await p.goto(f"{base}/rules")
            expect(f"Version {before + 1} active" in await p.text(), f"policy: rules page now shows version {before + 1}")

            # Rollback: HR Ops rolls back to the version just replaced -> approver approves -> rules equal the target's
            print("[rollback]")
            target = next(v for v in (await p.api("GET", "/api/policy/versions"))["versions"] if v["number"] == before)
            await sign_in("hrops@azentio.local", "hrops-dev-pass")
            await p.goto(f"{base}/rules/history")
            await p.wait_for("document.body.innerText.includes('Roll back to this')")
            await p.click_text("Roll back to this")  # newest superseded version first, i.e. v{before}
            expect(await p.wait_for(f"!!document.querySelector('[role=dialog]') && document.querySelector('[role=dialog]').innerText.includes('Roll back to v{before}')"),
                   f"rollback: dialog offers v{before}")
            await p.fill("[role=dialog] textarea", "Smoke test: payroll changed its mind.")
            await p.click_text("Create rollback", "[role=dialog] button")
            vs = await p.api("GET", "/api/policy/versions")
            rb = next((v for v in vs["versions"] if v["id"] == vs["open_id"]), None) if vs["open_id"] else None
            for _ in range(20):
                if rb:
                    break
                await asyncio.sleep(0.5)
                vs = await p.api("GET", "/api/policy/versions")
                rb = next((v for v in vs["versions"] if v["id"] == vs["open_id"]), None) if vs["open_id"] else None
            expect(rb is not None and rb["rollback_of"] == target["id"] and rb["state"] == "pending_approval",
                   "rollback: creates a pending version that rolls back to the target")
            await p.settle()
            await p.shot(shots / "rules-rollback.png")
            if rb:
                for em, pw2 in approvers[:rb["required_approvals"]]:
                    await sign_in(em, pw2)
                    await p.api("POST", f"/api/policy/versions/{rb['id']}/approve", {"reason": "smoke: rollback ok"})
                active = None
                for _ in range(40):
                    v = await p.api("GET", f"/api/policy/versions/{rb['id']}")
                    if v["state"] == "active":
                        active = v
                        break
                    await asyncio.sleep(1)
                strip = lambda rules: sorted(({k: x for k, x in r.items() if k != "ord"} for r in rules), key=lambda r: r["id"])  # noqa: E731
                tgt = await p.api("GET", f"/api/policy/versions/{target['id']}")
                expect(active is not None and active["rollback_of"] == target["id"], "rollback: approval publishes the rollback")
                expect(active is not None and strip(active["rules"]) == strip(tgt["rules"]), f"rollback: active rules equal v{before}'s")

            # Document review (Phase 2): a high-risk document is read-only for HR Ops; an approver accepts it with a reason.
            print("[review]")
            await sign_in("hrops@azentio.local", "hrops-dev-pass")

            async def high_risk_open():
                q = await p.api("GET", "/api/review-queue?kind=doc.review")
                return next((it for it in q.get("items", []) if it["document"] and it["document"]["tier"] == "high"
                             and not it["assignee"]), None)
            target = await high_risk_open()
            if target is None:  # a re-run already decided the seeded one: upload a fresh photo ID through the portal
                tok = (await p.api("GET", "/api/employees/E1006/portal-link"))["token"]
                png = base64.b64encode(bytes.fromhex("89504e470d0a1a0a0000000d4948445200000001000000010806000000"
                                                     "1f15c4890000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082")).decode()
                await p.js(f"""(async () => {{ const b = Uint8Array.from(atob("{png}"), c => c.charCodeAt(0));
                    const fd = new FormData(); fd.append("file", new Blob([b], {{type: "image/png"}}), "smoke_passport.png");
                    fd.append("item_key", "photo_id");
                    return (await fetch("/api/portal/{tok}/upload", {{method: "POST", body: fd}})).status; }})()""")
                for _ in range(120):  # extraction is a job: with no LLM it reaches review (ai_unavailable) after the retries
                    target = await high_risk_open()
                    if target:
                        break
                    await asyncio.sleep(2)
            expect(target is not None, "review: a high-risk document is waiting for review")
            if target:
                did = target["document"]["id"]
                await p.goto(f"{base}/review")
                expect(await p.wait_for("document.body.innerText.includes('High risk')"), "review: queue lists it with its risk tier")
                await p.shot(shots / "review-queue.png")
                await p.goto(f"{base}/review/{did}")
                expect(await p.wait_for("document.body.innerText.includes('decided by an approver')"),
                       "review: HR Ops sees a high-risk document read-only")
                await sign_in("approver@azentio.local", "approver-dev-pass")
                await p.goto(f"{base}/review/{did}")
                await p.wait_for("!!document.querySelector('input[placeholder^=\"Why\"]')")
                await p.settle()
                await p.shot(shots / "review-detail.png")
                page_text = (await p.text()).lower()
                expect("risk tier" in page_text and "policy in effect" in page_text, "review: detail leads with the policy facts")
                await p.fill('input[placeholder^="Why"]', "Smoke test: matches the original passport")
                await asyncio.sleep(0.3)
                await p.click_text("Accept", "button")
                accepted = False
                for _ in range(20):
                    if (await p.api("GET", f"/api/documents/{did}"))["document"]["status"] == "Accepted":
                        accepted = True
                        break
                    await asyncio.sleep(0.5)
                expect(accepted, "review: the approver's decision accepts the document")
                det = await p.api("GET", f"/api/documents/{did}")
                expect(any(x["kind"] == "human_accept" and x["decided_by_email"] == "approver@azentio.local" for x in det["decisions"]),
                       "review: the decision is recorded with the approver and the reason")
                emp = det["document"]["employee_id"]
                await p.goto(f"{base}/employees/{emp}")
                expect(await p.wait_for("document.body.innerText.toLowerCase().includes('accepted by approver@azentio.local')"),
                       "review: the employee page shows who accepted it")

            # Auto-accept and spot check: a low-risk document the policy accepted, then an approver checks the sample.
            print("[auto-accept + spot check]")
            await sign_in("approver@azentio.local", "approver-dev-pass")
            await p.goto(f"{base}/employees/E1004")
            expect(await p.wait_for("document.body.innerText.toLowerCase().includes('auto-accepted')"),
                   "auto-accept: Meera's utility bill shows it was auto-accepted, with confidence and policy version")
            spots = (await p.api("GET", "/api/review-queue?kind=doc.spot_check")).get("items", [])
            if spots:
                sd = spots[0]["document"]["id"]
                await p.goto(f"{base}/review/{sd}")
                await p.wait_for("!!document.querySelector('input[placeholder^=\"Why\"]')")
                await p.settle()
                await p.shot(shots / "review-spot-check.png")
                await p.fill('input[placeholder^="Why"]', "Smoke test: the bill matches the joiner's address")
                await asyncio.sleep(0.3)
                await p.click_text("Correct", "button")
                passed = False
                for _ in range(20):
                    det = await p.api("GET", f"/api/documents/{sd}")
                    if any(x["kind"] == "spot_check_pass" for x in det["decisions"]):
                        passed = True
                        break
                    await asyncio.sleep(0.5)
                expect(passed and det["document"]["status"] == "Accepted",
                       "spot check: the approver's 'Correct' verdict is recorded and the acceptance stands")
            else:  # a re-run: the seeded sample was already checked
                acc = await p.api("GET", "/api/quality/ai-accuracy")
                expect(acc["spot_checks"] >= 1, "spot check: an earlier verdict is recorded")
            await p.goto(f"{base}/quality")
            expect(await p.wait_for("document.body.innerText.includes('AI document decisions')"),
                   "quality: the AI document decisions card renders")
    finally:
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)
    print(f"\nsmoke: {'PASS' if not failures else f'{len(failures)} FAILED'}; screenshots in {shots}")
    return 1 if failures else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.getenv("SMOKE_BASE", "http://localhost:3000"))
    ap.add_argument("--shots", default=str(Path(__file__).resolve().parent.parent / "docs" / "screenshots"))
    a = ap.parse_args()
    sys.exit(asyncio.run(run(a.base.rstrip("/"), Path(a.shots))))
