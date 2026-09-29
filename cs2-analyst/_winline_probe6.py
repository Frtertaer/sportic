import asyncio
LIST = "https://winline.ru/stavki/sport/kibersport/counter-strike"


async def main():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1500, "height": 3600},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        await pg.goto(LIST, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(6000)
        evs = await pg.evaluate("""() => Array.from(document.querySelectorAll('ww-feature-block-event-dsk'))
          .filter(c=>!c.querySelector('.card--live'))
          .map(c=>(c.querySelector('a[href^=\"/stavki/event/\"]')||{}).getAttribute?.('href')).filter(Boolean)""")
        url = "https://winline.ru" + evs[0]
        print("opening:", url)
        await pg.goto(url, wait_until="networkidle", timeout=60000)
        try:
            await pg.wait_for_function("() => (document.body.innerText.match(/\\d\\.\\d{2}/g)||[]).length>6", timeout=25000)
        except Exception:
            pass
        await pg.wait_for_timeout(2000)
        for _ in range(8):
            await pg.mouse.wheel(0, 2400); await pg.wait_for_timeout(400)
        # each market block: dump its title + its selection labels+odds
        data = await pg.evaluate(r"""() => {
          const out=[];
          // market blocks are Angular components; try several container guesses
          const blocks = document.querySelectorAll('ww-market, [class*=market], [class*=Market], [class*=outcome-group], [class*=group]');
          const seen=new Set();
          blocks.forEach(blk=>{
            const title=(blk.querySelector('[class*=title],[class*=name],[class*=header],h3,h4,span')||{}).innerText;
            const t=(title||'').trim();
            if(!t || t.length>50 || seen.has(t)) return;
            const odds=Array.from(blk.querySelectorAll('*')).map(e=>(e.innerText||'').trim())
               .filter(x=>/^\d{1,2}\.\d{2}$/.test(x)).slice(0,6);
            const labs=Array.from(blk.querySelectorAll('*')).map(e=>(e.innerText||'').trim())
               .filter(x=>x && x.length<18 && /карт|фор|\+1\.5|\-1\.5|0\.5|2:0|2:1|тотал|больше|меньше|хотя/i.test(x)).slice(0,6);
            if(/карт|фор|точн|тотал|хотя|исход|победит|тайм/i.test(t) || labs.length){
              seen.add(t); out.push({title:t, labs, odds});
            }
          });
          return out.slice(0,30);
        }""")
        print("=== market blocks (title | labels | odds) ===")
        for d in data:
            print(f"  [{d['title']}] labs={d['labs']} odds={d['odds']}")
        await b.close()

asyncio.run(main())
