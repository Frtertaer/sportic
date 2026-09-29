import asyncio


async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"), locale="en-US")
        p = await ctx.new_page()
        await p.goto("https://www.sofascore.com/", wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(7000)   # let Cloudflare clear
        live = await p.evaluate("""async () => {
            try {
                const r = await fetch('https://api.sofascore.com/api/v1/sport/football/events/live',
                                      {headers:{'Accept':'application/json'}});
                return {ok:r.status, data: await r.json()};
            } catch(e){ return {err: String(e)}; }
        }""")
        if "data" not in live:
            print("live fetch fail:", live); await b.close(); return
        ev = live["data"].get("events", [])
        inplay = [e for e in ev if e.get("status", {}).get("type") == "inprogress"]
        print(f"HTTP {live['ok']} | LIVE football: {len(ev)} | in-progress: {len(inplay)}")
        for e in inplay[:5]:
            print(f"  {e['homeTeam']['name']} {e.get('homeScore',{}).get('current','?')}:"
                  f"{e.get('awayScore',{}).get('current','?')} {e['awayTeam']['name']} "
                  f"| id {e['id']} | {e.get('tournament',{}).get('name','?')}")
        target = inplay[0] if inplay else (ev[0] if ev else None)
        if target:
            eid = target["id"]
            print(f"\n=== live-статы матча id {eid} ===")
            stats = await p.evaluate("""async (eid) => {
                const r = await fetch('https://api.sofascore.com/api/v1/event/'+eid+'/statistics',
                                      {headers:{'Accept':'application/json'}});
                return {ok:r.status, data: (r.status===200 ? await r.json() : null)};
            }""", eid)
            if stats.get("data"):
                for period in stats["data"].get("statistics", []):
                    if period.get("period") != "ALL":
                        continue
                    for g in period.get("groups", []):
                        for it in g.get("statisticsItems", []):
                            print(f"  {it['name']}: {it.get('home')} - {it.get('away')}")
            else:
                print("  stats HTTP:", stats.get("ok"), "(нет ALL-статы, матч может быть только начался)")
        await b.close()


asyncio.run(m())
