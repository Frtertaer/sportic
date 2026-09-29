import asyncio, re, sys
LIST = "https://winline.ru/stavki/sport/kibersport/counter-strike"


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1400, "height": 3000},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        await pg.goto(LIST, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(6000)
        ev = await pg.evaluate("() => Array.from(document.querySelectorAll('a[href^=\"/stavki/event/\"]')).map(a=>a.getAttribute('href'))")
        ev = list(dict.fromkeys(ev))
        print("events:", len(ev))
        if not ev:
            await b.close(); return
        url = "https://winline.ru" + ev[0]
        print("opening:", url)
        await pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(7000)
        # scroll to load all market groups
        for _ in range(8):
            await pg.mouse.wheel(0, 2500)
            await pg.wait_for_timeout(600)
        # click any collapsed group headers containing 'карт'/'фора'
        await pg.evaluate("""() => {
          document.querySelectorAll('*').forEach(e=>{
            const t=(e.innerText||'').trim().toLowerCase();
            if(t && t.length<40 && /карт|фора|точный|хотя бы|тотал/.test(t)){ try{e.click()}catch(_){} }
          });
        }""")
        await pg.wait_for_timeout(2000)
        # dump market group titles
        titles = await pg.evaluate("""() => Array.from(document.querySelectorAll('[class*=title],[class*=header],[class*=name],[class*=group],h2,h3'))
          .map(e=>(e.innerText||'').trim()).filter(t=>t && t.length<50 && /карт|фора|точный|хотя бы|тотал|исход|победит/i.test(t))""")
        print("\n=== map-related market titles ===")
        for t in dict.fromkeys(titles):
            print("  ", t)
        txt = await pg.evaluate("() => document.body.innerText")
        print("\n=== context around key markets ===")
        for kw in ["фора", "точный счёт", "хотя бы", "тотал карт", "индивидуальн"]:
            for m in re.finditer(r".{0,10}" + kw + r".{0,160}", txt, re.I):
                print(f"[{kw}]", m.group(0).replace("\n", " | ")[:190]); break
        await b.close()

asyncio.run(main())
