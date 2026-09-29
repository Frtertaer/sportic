import asyncio, json


async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = await b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            locale="en-US", viewport={"width": 1366, "height": 900})
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        p = await ctx.new_page()
        url = "https://api.sofascore.com/api/v1/sport/football/events/live"
        r = await p.goto(url, wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(9000)   # let managed challenge auto-clear
        body = await p.evaluate("() => document.body ? document.body.innerText : ''")
        try:
            data = json.loads(body)
            ev = data.get("events", [])
            inplay = [e for e in ev if e.get("status", {}).get("type") == "inprogress"]
            print(f"OK! LIVE football: {len(ev)} | in-progress: {len(inplay)}")
            for e in inplay[:4]:
                print(f"  {e['homeTeam']['name']} {e.get('homeScore',{}).get('current','?')}:"
                      f"{e.get('awayScore',{}).get('current','?')} {e['awayTeam']['name']} id {e['id']}")
            tgt = inplay[0] if inplay else (ev[0] if ev else None)
            if tgt:
                eid = tgt["id"]
                r2 = await p.goto(f"https://api.sofascore.com/api/v1/event/{eid}/statistics",
                                  wait_until="domcontentloaded", timeout=45000)
                await p.wait_for_timeout(3000)
                sbody = await p.evaluate("() => document.body ? document.body.innerText : ''")
                st = json.loads(sbody)
                print(f"\n=== live-статы матча {eid} ===")
                for period in st.get("statistics", []):
                    if period.get("period") != "ALL":
                        continue
                    for g in period.get("groups", []):
                        for it in g.get("statisticsItems", []):
                            print(f"  {it['name']}: {it.get('home')} - {it.get('away')}")
        except Exception as ex:  # noqa: BLE001
            print(f"still blocked: HTTP {r.status if r else '?'} | {type(ex).__name__} | body[:160]: {body[:160]}")
        await b.close()


asyncio.run(m())
