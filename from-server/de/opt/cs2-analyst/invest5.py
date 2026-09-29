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
        await p.evaluate("""() => {
            const el = [...document.querySelectorAll('[class*=sport-menu__list-item]')].find(x => /Киберспорт/i.test(x.innerText||''));
            if (el) (el.querySelector('a,[class*=link]') || el).click();
        }""")
        await p.wait_for_timeout(6000)
        for _ in range(6):
            await p.evaluate("window.scrollBy(0,1200)"); await p.wait_for_timeout(400)
        d = await p.evaluate("""() => {
            const main = document.querySelector('main,[class*=main__wrapper],[class*=content]') || document.body;
            const txt = (main.innerText||'');
            const csIdx = txt.search(/Counter-Strike|CS2|CCT|United21/i);
            // classes of elements holding a score
            const paths = [];
            for (const el of document.querySelectorAll('*')) {
                if (el.children.length===0 && /^[0-3]\\s*:\\s*[0-3]$/.test((el.innerText||'').trim())) {
                    paths.push((el.parentElement?.className||'')+' / '+(el.parentElement?.parentElement?.className||''));
                }
            }
            return {hasCS: csIdx>=0, around: csIdx>=0 ? txt.slice(csIdx, csIdx+400).replace(/\\n/g,'|') : txt.slice(0,400).replace(/\\n/g,'|'),
                    scorePaths: [...new Set(paths)].slice(0,3), nScore: paths.length};
        }""")
        print("has CS content:", d["hasCS"], "| score elems:", d["nScore"])
        print("score parent classes:", d["scorePaths"])
        print("text around CS (| = newline):")
        print(d["around"])
        await b.close()
asyncio.run(m())
