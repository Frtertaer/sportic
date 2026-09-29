#!/usr/bin/env python3
"""Attach winline FOOTBALL 1X2 odds to the sportsbot's predictions via winline SEARCH (per match) —
the RU book the user bets on. Non-invasive: writes data/football_winline.json keyed by the bot's
English match, the website shows the кол + value. winline is RU, bot is EN -> we search each match by
the RU team name (national-team map + fallback). Run on a timer for the top upcoming predictions."""
from __future__ import annotations
import asyncio, json, re, sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "football_winline.json"

RU2EN = {
    "мексика": "Mexico", "англия": "England", "бразилия": "Brazil", "норвегия": "Norway",
    "парагвай": "Paraguay", "франция": "France", "канада": "Canada", "марокко": "Morocco",
    "аргентина": "Argentina", "испания": "Spain", "германия": "Germany", "португалия": "Portugal",
    "нидерланды": "Netherlands", "бельгия": "Belgium", "хорватия": "Croatia", "италия": "Italy",
    "уругвай": "Uruguay", "колумбия": "Colombia", "япония": "Japan", "южная корея": "South Korea",
    "сша": "USA", "сенегал": "Senegal", "гана": "Ghana", "нигерия": "Nigeria", "египет": "Egypt",
    "камерун": "Cameroon", "эквадор": "Ecuador", "перу": "Peru", "чили": "Chile", "дания": "Denmark",
    "швейцария": "Switzerland", "польша": "Poland", "сербия": "Serbia", "швеция": "Sweden",
    "австрия": "Austria", "уэльс": "Wales", "австралия": "Australia", "катар": "Qatar", "иран": "Iran",
    "тунис": "Tunisia", "алжир": "Algeria", "турция": "Turkey", "шотландия": "Scotland",
    "кабо-верде": "Cape Verde", "кот-д’ивуар": "Ivory Coast", "украина": "Ukraine", "чехия": "Czechia",
}
EN2RU = {v.lower(): k for k, v in RU2EN.items()}


def _ru(en: str) -> str:
    return EN2RU.get((en or "").strip().lower(), en)


def _norm(s: str) -> str:
    return "".join(ch for ch in (s or "").lower() if ch.isalnum())


async def _search_one(pg, en1: str, en2: str) -> dict | None:
    q = _ru(en1)
    try:
        inp = await pg.query_selector("input[placeholder*='оиск']")
        if not inp:
            return None
        await inp.click()
        await inp.fill("")
        await inp.type(q, delay=45)
        await pg.wait_for_timeout(2800)
        rows = await pg.evaluate(r"""() => {
          const out=[];
          document.querySelectorAll('a[href*="/stavki/event/"]').forEach(a=>{
            const blk=a.closest('[class*=card],[class*=event],li,tr,div');
            const txt=((blk&&blk.innerText)||a.innerText||'').split('\n').map(x=>x.trim()).filter(Boolean);
            if(txt.length) out.push(txt.slice(0,14));
          });
          return out.slice(0,25);
        }""")
    except Exception:  # noqa: BLE001
        return None
    dec = re.compile(r"^\d{1,3}[.,]\d{2}$")
    n1, n2 = _norm(en1), _norm(en2)
    r1, r2 = _norm(_ru(en1)), _norm(_ru(en2))
    for txt in rows:
        blob = _norm(" ".join(txt))
        okt1 = (n1 and n1 in blob) or (r1 and r1 in blob)
        okt2 = (n2 and n2 in blob) or (r2 and r2 in blob)
        if not (okt1 and okt2):
            continue
        odds = [float(x.replace(",", ".")) for x in txt if dec.match(x)]
        odds = [o for o in odds if 1.01 <= o <= 60]
        if len(odds) >= 3:
            o1, ox, o2 = odds[0], odds[1], odds[2]
            if 1.5 <= ox <= 15:
                return {"o1": o1, "ox": ox, "o2": o2}
    return None


async def _run(matches: list[tuple[str, str]]) -> dict:
    from playwright.async_api import async_playwright
    res = {}
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(viewport={"width": 1400, "height": 2200},
                                  user_agent="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36")
        pg = await ctx.new_page()
        await pg.goto("https://winline.ru/stavki/", wait_until="domcontentloaded", timeout=60000)
        await pg.wait_for_timeout(6000)
        for en1, en2 in matches:
            try:
                o = await _search_one(pg, en1, en2)
                if o:
                    res[f"{en1}|{en2}"] = o
            except Exception:  # noqa: BLE001
                pass
        await b.close()
    return res


def scrape_and_store(matches: list[tuple[str, str]]) -> dict:
    r = asyncio.run(_run(matches)) if matches else {}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps({"fetched_at": datetime.now(timezone.utc).isoformat(), "odds": r},
                              ensure_ascii=False, indent=1), encoding="utf-8")
    return r


def lookup(en1: str, en2: str) -> dict | None:
    try:
        data = json.loads(OUT.read_text(encoding="utf-8")).get("odds", {})
    except Exception:  # noqa: BLE001
        return None
    n1, n2 = _norm(en1), _norm(en2)
    for k, v in data.items():
        a, b = k.split("|")
        if _norm(a) == n1 and _norm(b) == n2:
            return v
        if _norm(a) == n2 and _norm(b) == n1:
            return {"o1": v["o2"], "ox": v["ox"], "o2": v["o1"]}
    return None


def _upcoming(limit: int = 12) -> list[tuple[str, str]]:
    import sqlite3
    c = sqlite3.connect("file:sportsbot.db?mode=ro", uri=True)
    return [(r[0], r[1]) for r in c.execute(
        "SELECT team1,team2 FROM predictions WHERE actual_result IS NULL ORDER BY id DESC LIMIT ?", (limit,))]


if __name__ == "__main__":
    ms = _upcoming(int(sys.argv[1]) if len(sys.argv) > 1 else 12)
    r = scrape_and_store(ms)
    print(f"searched {len(ms)} bot matches, found winline odds for {len(r)}:")
    for k, v in r.items():
        print(f"  {k.replace('|', ' vs ')}  ->  {v['o1']}/{v['ox']}/{v['o2']}")
