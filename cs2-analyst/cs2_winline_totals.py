#!/usr/bin/env python3
"""Scrape winline's per-map ROUND-TOTAL line + over/under odds for CS2 matches.

The round total ("1 карта 21.5") is the ONE market where our calibrated round_totals
model has a (small, honest) read vs the book. Layout in a card's token stream is a
line token (x.5 in 17.5-27.5) flanked by the over odd (before) and under odd (after),
following a "N карта" label. Writes data/winline_totals.json = [{t1,t2,totals:[{map_no,
line,over,under}]}], consumed by the totals value monitor.

  <playwright-python> cs2_winline_totals.py        scrape + write json
"""
from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timezone
from pathlib import Path

OUT = Path(__file__).resolve().parent / "data" / "winline_totals.json"
URL = "https://winline.ru/stavki/sport/kibersport/counter-strike"

_ODD = re.compile(r"^\d{1,2}\.\d{2}$")
_LINE = re.compile(r"^\d{2}\.5$")


def _is_name(s: str) -> bool:
    return (bool(re.search(r"[A-Za-zА-Яа-я]", s))
            and not re.match(r"(?i)^матч$|карта|тотал|фора|сегодня|завтра|период|^пер\.", s)
            and 1 < len(s) < 40 and not _ODD.match(s))


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _parse_card(tokens: list[str]) -> dict | None:
    teams = [t for t in tokens if _is_name(t)][:2]
    if len(teams) < 2:
        return None
    totals = []
    cur_map = 0
    for i, t in enumerate(tokens):
        m = re.match(r"(?i)^(\d)\s*карта", t)
        if m:
            cur_map = int(m.group(1))
        elif re.match(r"(?i)^матч$", t):
            cur_map = 0
        if _LINE.match(t) and 17.5 <= float(t) <= 27.5:
            over = tokens[i - 1] if i > 0 else ""
            under = tokens[i + 1] if i + 1 < len(tokens) else ""
            if _ODD.match(over) and _ODD.match(under):
                totals.append({"map_no": cur_map, "line": float(t),
                               "over": float(over), "under": float(under)})
    if not totals:
        return None
    # dedupe by (map_no, line)
    seen, uniq = set(), []
    for r in totals:
        k = (r["map_no"], r["line"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(r)
    return {"t1": teams[0], "t2": teams[1], "totals": uniq}


_SCROLL = r"""
() => {
  const c = document.querySelectorAll('.card__body,[class*=card__body]');
  if (c.length) c[c.length - 1].scrollIntoView({ block: 'end' });
  const m = document.querySelector('main,.main__wrapper,[class*=main__wrapper]');
  if (m) m.scrollTop = m.scrollHeight;
  window.scrollBy(0, 900);
}
"""
_CARDS = r"""
() => {
  const out = [];
  for (const bd of document.querySelectorAll('.card__body,[class*=card__body]')) {
    let card = bd;
    for (let k = 0; k < 5; k++) { if (card.parentElement) card = card.parentElement; }
    out.push((card.innerText || '').split('\n').map(x => x.trim()).filter(Boolean));
  }
  return out;
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
        await page.wait_for_timeout(9000)
        for _ in range(40):
            for toks in await page.evaluate(_CARDS):
                r = _parse_card(toks)
                if r:
                    acc[(r["t1"].lower(), r["t2"].lower())] = r
            await page.evaluate(_SCROLL)
            await page.wait_for_timeout(400)
        for toks in await page.evaluate(_CARDS):
            r = _parse_card(toks)
            if r:
                acc[(r["t1"].lower(), r["t2"].lower())] = r
        await b.close()
    out = list(acc.values())
    for r in out:
        r["fetched_at"] = now
    return out


def lookup(team1: str, team2: str) -> dict | None:
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
        if (m(n1, w1) and m(n2, w2)) or (m(n1, w2) and m(n2, w1)):
            return r
    return None


def main() -> None:
    rows = asyncio.run(scrape())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    n_tot = sum(len(r["totals"]) for r in rows)
    print(f"winline totals: {len(rows)} matches, {n_tot} round-total lines -> {OUT}")
    for r in rows[:12]:
        ts = "; ".join(f"к{t['map_no']} O/U {t['line']} @{t['over']}/{t['under']}" for t in r["totals"])
        print(f"  {r['t1']} vs {r['t2']}: {ts}")


if __name__ == "__main__":
    main()
