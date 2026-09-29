#!/usr/bin/env python3
"""Load the captured winline_auth.json and check whether it's a LOGGED-IN session."""
import asyncio
from pathlib import Path

HERE = Path(__file__).resolve().parent


async def main() -> None:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True)
        ctx = await b.new_context(storage_state=str(HERE / "winline_auth.json"), locale="ru-RU")
        page = await ctx.new_page()
        await page.goto("https://winline.ru/", wait_until="domcontentloaded")
        await page.wait_for_timeout(7000)
        info = await page.evaluate("""() => {
            const txt = document.body ? document.body.innerText : '';
            const btns = [...document.querySelectorAll('a,button,[role=button]')].map(b => (b.innerText||'').trim());
            return {
                hasVoyti: btns.some(t => t === 'Войти' || t === 'Вход' || t === 'Войти в кабинет'),
                balanceHits: (txt.match(/[0-9][0-9\\s.,]*\\s*(?:₽|руб)/gi) || []).slice(0, 4),
                accountWords: /выход|мой профиль|кабинет|мои ставки|пополнить|вывести/i.test(txt)
            };
        }""")
        logged = (not info["hasVoyti"]) and (info["accountWords"] or bool(info["balanceHits"]))
        print("LOGGED_IN =", logged)
        print("  hasVoytiButton :", info["hasVoyti"])
        print("  balanceHits    :", info["balanceHits"])
        print("  accountWords   :", info["accountWords"])
        await b.close()


if __name__ == "__main__":
    asyncio.run(main())
