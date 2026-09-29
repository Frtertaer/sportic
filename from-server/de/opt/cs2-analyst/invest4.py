import asyncio
async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(storage_state="/opt/cs2-analyst/data/winline_auth.json",
                                  locale="ru-RU", viewport={"width": 1500, "height": 1400})
        p = await ctx.new_page()
        await p.goto("https://winline.ru/auth/results", wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(8000)
        # results-page sport filter candidates (buttons/divs, NOT nav <a href>)
        filters = await p.evaluate("""() => {
            const cand = [];
            for (const el of document.querySelectorAll('button,div,span,li,[class*=filter],[class*=sport],[class*=menu]')) {
                const t = (el.innerText||'').trim();
                if (/киберспорт|counter|cs2|cs:go/i.test(t) && t.length < 30) {
                    cand.push({t, tag: el.tagName, cls: (el.className||'').slice(0,40), href: el.getAttribute('href')||''});
                }
            }
            const seen=new Set(); return cand.filter(c=>{const k=c.t+c.cls; if(seen.has(k))return false; seen.add(k); return true;}).slice(0,10);
        }""")
        print("CS-related filter candidates on results page:")
        for c in filters:
            print("   ", c)
        # scroll default view, dump raw score rows
        for _ in range(8):
            await p.evaluate("window.scrollBy(0,1400)"); await p.wait_for_timeout(400)
        rows = await p.evaluate("""() => {
            const out = [];
            for (const c of document.querySelectorAll('[class*=result],[class*=event],[class*=card],[class*=match],tr')) {
                const t = (c.innerText||'').replace(/\\n+/g,' | ').trim();
                if (/[0-3]\\s*:\\s*[0-3]/.test(t) && t.length>8 && t.length<160) out.push(t);
            }
            return [...new Set(out)].slice(0, 14);
        }""")
        print("\ndefault result rows (any sport):")
        for r in rows:
            print("   ", r[:150])
        await b.close()
asyncio.run(m())
