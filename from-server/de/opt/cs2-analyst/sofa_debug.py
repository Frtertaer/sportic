import asyncio, json


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
        await p.goto("https://api.sofascore.com/api/v1/sport/football/events/live",
                     wait_until="domcontentloaded", timeout=60000)
        await p.wait_for_timeout(10000)
        live = json.loads(await p.evaluate("() => document.body.innerText"))
        inplay = [e for e in live["events"] if e.get("status", {}).get("type") == "inprogress"]
        print("in-progress:", len(inplay))
        # pick 3 bigger-club matches
        for e in inplay[:4]:
            eid = e["id"]
            r = await p.goto(f"https://api.sofascore.com/api/v1/event/{eid}/statistics",
                             wait_until="domcontentloaded", timeout=60000)
            await p.wait_for_timeout(2500)
            body = await p.evaluate("() => document.body.innerText")
            tag = "?"
            try:
                st = json.loads(body)
                if "error" in st:
                    tag = f"CHALLENGE {st['error']}"
                elif st.get("statistics"):
                    periods = [pp.get("period") for pp in st["statistics"]]
                    tag = f"HAS STATS, periods={periods}"
                else:
                    tag = f"EMPTY (keys={list(st.keys())})"
            except Exception:
                tag = f"NOT-JSON: {body[:80]}"
            print(f"  {e['homeTeam']['name']} vs {e['awayTeam']['name']} id {eid} HTTP {r.status} -> {tag}")
        await b.close()


asyncio.run(m())
