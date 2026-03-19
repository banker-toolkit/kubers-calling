"""
fetch_token.py  —  Kubers IndMoney JWT fetcher
Opens Chrome, waits for Google/OTP login, captures token automatically.

Usage:  python fetch_token.py
First time:  pip install playwright && playwright install chromium
"""
import asyncio, json, re, sys, base64
from datetime import datetime
from pathlib import Path

ENGINE_DIR = Path(r"C:\Kubers\engine")
CREDS_FILE = ENGINE_DIR / "investright_creds.json"
LOGIN_URL  = "https://indmoney.com/signin"
API_HOST   = "api.indstocks.com"
TIMEOUT    = 120   # seconds to wait for login

def _is_jwt(s):
    p = s.split(".")
    return len(p) == 3 and all(len(x) > 10 for x in p)

def _expiry(token):
    try:
        payload = token.split(".")[1] + "=="
        data = json.loads(base64.b64decode(payload))
        exp  = data.get("exp", 0)
        return datetime.fromtimestamp(exp).strftime("%H:%M on %d-%b") if exp else "unknown"
    except Exception:
        return "unknown"

async def _fetch():
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("Run:  pip install playwright && playwright install chromium")
        sys.exit(1)

    captured = None

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=False)
        page    = await (await browser.new_context()).new_page()

        print("\n" + "="*55)
        print("  Chrome opening — complete Google login + OTP")
        print(f"  Token captured automatically. {TIMEOUT}s timeout.")
        print("="*55 + "\n")

        async def on_req(req):
            nonlocal captured
            if captured: return
            auth = req.headers.get("authorization", "")
            if _is_jwt(auth): captured = auth
            elif auth.startswith("Bearer ") and _is_jwt(auth[7:]): captured = auth[7:]

        async def on_resp(resp):
            nonlocal captured
            if captured or API_HOST not in resp.url: return
            try:
                body = await resp.text()
                for m in re.findall(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+", body):
                    if _is_jwt(m) and len(m) > 100:
                        captured = m; return
            except Exception: pass

        page.on("request",  on_req)
        page.on("response", on_resp)
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")

        waited = 0
        while not captured and waited < TIMEOUT:
            await asyncio.sleep(1); waited += 1
            if waited % 20 == 0: print(f"  Waiting... {waited}s / {TIMEOUT}s")

        await browser.close()

    if not captured:
        print("\n  No token captured. Did the browser login complete?\n")
        sys.exit(1)

    data = {}
    if CREDS_FILE.exists():
        try: data = json.loads(CREDS_FILE.read_text())
        except Exception: pass
    data["jwt_token"] = captured
    CREDS_FILE.write_text(json.dumps(data, indent=2))

    print(f"\n  ✓ Token saved → {CREDS_FILE}")
    print(f"  ✓ Valid until: {_expiry(captured)}")
    print(f"  ✓ Ready. Run: python kubers_calling.py\n")

if __name__ == "__main__":
    asyncio.run(_fetch())
