import asyncio
async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(locale="ru-RU", viewport={"width": 1400, "height": 1600},
                                  user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        p = await ctx.new_page()
        await p.goto("https://g-cs.ru/matches/finished", wait_until="networkidle", timeout=45000)
        await p.wait_for_timeout(4000)
        for _ in range(6):
            await p.evaluate("window.scrollBy(0,1500)"); await p.wait_for_timeout(400)
        data = await p.evaluate(r"""() => {
            const rows = [];
            for (const a of document.querySelectorAll('a[href*="/matches/2"]')) {
                // per match: gather leaf texts in order
                const leaves = [];
                for (const el of a.querySelectorAll('*')) {
                    if (el.children.length===0) { const t=(el.innerText||'').trim(); if (t) leaves.push(t); }
                }
                if (leaves.length) rows.push({href: a.getAttribute('href'), leaves: leaves.slice(0,14)});
            }
            return {n: rows.length, rows: rows.slice(0,6)};
        }""")
        print("finished match links:", data["n"])
        for r in data["rows"]:
            print(" ", r["href"])
            print("    leaves:", r["leaves"])
        await b.close()
asyncio.run(m())
