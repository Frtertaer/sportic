#!/usr/bin/env python3
"""Scrape g-cs.ru/matches/finished — FULL CS2 results coverage (has the tournaments bo3 misses:
CCT/United21/Al Ahli/Västerås). Each finished-match link exposes leaves [team1, s1, '-', s2, team2].
Writes data/gcs_results.json for the resolvers. Playwright (real browser) to pass g-cs.ru anti-bot."""
from __future__ import annotations
import asyncio, json, re, unicodedata
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "data" / "gcs_results.json"
URL = "https://g-cs.ru/matches/finished"
_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
       "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


async def scrape():
    from playwright.async_api import async_playwright
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(locale="ru-RU", viewport={"width": 1400, "height": 1600}, user_agent=_UA)
        p = await ctx.new_page()
        await p.goto(URL, wait_until="networkidle", timeout=45000)
        await p.wait_for_timeout(4000)
        for _ in range(12):
            await p.evaluate("window.scrollBy(0, 1500)")
            await p.wait_for_timeout(400)
        rows = await p.evaluate(r"""() => {
            const out = [];
            for (const a of document.querySelectorAll('a[href*="/matches/2"]')) {
                const leaves = [];
                for (const el of a.querySelectorAll('*')) {
                    if (el.children.length === 0) { const t = (el.innerText||'').trim(); if (t) leaves.push(t); }
                }
                out.push({ href: a.getAttribute('href'), leaves });
            }
            return out;
        }""")
        await b.close()
    return rows


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9а-я]", "", s.lower())


def main():
    rows = asyncio.run(scrape())
    out, seen = [], set()
    for r in rows:
        lv = r.get("leaves") or []
        digs = [(i, int(x)) for i, x in enumerate(lv) if x.isdigit() and int(x) <= 3]
        if len(digs) < 2 or len(lv) < 5:
            continue
        (i1, s1), (i2, s2) = digs[0], digs[1]
        t1 = lv[0]
        t2 = lv[i2 + 1] if i2 + 1 < len(lv) else lv[-1]
        if s1 == s2 or not t1 or not t2 or t1 == t2:
            continue
        href = r.get("href") or ""
        if href in seen:
            continue
        seen.add(href)
        winner = t1 if s1 > s2 else t2
        out.append({"t1": t1, "t2": t2, "s1": s1, "s2": s2, "winner": winner,
                    "n1": _norm(t1), "n2": _norm(t2), "wn": _norm(winner), "date": href[10:20]})
    OUT.write_text(json.dumps({"updated": datetime.now(timezone.utc).isoformat(), "matches": out},
                              ensure_ascii=False), encoding="utf-8")
    print(f"g-cs.ru results: {len(out)} finished CS2 matches -> {OUT.name}")
    for r in out[:14]:
        print(f"  {r['t1']} {r['s1']}:{r['s2']} {r['t2']}  -> {r['winner']}")


if __name__ == "__main__":
    main()
