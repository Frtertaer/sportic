import asyncio
LIST = "https://winline.ru/stavki/sport/kibersport/counter-strike"


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1500, "height": 3600},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        await pg.goto(LIST, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(7000)
        # click a NON-live CS2 event card link (SPA navigation)
        link = await pg.query_selector("ww-feature-block-event-dsk:not(:has(.card--live)) a[href^='/stavki/event/']")
        if not link:
            link = await pg.query_selector("a[href^='/stavki/event/']")
        href = await link.get_attribute("href")
        print("clicking:", href)
        await link.click()
        await pg.wait_for_timeout(9000)
        for _ in range(10):
            await pg.mouse.wheel(0, 2200); await pg.wait_for_timeout(400)
        # dump market titles: winline market rows are in ww-* detail components
        info = await pg.evaluate(r"""() => {
          const txt=document.body.innerText;
          const wwtags=[...new Set([...document.querySelectorAll('*')].map(e=>e.tagName.toLowerCase()).filter(n=>n.startsWith('ww-')&&/market|outcome|event-detail|bet|line|factor/.test(n)))];
          // grab lines that are a market title (letters, has карт/фора/точн/тотал) OR an outcome +1.5/-1.5 with next odd
          const lines=txt.split('\n').map(x=>x.trim()).filter(Boolean);
          const idx=[];
          lines.forEach((l,i)=>{ if(/фора по карт|точный счёт|тотал карт|карт|хотя бы|индивидуальн/i.test(l) && l.length<45) idx.push(i); });
          const ctx=idx.slice(0,8).map(i=>lines.slice(i,i+8).join(' | '));
          return {len:txt.length, oddcount:(txt.match(/\d\.\d{2}/g)||[]).length, wwtags, ctx};
        }""")
        print("body len:", info["len"], "odds:", info["oddcount"])
        print("market ww-tags:", info["wwtags"])
        print("=== map-market context ===")
        for c in info["ctx"]:
            print("  ", c[:200])
        await b.close()

asyncio.run(main())
