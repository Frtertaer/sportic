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
        await pg.wait_for_timeout(6000)
        evs = await pg.evaluate("""() => Array.from(document.querySelectorAll('ww-feature-block-event-dsk'))
          .filter(c=>!c.querySelector('.card--live'))
          .map(c=>(c.querySelector('a[href^=\"/stavki/event/\"]')||{}).getAttribute?.('href')).filter(Boolean)""")
        url = "https://winline.ru" + evs[0]
        print("URL:", url)
        await pg.goto(url, wait_until="networkidle", timeout=60000)
        try:
            await pg.wait_for_function("() => (document.body.innerText.match(/\\d\\.\\d{2}/g)||[]).length>6", timeout=25000)
        except Exception:
            pass
        await pg.wait_for_timeout(2000)
        for _ in range(10):
            await pg.mouse.wheel(0, 2200); await pg.wait_for_timeout(400)
        # try the market container by the Angular tag we DID see (ww-*)
        tags = await pg.evaluate("""() => {
          const s=new Set();
          document.querySelectorAll('*').forEach(e=>{const n=e.tagName.toLowerCase(); if(n.startsWith('ww-')) s.add(n);});
          return Array.from(s);
        }""")
        print("ww-* tags:", tags[:40])
        full = await pg.evaluate("() => document.body.innerText")
        print("=== FULL innerText (chars={}) ===".format(len(full)))
        print(full[:3500])
        await b.close()

asyncio.run(main())
