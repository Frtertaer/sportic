import asyncio
LIST = "https://winline.ru/stavki/sport/kibersport/counter-strike"


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1400, "height": 2400},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        await pg.goto(LIST, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(7000)
        allh = await pg.evaluate("() => Array.from(document.querySelectorAll('a')).map(a=>a.getAttribute('href')).filter(Boolean)")
        cs = [h for h in allh if h and ("counter" in h or "kibersport" in h or "/event" in h)]
        print("total anchors:", len(allh), "| cs/kibersport anchors:", len(cs))
        for h in list(dict.fromkeys(cs))[:12]:
            print("  ", h)
        # card structure: how many match cards + a sample
        info = await pg.evaluate("""() => {
          const cards = document.querySelectorAll('.card__body, [class*="card__body"], [class*="event"]');
          const sample = cards.length ? cards[0].outerHTML.slice(0,600) : 'NO CARDS';
          // find any element whose click navigates (data-href / role=link)
          const dh = Array.from(document.querySelectorAll('[data-href],[href],[class*=card]')).slice(0,5)
             .map(e=>({tag:e.tagName, cls:(e.className||'').toString().slice(0,40), dh:e.getAttribute('data-href'), href:e.getAttribute('href')}));
          return {ncards: cards.length, sample, dh};
        }""")
        print("\ncards:", info["ncards"])
        print("clickable samples:", info["dh"])
        print("\ncard sample HTML:\n", info["sample"])
        await b.close()

asyncio.run(main())
