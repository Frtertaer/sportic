import asyncio, json


async def nav(p, url, tries=10):
    await p.goto(url, wait_until="domcontentloaded", timeout=60000)
    for _ in range(tries):
        await p.wait_for_timeout(1100)
        try:
            d = json.loads(await p.evaluate("() => document.body ? document.body.innerText : ''"))
            if isinstance(d, dict) and "error" not in d:
                return d
        except Exception:
            pass
    return None


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
        live = await nav(p, "https://api.sofascore.com/api/v1/sport/football/events/live")
        inplay = [e for e in live["events"] if e.get("status", {}).get("type") == "inprogress"]
        for e in inplay[:4]:
            eid = e["id"]
            g = await nav(p, f"https://api.sofascore.com/api/v1/event/{eid}/graph")
            print(f"\n=== {e['homeTeam']['name']} vs {e['awayTeam']['name']} (id {eid}) ===")
            if not g:
                print("  graph None"); continue
            pts = g.get("graphPoints") or g.get("graph") or []
            print(f"  graphPoints: {len(pts)}")
            if pts:
                # last 6 minutes of momentum (+ = home pressing, - = away)
                tail = pts[-8:]
                print("  последние точки (min:value):", [(x.get('minute'), x.get('value')) for x in tail])
                vals = [x.get("value", 0) for x in pts]
                print(f"  разброс value: min {min(vals)} max {max(vals)}")
        await b.close()


asyncio.run(m())
