import asyncio


async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, proxy={"server": "http://127.0.0.1:10001"},
                                     args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = await b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"), locale="en-US")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        p = await ctx.new_page()
        feeds = []
        p.on("request", lambda r: feeds.append(r.url) if ("feed" in r.url or "flashscore.ninja" in r.url or "/x/" in r.url) else None)
        r = await p.goto("https://www.flashscore.com/football/", wait_until="domcontentloaded", timeout=60000)
        await p.wait_for_timeout(9000)
        info = await p.evaluate(r"""() => {
            const live = document.querySelectorAll('[class*="event__match--live"], .event__match--live');
            const anyMatch = document.querySelectorAll('[class*="event__match"]');
            return {title: document.title.slice(0,60), live: live.length, matches: anyMatch.length,
                    bodyLen: document.body.innerText.length, head: document.body.innerText.slice(0,140)};
        }""")
        print(f"HTTP {r.status if r else '?'} | title: {info['title']}")
        print(f"live matches: {info['live']} | total match rows: {info['matches']} | bodyLen {info['bodyLen']}")
        print(f"head: {info['head'][:120]}")
        print("=== data-feed requests seen (top 6) ===")
        for u in list(dict.fromkeys(feeds))[:6]:
            print("  ", u[:110])
        await b.close()


asyncio.run(m())
