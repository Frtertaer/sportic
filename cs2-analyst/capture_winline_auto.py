#!/usr/bin/env python3
"""Auto-capture winline login session — NO console interaction needed.
Opens a real browser; you log into winline by hand (phone + SMS + captcha). It saves the session to
winline_auth.json every few seconds, so the moment you're logged in it's captured, and it detects
the login and reports DONE. Exits after 15 min or when a file named STOP appears next to this script.
No password is ever stored — only the resulting session cookies/localStorage."""
import asyncio
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "winline_auth.json"
STOP = HERE / "STOP"


async def main() -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=False, args=["--start-maximized"])
        ctx = await b.new_context(locale="ru-RU", viewport=None)
        page = await ctx.new_page()
        await page.goto("https://winline.ru/", wait_until="domcontentloaded")
        print("BROWSER_OPEN: log into winline now (phone + SMS + captcha).", flush=True)
        deadline = time.time() + 900
        hits = 0
        while time.time() < deadline and not STOP.exists():
            await asyncio.sleep(6)
            try:
                await ctx.storage_state(path=str(OUT))                 # continuous snapshot
                logged = await page.evaluate("""() => {
                    const txt = document.body ? document.body.innerText : '';
                    const btns = [...document.querySelectorAll('a,button,[role=button]')].map(b => (b.innerText||'').trim());
                    const hasLoginBtn = btns.some(t => t === 'Войти' || t === 'Вход' || t === 'Войти в кабинет');
                    const hasBalance = /\\d[\\d\\s.,]*\\s*(₽|руб)/i.test(txt);
                    return (!hasLoginBtn && hasBalance);
                }""")
                nck = len(await ctx.cookies())
                if logged:
                    hits += 1
                    print(f"LOGGED_IN detected ({hits}) — session saved, {nck} cookies", flush=True)
                    if hits >= 2:
                        await ctx.storage_state(path=str(OUT))
                        print("DONE: winline_auth.json is ready.", flush=True)
                        break
                else:
                    print(f"waiting for login... ({nck} cookies)", flush=True)
            except Exception as e:  # noqa: BLE001
                print("snap error:", type(e).__name__, e, flush=True)
        await ctx.storage_state(path=str(OUT))
        await b.close()
        print("CLOSED: final winline_auth.json written.", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
