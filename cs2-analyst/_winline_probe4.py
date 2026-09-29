import asyncio, re
LIST = "https://winline.ru/stavki/sport/kibersport/counter-strike"


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1500, "height": 3200},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        await pg.goto(LIST, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(6000)
        # events from NON-live cards
        evs = await pg.evaluate("""() => Array.from(document.querySelectorAll('ww-feature-block-event-dsk'))
          .map(c=>({live: !!c.querySelector('.card--live'),
                    href: (c.querySelector('a[href^=\"/stavki/event/\"]')||{}).getAttribute?.('href'),
                    txt:(c.innerText||'').split('\\n').filter(x=>x.trim()).slice(0,2).join(' vs ')}))
          .filter(e=>e.href)""")
        up = [e for e in evs if not e["live"]] or evs
        print("events:", len(evs), "upcoming:", len([e for e in evs if not e['live']]))
        e = up[0]
        url = "https://winline.ru" + e["href"]
        print("opening UPCOMING:", e["txt"], "->", url)
        await pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(6000)
        # find + click the tab bar entries (market categories)
        tabs = await pg.evaluate("""() => Array.from(document.querySelectorAll('[class*=tab],[role=tab],[class*=chip],[class*=filter] button, button'))
          .map(e=>(e.innerText||'').trim()).filter(t=>t && t.length<24)""")
        print("\n=== tabs/buttons ===", [t for t in dict.fromkeys(tabs)][:25])
        # click a 'карт'/'фора' tab if present
        clicked = await pg.evaluate("""() => { let n=0;
          document.querySelectorAll('[class*=tab],[role=tab],[class*=chip], button').forEach(e=>{
            if(/карт|фора/i.test((e.innerText||''))){ try{e.click(); n++}catch(_){}}}); return n; }""")
        await pg.wait_for_timeout(2500)
        for _ in range(6):
            await pg.mouse.wheel(0, 2200); await pg.wait_for_timeout(400)
        txt = await pg.evaluate("() => document.body.innerText")
        print("clicked map/fora tabs:", clicked, "| body len:", len(txt))
        # show all lines that look like a market label or contain карт/фора
        lines = [l.strip() for l in txt.split("\n") if l.strip()]
        keep = [l for l in lines if re.search(r"фора|карт|точн|хотя бы|тотал|исход|1x2|двойн", l, re.I) and len(l) < 60]
        print("\n=== market-label lines ===")
        for l in dict.fromkeys(keep)[:40]:
            print("  ", l)
        await b.close()

asyncio.run(main())
