import asyncio, re
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
        print("opening:", url)
        await pg.goto(url, wait_until="networkidle", timeout=60000)
        # wait for markets: any element with a decimal odd
        try:
            await pg.wait_for_function(
                "() => (document.body.innerText.match(/\\b\\d\\.\\d{2}\\b/g)||[]).length > 6", timeout=25000)
        except Exception as e:
            print("odds wait timeout:", type(e).__name__)
        await pg.wait_for_timeout(3000)
        for _ in range(6):
            await pg.mouse.wheel(0, 2500); await pg.wait_for_timeout(500)
        await pg.screenshot(path="/tmp/winline_event.png", full_page=False)
        txt = await pg.evaluate("() => document.body.innerText")
        print("body len:", len(txt), "| odds count:", len(re.findall(r"\b\d\.\d{2}\b", txt)))
        lines = [l.strip() for l in txt.split("\n") if l.strip()]
        keep = [l for l in lines if re.search(r"фора|карт|точн|хотя бы|тотал|исход|победит|команда", l, re.I) and len(l) < 70]
        print("=== market labels ===")
        for l in list(dict.fromkeys(keep))[:45]:
            print("  ", l)
        await b.close()

asyncio.run(main())
