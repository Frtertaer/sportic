#!/usr/bin/env python3
"""Scrape winline's OWN results (/auth/results, authed) for finished CS2 matches — 100% covers what
winline offered (bo3 misses CCT/United21/etc.). Clicks the results-page Киберспорт filter (Angular
sport-menu, NOT the header nav), parses 'Team1 - Team2' + score, writes data/winline_results.json."""
from __future__ import annotations
import asyncio, json, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
AUTH = ROOT / "data" / "winline_auth.json"
OUT = ROOT / "data" / "winline_results.json"
URL = "https://winline.ru/auth/results"

_CLICK_CYBER = """
() => {
  const el = [...document.querySelectorAll('[class*=sport-menu__list-item]')]
    .find(x => /Киберспорт/i.test(x.innerText||''));
  if (el) { (el.querySelector('a,[class*=link]') || el).click(); return true; }
  return false;
}
"""

_PARSE = r"""
() => {
  const out = [];
  const teamRe = /^(.{2,34}?)\s+[-–]\s+(.{2,34})$/;
  for (const c of document.querySelectorAll('[class*=result],[class*=event],[class*=match],[class*=card__body]')) {
    const lines = (c.innerText||'').split('\n').map(x=>x.trim()).filter(Boolean);
    let teams = null, sc = null;
    for (const l of lines) {
      if (!teams) { const m = l.match(teamRe);
        if (m && /[A-Za-zА-Яа-я0-9]/.test(m[1]) && /[A-Za-zА-Яа-я0-9]/.test(m[2])) teams = [m[1].trim(), m[2].trim()]; }
      if (!sc) { const s = l.match(/^(\d{1,2})\s*:\s*(\d{1,2})/); if (s) sc = [+s[1], +s[2]]; }
    }
    if (teams && sc && Math.max(sc[0],sc[1]) <= 3) out.push({ t1: teams[0], t2: teams[1], s1: sc[0], s2: sc[1] });
  }
  const seen = new Set(); const uniq = [];
  for (const r of out) { const k = r.t1+'|'+r.t2; if (!seen.has(k)) { seen.add(k); uniq.push(r); } }
  return uniq.slice(0, 80);
}
"""


async def scrape() -> list[dict]:
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(storage_state=str(AUTH), locale="ru-RU",
                                  viewport={"width": 1500, "height": 1500})
        p = await ctx.new_page()
        await p.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await p.wait_for_timeout(8000)
        clicked = await p.evaluate(_CLICK_CYBER)
        await p.wait_for_timeout(5000)
        for _ in range(14):
            await p.evaluate("window.scrollBy(0, 1500)")
            await p.wait_for_timeout(400)
        rows = await p.evaluate(_PARSE)
        await b.close()
    print(f"(filter clicked: {clicked})")
    return rows


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9а-я]", "", s.lower())


def main():
    rows = asyncio.run(scrape())
    out = []
    for r in rows:
        if r["s1"] == r["s2"]:
            continue
        winner = r["t1"] if r["s1"] > r["s2"] else r["t2"]
        out.append({"t1": r["t1"], "t2": r["t2"], "s1": r["s1"], "s2": r["s2"], "winner": winner,
                    "n1": _norm(r["t1"]), "n2": _norm(r["t2"]), "wn": _norm(winner)})
    OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(), "matches": out},
                              ensure_ascii=False), encoding="utf-8")
    print(f"winline results: {len(out)} finished matches -> {OUT.name}")
    for r in out[:18]:
        print(f"  {r['t1']} {r['s1']}:{r['s2']} {r['t2']}  -> {r['winner']}")


if __name__ == "__main__":
    main()
