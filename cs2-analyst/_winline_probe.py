"""Probe a winline CS2 MATCH page to locate the map-handicap / 'win >=1 map' market DOM."""
import asyncio, re

LIST = "https://winline.ru/stavki/sport/kibersport/counter-strike"


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1400, "height": 2000},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        await pg.goto(LIST, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(6000)
        # collect match hrefs
        hrefs = await pg.evaluate(
            "() => Array.from(document.querySelectorAll('a[href*=\"/counter-strike/\"]'))"
            ".map(a=>a.getAttribute('href')).filter(h=>h && h.split('/').length>6)")
        hrefs = list(dict.fromkeys(hrefs))
        print("match hrefs found:", len(hrefs))
        for h in hrefs[:3]:
            print("  ", h)
        if not hrefs:
            await b.close(); return
        url = "https://winline.ru" + hrefs[0]
        print("\nopening match:", url)
        await pg.goto(url, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(7000)
        # dump market group headers + any 'карт'/'фора' section text
        groups = await pg.evaluate(
            "() => Array.from(document.querySelectorAll('*')).filter(e=>{"
            "const t=(e.innerText||'').trim();"
            "return t && t.length<60 && /фора|карт|тотал|хотя бы|точный счёт|исход/i.test(t) && e.children.length<=2;"
            "}).map(e=>e.innerText.trim()).slice(0,40)")
        print("\n=== market-ish labels on the page ===")
        for g in dict.fromkeys(groups):
            print("  ", g)
        # full text around 'карт'
        txt = await pg.evaluate("() => document.body.innerText")
        for m in re.finditer(r".{0,40}(фора|хотя бы|тотал карт|точный счёт).{0,120}", txt, re.I):
            print("CTX:", m.group(0).replace("\n", " | ")[:180])
        await b.close()

asyncio.run(main())
