#!/usr/bin/env python3
"""Scrape winline's MAP-HANDICAP market ("Фора карт") for CS2 matches -> the кол on
"{team} +1.5 карт" = the team wins >=1 map (not swept 0-2). The event-detail page only
renders via SPA click-through (a direct goto shows the frontpage), so we open the CS2 list,
click each target event card, read the 'Фора карт' block, then go back.

Public API: scrape_map_handicap(pairs) -> {(t1,t2): {"team_a": name, "+1.5": odd, "-1.5": odd,
                                                      "team_b": name, ...}}  (best-effort)."""
from __future__ import annotations
import asyncio, re
from pathlib import Path

LIST = "https://winline.ru/stavki/sport/kibersport/counter-strike"


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").upper() if c.isalnum())


def _match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b and (a in b or b in a or (len(a) >= 4 and a[:5] == b[:5])))


def _parse_fora_kart(text: str) -> dict | None:
    """From event innerText, extract {team: {'+1.5': odd, '-1.5': odd}} of the 'Фора карт' block."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    try:
        i = next(k for k, l in enumerate(lines) if re.fullmatch(r"Фора карт", l, re.I))
    except StopIteration:
        return None
    block = lines[i + 1:i + 14]
    out: dict[str, dict] = {}
    cur = None
    cur_team = None
    for l in block:
        if re.fullmatch(r"[+\-−]1[.,]5", l):
            sign = "+1.5" if l[0] == "+" else "-1.5"
            cur = ("sign", sign)
        elif re.fullmatch(r"\d{1,2}[.,]\d{2}", l):
            if cur and cur[0] == "sign" and cur_team:
                out.setdefault(cur_team, {})[cur[1]] = float(l.replace(",", "."))
                cur = None
        elif len(l) < 30 and re.search(r"[A-Za-zА-Яа-я0-9]", l) and not re.search(r"карт|тотал|фора|матч|больше|меньше", l, re.I):
            cur_team = l
    return out or None


async def _scrape(pairs: list[tuple[str, str]]) -> dict:
    from playwright.async_api import async_playwright
    res: dict = {}
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1500, "height": 3200},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        await pg.goto(LIST, wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(7000)
        cards = await pg.evaluate("""() => Array.from(document.querySelectorAll('ww-feature-block-event-dsk')).map(c=>({
            txt:(c.innerText||'').split('\\n').filter(x=>x.trim()).slice(0,2),
            href:(c.querySelector('a[href^=\"/stavki/event/\"]')||{}).getAttribute?.('href')})).filter(c=>c.href)""")
        for (t1, t2) in pairs:
            card = next((c for c in cards if len(c["txt"]) >= 2 and
                         ((_match(t1, c["txt"][0]) and _match(t2, c["txt"][1])) or
                          (_match(t1, c["txt"][1]) and _match(t2, c["txt"][0])))), None)
            if not card:
                continue
            try:
                link = await pg.query_selector(f"a[href='{card['href']}']")
                if not link:
                    continue
                await link.click()
                await pg.wait_for_timeout(6000)
                try:
                    await pg.wait_for_function(
                        "() => /Фора карт/i.test(document.body.innerText)", timeout=12000)
                except Exception:  # noqa: BLE001
                    pass
                await pg.mouse.wheel(0, 4000); await pg.wait_for_timeout(1200)
                txt = await pg.evaluate("() => document.body.innerText")
                fk = _parse_fora_kart(txt)
                if fk:
                    res[(t1, t2)] = fk
            except Exception:  # noqa: BLE001
                pass
            finally:
                try:
                    await pg.goto(LIST, wait_until="domcontentloaded", timeout=45000)
                    await pg.wait_for_timeout(4000)
                except Exception:  # noqa: BLE001
                    pass
        await b.close()
    return res


def scrape_map_handicap(pairs: list[tuple[str, str]]) -> dict:
    if not pairs:
        return {}
    try:
        return asyncio.run(_scrape(pairs))
    except Exception:  # noqa: BLE001
        return {}


if __name__ == "__main__":
    import sys, json
    ps = [tuple(a.split("|")) for a in sys.argv[1:]] or [("ENJOY", "ENDLESS JOURNEY")]
    r = scrape_map_handicap(ps)
    print(json.dumps({f"{k[0]} vs {k[1]}": v for k, v in r.items()}, ensure_ascii=False, indent=2))
