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
           "Approvals", "Logs", "AI quality", "Activity", "Users"]
STAFF_NAV = [n for n in NAV_ALL if n not in ("Add joiners", "Users")]

# role -> (email, password, nav labels expected, allowed pages, a forbidden page)
ROLES = {
    "admin": ("admin@azentio.local", "admin-dev-pass", STAFF_NAV + ["Users"], ["/dashboard", "/connectors", "/users"], "/import"),
    "hr_ops": ("hrops@azentio.local", "hrops-dev-pass", STAFF_NAV[:1] + ["Add joiners"] + STAFF_NAV[1:], ["/dashboard", "/rules", "/import"], "/users"),
    "approver": ("approver@azentio.local", "approver-dev-pass", STAFF_NAV, ["/dashboard", "/approvals"], "/users"),
    "manager": ("manager@azentio.local", "manager-dev-pass", ["Dashboard", "Activity"], ["/dashboard", "/activity"], "/rules"),
    "auditor": ("auditor@azentio.local", "auditor-dev-pass", STAFF_NAV, ["/dashboard", "/logs"], "/users"),
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
    proc = subprocess.Popen([find_chrome(), "--headless=new", f"--remote-debugging-port={port}", f"--user-data-dir={profile}",
                             "--window-size=1440,900", "--hide-scrollbars", "--no-first-run", "about:blank"],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    failures = []
    try:
        for _ in range(50):
            try:
                tabs = json.load(urllib.request.urlopen(f"http://127.0.0.1:{port}/json"))
                ws_url = next(t["webSocketDebuggerUrl"] for t in tabs if t["type"] == "page")
                break
            except Exception:
                time.sleep(0.2)
        else:
            sys.exit("smoke: Chrome did not start")
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

            for role, (email, pw, nav, allowed, forbidden) in ROLES.items():
                print(f"[{role}]")
                await p.send("Network.clearBrowserCookies")
                await p.goto(f"{base}/login?next=/dashboard")
                # Fill the real form (React-controlled inputs need the native setter + an input event)
                await p.js(f"""(() => {{
                    const set = (el, v) => {{ Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value').set.call(el, v);
                                             el.dispatchEvent(new Event('input', {{ bubbles: true }})); }};
                    set(document.querySelector('#email'), {json.dumps(email)});
                    set(document.querySelector('#password'), {json.dumps(pw)});
                    return true; }})()""")
                await asyncio.sleep(0.2)
                await p.js("document.querySelector('form button[type=submit]').click()")
                expect(await p.wait_for("location.pathname === '/dashboard'"), f"{role}: login form signs in")
                await p.settle()

                got = await p.js("[...document.querySelectorAll('aside nav a')].map(a => a.innerText.trim())")
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
