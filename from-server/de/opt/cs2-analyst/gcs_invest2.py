import asyncio
async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(locale="ru-RU", viewport={"width": 1400, "height": 1600},
                                  user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        p = await ctx.new_page()
        await p.goto("https://g-cs.ru/matches", wait_until="networkidle", timeout=45000)
        await p.wait_for_timeout(4000)
        for _ in range(5):
            await p.evaluate("window.scrollBy(0,1600)"); await p.wait_for_timeout(500)
        data = await p.evaluate(r"""() => {
            // nav links about results
            const links = [...document.querySelectorAll('a')].map(a=>({t:(a.innerText||'').trim(), h:a.getAttribute('href')||''}))
                .filter(x=>/результат|result|матч|match/i.test(x.t) || /result|match/i.test(x.h));
            const nav = [...new Map(links.map(x=>[x.h,x])).values()].slice(0,12);
            // match links: <a href*=/match or /matches/> with their text
            const mrows = [...document.querySelectorAll('a[href*=\"match\"], [class*=match], [class*=game], [class*=event]')]
                .map(c=>(c.innerText||'').replace(/\s+/g,' ').trim()).filter(t=>t.length>6 && t.length<120);
            const uniq = [...new Set(mrows)].slice(0, 14);
            return {nav, mrows: uniq, bodyLen: document.body.innerText.length};
        }""")
        print("nav/result links:")
        for x in data["nav"]:
            print("   ", repr(x["t"][:30]), "->", x["h"])
        print("\nmatch-row texts:")
        for r in data["mrows"]:
            print("   ", r[:110])
        await b.close()
asyncio.run(m())
