import asyncio
HOME = "https://winline.ru/stavki/"


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1400, "height": 2200},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        await pg.goto(HOME, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(6000)
        # find search input candidates
        inputs = await pg.evaluate("""() => Array.from(document.querySelectorAll('input')).map(i=>({
            type:i.type, ph:i.placeholder, name:i.name, cls:(i.className||'').slice(0,40)}))""")
        print("inputs:", inputs[:8])
        # try clicking a search icon then typing
        clicked = await pg.evaluate("""() => { let n=0;
          document.querySelectorAll('[class*=search],[class*=Search],[aria-label*=oиск],[aria-label*=earch],button,a').forEach(e=>{
            if(/поиск|search/i.test((e.getAttribute('aria-label')||'')+(e.className||''))){ try{e.click();n++}catch(_){}}}); return n;}""")
        await pg.wait_for_timeout(1500)
        print("clicked search-ish:", clicked)
        inp = await pg.query_selector("input[type=search], input[placeholder*='оиск'], input[placeholder*='earch']")
        if inp:
            await inp.click()
            await inp.type("Бразилия", delay=60)
            await pg.wait_for_timeout(3500)
            res = await pg.evaluate("""() => {
              const t=document.body.innerText;
              const i=t.toLowerCase().indexOf('бразил');
              return {url:location.href, near: i>=0? t.slice(Math.max(0,i-40), i+300): 'not in text',
                      anchors: Array.from(document.querySelectorAll('a[href*=\"/stavki/event/\"]')).slice(0,6).map(a=>a.getAttribute('href'))};
            }""")
            print("after search:", res["url"])
            print("near:", res["near"].replace("\\n"," | ")[:260])
            print("event anchors:", res["anchors"])
        else:
            print("no search input found; trying URL /search")
            await pg.goto("https://winline.ru/search?query=Бразилия", wait_until="domcontentloaded", timeout=45000)
            await pg.wait_for_timeout(4000)
            print("search url ->", pg.url, "| body has бразил:", "бразил" in (await pg.evaluate("()=>document.body.innerText")).lower())
        await b.close()

asyncio.run(main())
