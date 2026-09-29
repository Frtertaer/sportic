import asyncio
async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(storage_state="/opt/cs2-analyst/data/winline_auth.json",
                                  locale="ru-RU", viewport={"width": 1500, "height": 1400})
        p = await ctx.new_page()
        await p.goto("https://winline.ru/auth/results", wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(9000)
        # try to click a CS2 / Counter-Strike filter if present, scroll to load
        for _ in range(6):
            await p.evaluate("window.scrollBy(0, 1200)")
            await p.wait_for_timeout(500)
        info = await p.evaluate("""() => {
            const txt = (document.body.innerText||'');
            const cs = txt.includes('Counter-Strike') || txt.includes('CS2') || /кибер/i.test(txt);
            // look for finished match rows: team names + score pattern
            const scoreRe = /[0-2]\\s*[:\\-]\\s*[0-2]/;
            const cards = [...document.querySelectorAll('[class*=card],[class*=event],[class*=result],[class*=row]')]
                .map(c => (c.innerText||'').replace(/\\n+/g,' | ').trim())
                .filter(t => scoreRe.test(t) && t.length < 200).slice(0, 12);
            const sportTabs = [...document.querySelectorAll('a,button,[role=tab],[class*=tab]')]
                .map(x=>(x.innerText||'').trim()).filter(t=>t && t.length<30).slice(0,30);
            return {len: txt.length, has_cyber: cs, sample_cards: cards, tabs: [...new Set(sportTabs)].slice(0,25),
                    head: txt.slice(0, 300).replace(/\\n+/g,' | ')};
        }""")
        print("page len:", info["len"], "| has cyber:", info["has_cyber"])
        print("head:", info["head"])
        print("tabs:", info["tabs"])
        print("sample finished cards:")
        for c in info["sample_cards"]:
            print("   ", c[:150])
        await b.close()
asyncio.run(m())
