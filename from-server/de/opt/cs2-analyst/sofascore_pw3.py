import asyncio, json
from datetime import datetime, timezone


async def nav_json(p, url):
    await p.goto(url, wait_until="domcontentloaded", timeout=45000)
    await p.wait_for_timeout(2500)
    body = await p.evaluate("() => document.body ? document.body.innerText : ''")
    return json.loads(body)


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
        ev, fin = [], []
        for dt in ("2026-07-06", "2026-07-05", datetime.now(timezone.utc).strftime("%Y-%m-%d")):
            sched = await nav_json(p, f"https://api.sofascore.com/api/v1/sport/football/scheduled-events/{dt}")
            ev = sched.get("events", [])
            fin = [e for e in ev if e.get("status", {}).get("type") == "finished"]
            print(f"матчей за {dt}: {len(ev)} | завершённых: {len(fin)}")
            if fin:
                break
        # find one whose statistics endpoint has data
        for e in fin[:20]:
            eid = e["id"]
            try:
                st = await nav_json(p, f"https://api.sofascore.com/api/v1/event/{eid}/statistics")
            except Exception:
                continue
            items = []
            for period in st.get("statistics", []):
                if period.get("period") != "ALL":
                    continue
                for g in period.get("groups", []):
                    for it in g.get("statisticsItems", []):
                        items.append(it["name"])
            if items:
                print(f"\n=== СТАТЫ есть: {e['homeTeam']['name']} vs {e['awayTeam']['name']} (id {eid}) ===")
                print("Все доступные метрики:")
                for period in st.get("statistics", []):
                    if period.get("period") != "ALL":
                        continue
                    for g in period.get("groups", []):
                        print(f"  [{g.get('groupName')}]")
                        for it in g.get("statisticsItems", []):
                            print(f"     {it['name']}: {it.get('home')} - {it.get('away')}")
                break
        await b.close()


asyncio.run(m())
