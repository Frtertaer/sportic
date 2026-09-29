#!/usr/bin/env python3
"""ONE-TIME winline login capture for the live-lag sniper bot.

Opens a REAL (headed) Chromium. You log into winline.ru by hand (phone + SMS + captcha — whatever
it asks), then press Enter here. It saves the authenticated session to winline_auth.json.
NO password is ever stored — only the resulting session cookies/localStorage.

Run on a machine WITH a screen:
    pip install playwright
    playwright install chromium
    python capture_winline_login.py

Then copy winline_auth.json to the server (chmod 600) next to livelag_bet.py:
    scp winline_auth.json root@100.116.150.111:/opt/cs2-analyst/data/winline_auth.json

IP/device caveat: winline may tie a live session to the IP it was created on. If bets from the
server get logged out, re-run this capture FROM the server (headed via VNC) so the IP matches.
"""
import asyncio
from pathlib import Path

OUT = Path(__file__).resolve().parent / "winline_auth.json"


async def main() -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = await b.new_context(locale="ru-RU", viewport=None)
        page = await ctx.new_page()
        await page.goto("https://winline.ru/", wait_until="domcontentloaded")
        print("\n" + "=" * 64)
        print("  1) Log into winline in the browser window (phone + SMS + captcha).")
        print("  2) Make sure you see your balance / account — you are logged IN.")
        print("  3) Come back here and press ENTER to save the session.")
        print("=" * 64)
        input("\n>>> Logged in? press ENTER to save session... ")
        await ctx.storage_state(path=str(OUT))
        print(f"\n✓ session saved -> {OUT}")
        print("  now: scp it to the server as /opt/cs2-analyst/data/winline_auth.json (chmod 600)")
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
