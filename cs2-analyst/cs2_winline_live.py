#!/usr/bin/env python3
"""Scrape winline.ru LIVE esports line → data/winline_live.json.

winline's /stavki/live/cyber/ renders in-play matches in the DOM: team names,
series score, current-map round score, current map number, and LIVE ML odds.
This is the reliable live-state + live-odds source (bo3's 'live' status is flaky and
doesn't cover every match). The live monitor reads this to push value vs the book in
real time. Run via the playwright venv on a short timer.
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "winline_live.json"
URL = "https://winline.ru/stavki/live/cyber/"

_DEC = re.compile(r"^\d{1,2}\.\d{2}$")
_MAPMARK = re.compile(r"^(\d+)\s*[КK]$")        # "2К" = current map 2
_NAME = re.compile(r"[A-Za-zА-Яа-я]")


def _parse_card(text: str) -> dict | None:
    toks = [t.strip() for t in text.split("\n") if t.strip()]
    names = [t for t in toks if _NAME.search(t) and not _MAPMARK.match(t)
             and not _DEC.match(t) and len(t) < 40]
    odds = [float(t) for t in toks if _DEC.match(t)]
    ints = [int(t) for t in toks if t.isdigit()]
    cur_map = next((int(m.group(1)) for t in toks if (m := _MAPMARK.match(t))), None)
    if len(names) < 2 or len(odds) < 2 or cur_map is None:
        return None  # not an esports live card (or unparseable)
    return {
        "t1": names[0], "t2": names[1],
        "series": ints[:2] if len(ints) >= 2 else None,
        "map_score": ints[2:4] if len(ints) >= 4 else None,
        "cur_map": cur_map,
        "odd1": odds[-2], "odd2": odds[-1],
    }


# the live list scrolls INSIDE a <main> container, not the window — so the page only
# renders the first few live cards until you scroll the container (old bug: caught just
# 1-2 of N live matches). Scroll the container + extract incrementally to get them all.
_EXTRACT = "() => Array.from(document.querySelectorAll('[class*=card__body-live]')).map(c => c.innerText)"
_SCROLL = r"""
() => {
  const cards = document.querySelectorAll('[class*=card__body-live]');
  if (cards.length) cards[cards.length - 1].scrollIntoView({ block: 'end' });
  const m = document.querySelector('main, .main__wrapper, [class*=main__wrapper]');
  if (m) m.scrollTop = m.scrollHeight;
  window.scrollBy(0, 900);
}
"""


async def scrape() -> list[dict]:
    from playwright.async_api import async_playwright
    acc: dict[tuple, dict] = {}
    now = datetime.now(timezone.utc).isoformat()
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            locale="ru-RU", viewport={"width": 1500, "height": 1100})
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(8000)
        for _ in range(30):
            for c in await page.evaluate(_EXTRACT):
                r = _parse_card(c)
                if r:
                    acc[(r["t1"].lower(), r["t2"].lower())] = r
            await page.evaluate(_SCROLL)
            await page.wait_for_timeout(400)
        for c in await page.evaluate(_EXTRACT):
            r = _parse_card(c)
            if r:
                acc[(r["t1"].lower(), r["t2"].lower())] = r
        await b.close()
    out = []
    for r in acc.values():
        r["fetched_at"] = now
        out.append(r)
    return out


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def lookup(team1: str, team2: str) -> dict | None:
    """Live odds + state for our (team1, team2), aligned to our order."""
    if not OUT.exists():
        return None
    try:
        rows = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    n1, n2 = _norm(team1), _norm(team2)

    def m(a, b):
        return bool(a and b and (a in b or b in a or (len(a) >= 4 and len(b) >= 4 and a[:5] == b[:5])))

    for r in rows:
        w1, w2 = _norm(r["t1"]), _norm(r["t2"])
        if m(n1, w1) and m(n2, w2):
            return {**r, "odd_t1": r["odd1"], "odd_t2": r["odd2"]}
        if m(n1, w2) and m(n2, w1):
            return {**r, "odd_t1": r["odd2"], "odd_t2": r["odd1"],
                    "series": (r["series"][::-1] if r.get("series") else None),
                    "map_score": (r["map_score"][::-1] if r.get("map_score") else None)}
    return None


def main() -> None:
    rows = asyncio.run(scrape())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"winline LIVE: {len(rows)} esports matches -> {OUT}")
    for r in rows[:15]:
        print(f"  {r['t1']} vs {r['t2']} | серия {r['series']} | карта{r['cur_map']} {r['map_score']} "
              f"| live {r['odd1']}/{r['odd2']}")


if __name__ == "__main__":
    main()
