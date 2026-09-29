import asyncio
async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(storage_state="/opt/cs2-analyst/data/winline_auth.json",
                                  locale="ru-RU", viewport={"width": 1500, "height": 1400})
        p = await ctx.new_page()
        await p.goto("https://winline.ru/auth/results", wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(8000)
        clicked = await p.evaluate("""() => {
            const el = [...document.querySelectorAll('a,button,span,div,[class*=tab]')]
                .find(x => (x.innerText||'').trim() === 'Киберспорт');
            if (el) { el.click(); return true; } return false;
        }""")
        await p.wait_for_timeout(5000)
        info = await p.evaluate("""() => {
            const txt = document.body.innerText||'';
            // score-bearing elements + their classes
            const scoreRe = /^[0-3]\\s*:\\s*[0-3]$/;
            const withScore = [];
            for (const el of document.querySelectorAll('*')) {
                const t = (el.innerText||'').trim();
                if (scoreRe.test(t) && el.children.length===0) {
                    let up = el; const path=[];
                    for (let i=0;i<4 && up;i++){ path.push(up.className||up.tagName); up=up.parentElement; }
                    withScore.push(path.join(' < '));
                }
            }
            // sample a result container innerText
            const conts = [...document.querySelectorAll('[class*=result],[class*=event],[class*=card],[class*=match]')]
                .map(c=>(c.innerText||'').replace(/\\n+/g,' | ').trim()).filter(t=>t.length>5 && t.length<160).slice(0,8);
            return {url: location.href, hasCS: /Counter-Strike|CCT|United21|Bo3/i.test(txt),
                    curSport: (txt.match(/Результаты[\\s\\S]{0,60}/)||[''])[0].replace(/\\n+/g,' '),
                    nScores: withScore.length, scorePaths: [...new Set(withScore)].slice(0,4), conts};
        }""")
        print("clicked Киберспорт:", clicked, "| url:", info["url"])
        print("hasCS content:", info["hasCS"])
        print("near 'Результаты':", info["curSport"][:120])
        print("score elements:", info["nScores"])
        print("score DOM paths:", info["scorePaths"])
        print("container samples:")
        for c in info["conts"]:
            print("   ", c[:150])
        await b.close()
asyncio.run(m())
