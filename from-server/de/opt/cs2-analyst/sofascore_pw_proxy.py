import asyncio, json


async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=[
            "--no-sandbox", "--disable-blink-features=AutomationControlled"],
            proxy={"server": "http://127.0.0.1:10001"})   # DataImpulse residential forwarder
        ctx = await b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"), locale="en-US")
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        p = await ctx.new_page()
        r = await p.goto("https://api.sofascore.com/api/v1/sport/football/events/live",
                         wait_until="domcontentloaded", timeout=60000)
        await p.wait_for_timeout(12000)   # residential IP + browser solves JS challenge
        body = await p.evaluate("() => document.body ? document.body.innerText : ''")
        print("HTTP:", r.status if r else "?")
        print("body[:180]:", body[:180])
        try:
            data = json.loads(body)
            if "events" in data:
                ev = data["events"]
                ip = [e for e in ev if e.get("status", {}).get("type") == "inprogress"]
                print(f"\n✅ ПРОШЁЛ! live football: {len(ev)} | in-progress: {len(ip)}")
                tgt = ip[0] if ip else (ev[0] if ev else None)
                if tgt:
                    eid = tgt["id"]
                    await p.goto(f"https://api.sofascore.com/api/v1/event/{eid}/statistics",
                                 wait_until="domcontentloaded", timeout=60000)
                    await p.wait_for_timeout(3000)
                    st = json.loads(await p.evaluate("() => document.body.innerText"))
                    print(f"\n=== СТАТЫ матча {eid} ===")
                    for period in st.get("statistics", []):
                        if period.get("period") != "ALL":
                            continue
                        for g in period.get("groups", []):
                            for it in g.get("statisticsItems", []):
                                print(f"  {it['name']}: {it.get('home')} - {it.get('away')}")
            else:
                print("\n⚠️ всё ещё challenge/пусто")
        except Exception as ex:  # noqa: BLE001
            print("parse fail:", ex)
        await b.close()


asyncio.run(m())
