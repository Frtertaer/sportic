import asyncio
async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(storage_state="/opt/cs2-analyst/data/winline_auth.json",
                                  locale="ru-RU", viewport={"width": 1500, "height": 1100})
        p = await ctx.new_page()
        for url in ["https://winline.ru/results/",
                    "https://winline.ru/stavki/results/",
                    "https://winline.ru/stavki/results/cyber/",
                    "https://winline.ru/stavki/sport/kibersport/counter-strike"]:
            try:
                await p.goto(url, wait_until="domcontentloaded", timeout=40000)
                await p.wait_for_timeout(6000)
                info = await p.evaluate("""() => {
                    const txt = (document.body.innerText||'').slice(0, 500);
                    const rlinks = [...document.querySelectorAll('a')].map(a=>a.getAttribute('href')||'')
                        .filter(h=>/result|итог|результат/i.test(h));
                    const uniq = [...new Set(rlinks)].slice(0, 8);
                    // any element that looks like a finished score "2:0" / "0-2"
                    const scores = (document.body.innerText.match(/\\b[0-2]\\s*[:\\-]\\s*[0-2]\\b/g)||[]).slice(0,10);
                    const tabs = [...document.querySelectorAll('a,button,[role=tab]')].map(x=>(x.innerText||'').trim())
                        .filter(t=>/результат|итог|заверш|finished/i.test(t)).slice(0,6);
                    return {txt, result_links: uniq, score_samples: scores, result_tabs: tabs};
                }""")
                print("URL:", url, "-> HTTP", p.url)
                print("  txt:", info["txt"].replace(chr(10), " | ")[:280])
                print("  result_links:", info["result_links"])
                print("  result_tabs :", info["result_tabs"])
                print("  score_samples:", info["score_samples"])
            except Exception as e:
                print("URL:", url, "FAIL", type(e).__name__, str(e)[:80])
            print()
        await b.close()
asyncio.run(m())
