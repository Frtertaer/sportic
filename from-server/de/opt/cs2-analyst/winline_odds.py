#!/usr/bin/env python3
"""Scrape CS2/esports ML odds from winline.ru (RU book, loads in headless on DE,
no Cloudflare/geo block; its line WS is binary so we read the rendered DOM instead).

Writes data/winline_odds.json = [{t1,t2,ml1,ml2,fetched_at}], consumed by the cs2
monitor for AUTO edge vs the book (replaces manual /coef). Run via the playwright
venv (/opt/kwork-monitor/.venv) on a timer.

  <playwright-python> cs2_winline_odds.py        scrape + write json
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "winline_odds.json"
# CS2-only section (the bare /kibersport page mixes Dota2/LoL/etc and the 40-card
# cap was never reaching CS2 matches -> auto-odds never matched). This URL is the
# exclusive Counter-Strike discipline filter.
URL = "https://winline.ru/stavki/sport/kibersport/counter-strike"


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _team_match(a: str, b: str) -> bool:
    return bool(a and b and (a in b or b in a or (len(a) >= 4 and len(b) >= 4 and a[:5] == b[:5])))


def lookup(team1: str, team2: str, max_age_min: int = 30) -> dict | None:
    """Find winline ML odds for our (team1, team2). Returns odds aligned to our order."""
    if not OUT.exists():
        return None
    try:
        rows = json.loads(OUT.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    n1, n2 = _norm(team1), _norm(team2)
    for r in rows:
        w1, w2 = _norm(r.get("t1", "")), _norm(r.get("t2", ""))
        if _team_match(n1, w1) and _team_match(n2, w2):
            return {"odd1": r["ml1"], "odd2": r["ml2"], "src": "winline"}
        if _team_match(n1, w2) and _team_match(n2, w1):
            return {"odd1": r["ml2"], "odd2": r["ml1"], "src": "winline"}
    return None


def lookup_slug(slug: str) -> dict | None:
    """Fallback: match winline using the bo3 SLUG (which carries full team names),
    not the abbreviated DB name. e.g. DB 'LP' fails but slug 'largados_e_pelados-vs-
    vasco-...' fuzzy-matches winline 'LARGADOSYPELADOS'. Returns odds aligned to slug
    order (= our team1/team2 order, since the slug is team1-vs-team2)."""
    if not slug or "-vs-" not in slug:
        return None
    a, b = slug.split("-vs-", 1)
    return lookup(a, b)


_EXTRACT = r"""
() => {
  // NB: do NOT reject names starting with a digit — real teams are "9INE", "3DMAX",
  // "1WIN" etc. Pure numbers/odds/dates are still dropped (they have no letter, or match
  // the odds pattern). Old "^\d" rule silently lost every digit-led team (e.g. 9INE).
  const isName = s => /[A-Za-zА-Яа-я]/.test(s)
    && !/^матч$|карта|тотал|фора|сегодня|завтра|период|^пер\./i.test(s)
    && !/^\d{1,2}\s*[КKкk]$/.test(s)
    && s.length > 1 && s.length < 40 && !/^\d{1,2}\.\d{2}$/.test(s);
  const out = [];
  for (const body of document.querySelectorAll('.card__body, [class*="card__body"]')) {
    const bt = (body.innerText || '').split('\n').map(x => x.trim());
    const mi = bt.findIndex(x => /^матч$/i.test(x));
    if (mi < 0) continue;
    const ml = bt.slice(mi + 1).filter(x => /^\d{1,2}\.\d{2}$/.test(x)).slice(0, 2);
    if (ml.length < 2) continue;
    let card = body, teams = [];
    for (let i = 0; i < 6; i++) {
      card = card.parentElement; if (!card) break;
      const lines = [...new Set((card.innerText || '').split('\n').map(x => x.trim()).filter(isName))];
      if (lines.length >= 2) { teams = lines.slice(0, 2); break; }
    }
    if (teams.length === 2) out.push({ t1: teams[0], t2: teams[1], ml1: ml[0], ml2: ml[1] });
    if (out.length >= 60) break;
  }
  return out;
}
"""


# winline's CS2 list scrolls INSIDE a <main> container, NOT the window — so mouse.wheel
# on the window never moves it (old bug: only the first ~18 cards rendered). Push the last
# card into view + scroll the container, extracting incrementally to catch every tournament
# section (CS2 line runs ~10 days out, grouped by tournament).
_SCROLL = r"""
() => {
  const cards = document.querySelectorAll('.card__body, [class*="card__body"]');
  if (cards.length) cards[cards.length - 1].scrollIntoView({ block: 'end' });
  const m = document.querySelector('main, .main__wrapper, [class*="main__wrapper"]');
  if (m) m.scrollTop = m.scrollHeight;
  window.scrollBy(0, 900);
}
"""


async def scrape() -> list[dict]:
    from playwright.async_api import async_playwright
    acc: dict[tuple, dict] = {}
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"),
            locale="ru-RU", viewport={"width": 1500, "height": 1100})
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(9000)
        for _ in range(45):
            for r in await page.evaluate(_EXTRACT):
                acc[(r["t1"].lower(), r["t2"].lower())] = r
            await page.evaluate(_SCROLL)
            await page.wait_for_timeout(450)
        for r in await page.evaluate(_EXTRACT):
            acc[(r["t1"].lower(), r["t2"].lower())] = r
        await b.close()
    out = []
    now = datetime.now(timezone.utc).isoformat()
    for r in acc.values():
        try:
            out.append({"t1": r["t1"], "t2": r["t2"],
                        "ml1": float(r["ml1"]), "ml2": float(r["ml2"]), "fetched_at": now})
        except (ValueError, KeyError):
            continue
    return out


def main() -> None:
    rows = asyncio.run(scrape())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"winline: wrote {len(rows)} matches -> {OUT}")
    for r in rows[:12]:
        print(f"  {r['t1']} {r['ml1']} / {r['t2']} {r['ml2']}")


if __name__ == "__main__":
    main()
