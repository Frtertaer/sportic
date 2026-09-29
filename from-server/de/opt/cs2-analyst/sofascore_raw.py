import asyncio


async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = await b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"), locale="en-US")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        p = await ctx.new_page()
        for url in ("https://api.sofascore.com/api/v1/sport/football/events/live",
                    "https://www.sofascore.com/api/v1/sport/football/events/live"):
            r = await p.goto(url, wait_until="domcontentloaded", timeout=45000)
            await p.wait_for_timeout(6000)
            body = await p.evaluate("() => document.body ? document.body.innerText.slice(0,500) : ''")
            title = await p.title()
            print(f"URL: {url}")
            print(f"  HTTP {r.status if r else '?'} | title: {title[:60]}")
            print(f"  body[:400]: {body[:400]!r}")
            print()
        # also try the www-origin fetch after visiting www home (same-origin XHR w/ proper headers)
        await p.goto("https://www.sofascore.com/football/livescore", wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(8000)
        got = await p.evaluate("""async () => {
            try {
                const r = await fetch('/api/v1/sport/football/events/live', {headers:{'accept':'*/*'}});
                const t = await r.text();
                return {status:r.status, len:t.length, head:t.slice(0,300)};
            } catch(e){ return {err:String(e)}; }
        }""")
        print("same-origin /api fetch from www livescore page:", got)
        await b.close()


asyncio.run(m())
