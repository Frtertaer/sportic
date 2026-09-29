import asyncio
async def m():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(locale="ru-RU", viewport={"width": 1400, "height": 1400},
                                  user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        p = await ctx.new_page()
        await p.goto("https://g-cs.ru/matches", wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(6000)
        for _ in range(6):
            await p.evaluate("window.scrollBy(0,1400)"); await p.wait_for_timeout(400)
        info = await p.evaluate(r"""() => {
            // find elements whose text is exactly a score N:N (leaf), report their ancestor structure
            const leaves = [];
            for (const el of document.querySelectorAll('*')) {
                if (el.children.length===0 && /^\s*[0-3]\s*:\s*[0-3]\s*$/.test(el.innerText||'')) {
                    let up=el, cls=[];
                    for (let i=0;i<3 && up;i++){ cls.push((up.className||up.tagName).toString().slice(0,30)); up=up.parentElement; }
                    // the match container ~2-3 up: dump its text
                    let cont=el; for(let i=0;i<4;i++) if(cont.parentElement) cont=cont.parentElement;
                    leaves.push({cls: cls.join(' < '), cont: (cont.innerText||'').replace(/\n+/g,' | ').trim().slice(0,140)});
                }
            }
            const seen=new Set(); const uniq=leaves.filter(x=>{if(seen.has(x.cont))return false;seen.add(x.cont);return true;});
            return {title: document.title.slice(0,60), nLeaves: leaves.length, samples: uniq.slice(0,8)};
        }""")
        print("title:", info["title"])
        print("score leaves:", info["nLeaves"])
        for s in info["samples"]:
            print("  cls:", s["cls"])
            print("    cont:", s["cont"])
        await b.close()
asyncio.run(m())
