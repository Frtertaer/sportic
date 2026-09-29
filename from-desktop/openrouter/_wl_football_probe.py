import asyncio
URLS = ["https://winline.ru/stavki/sport/futbol", "https://winline.ru/stavki/sport/futbol/"]


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1500, "height": 2600},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        for u in URLS:
            try:
                await pg.goto(u, wait_until="domcontentloaded", timeout=60000)
                await pg.wait_for_timeout(6500)
                cards = await pg.evaluate("""() => Array.from(document.querySelectorAll('ww-feature-block-event-dsk'))
                  .slice(0,5).map(c=>c.innerText.split('\\n').map(x=>x.trim()).filter(Boolean).slice(0,12))""")
                print("URL:", u, "cards:", len(cards))
                for c in cards[:4]:
                    print("  ", c)
                if cards:
                    break
            except Exception as ex:
                print("URL", u, "err", type(ex).__name__)
        await b.close()

asyncio.run(main())
