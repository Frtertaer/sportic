#!/usr/bin/env python3
"""Phase 1 — winline odds gate for football (sofa) signals. On a signal, screenshot winline live-football,
let VISION cross-lingually find the match (Claude knows Spain=Испания) + read its total 'Больше' coef for the
signal's line. If coef >= MIN_ODDS (2.0) -> alert to the CS2 bot + log CLV (winline coef at signal time).
NO betting here (that's phase 2). Uses cheat-ai vision (public odds only). Screenshots to /tmp."""
from __future__ import annotations
import asyncio, json, sys
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, "/opt/cs2-analyst")
import vision  # noqa: E402

ROOT = Path("/opt/cs2-analyst")
LOG = ROOT / "data" / "sofa_signal_log.jsonl"
CLV = ROOT / "data" / "sofa_winline_clv.jsonl"
MIN_ODDS = 2.0
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")


async def _list_shot(page):
    """Load winline live football and screenshot only AFTER match content has rendered (SPA-safe)."""
    await page.goto("https://winline.ru/stavki/live/football/", wait_until="domcontentloaded", timeout=60000)
    for _ in range(15):
        await page.wait_for_timeout(1500)
        txt = await page.evaluate("() => document.body.innerText")
        if "Тотал" in txt and ("'" in txt or "тайм" in txt.lower() or "Т " in txt):
            break
    # scroll down to force winline to render more of the (lazy) live list, then back to top
    for _ in range(6):
        await page.mouse.wheel(0, 1400)
        await page.wait_for_timeout(500)
    await page.evaluate("() => window.scrollTo(0, 0)")
    await page.wait_for_timeout(1000)
    await page.screenshot(path="/tmp/wl_list.png", full_page=True)


def _ru_name(en: str) -> str:
    r = vision.ask_text(
        f"What is the Russian-language name of the football team '{en}' as shown on Russian bookmaker sites "
        f"like winline? Reply with ONLY the Russian name (one team), nothing else.")
    return (r or en).strip().strip('".')


async def _wait_content(page, needles=("Тотал", "Больше")):
    for _ in range(14):
        await page.wait_for_timeout(1000)
        txt = await page.evaluate("() => document.body.innerText")
        if any(n in txt for n in needles):
            return True
    return False


async def winline_over(en_t1: str, en_t2: str, line: float) -> dict:
    """Search winline by RU name, open the match (get URL), read the 'Больше{line}' coef via vision."""
    from playwright.async_api import async_playwright
    ru1 = _ru_name(en_t1)
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox", "--disable-blink-features=AutomationControlled"])
        ctx = await b.new_context(viewport={"width": 1400, "height": 1400}, user_agent=UA, locale="ru-RU")
        p = await ctx.new_page()
        await p.goto("https://winline.ru/stavki/live/football/", wait_until="domcontentloaded", timeout=60000)
        await p.wait_for_timeout(4000)
        # поиск по РУ-имени
        for sel in ['input[placeholder*="Поиск"]', 'input[type="search"]', 'input']:
            try:
                el = p.locator(sel).first
                await el.click(timeout=3000); await el.fill(ru1, timeout=3000)
                break
            except Exception:  # noqa: BLE001
                continue
        await p.wait_for_timeout(4500)
        url = None
        try:
            await p.get_by_text(ru1, exact=False).first.click(timeout=6000)  # открыть матч из результатов
            await _wait_content(p)
            url = p.url
            try:
                await p.get_by_text("Тотал", exact=True).first.click(timeout=3500)
                await p.wait_for_timeout(2500)
            except Exception:  # noqa: BLE001
                pass
        except Exception:  # noqa: BLE001
            pass
        await p.screenshot(path="/tmp/wl_match.png", full_page=True)
        await b.close()
    key = str(line).rstrip("0").rstrip(".") if "." in str(line) else str(line)
    g = vision.read_json("/tmp/wl_match.png",
        f'Winline football match page. Return JSON {{"found":true/false,"team1":"","team2":"","score":"",'
        f'"over":{{}}}} where "over" maps each main-time "Тотал (основное время) ... Больше X.X" line to its '
        f'coefficient, e.g. {{"2.5":1.31,"3.5":2.05,"4.5":3.61}}. found=false if this is not a football match.',
        900) or {}
    over_map = {str(k).replace(",", "."): v for k, v in (g.get("over") or {}).items()}
    try:
        over = float(str(over_map.get(key) or over_map.get(f"{line:.1f}") or 0).replace(",", "."))
    except Exception:  # noqa: BLE001
        over = 0.0
    return {"found": bool(g.get("found") and over > 0), "ru_t1": ru1, "over": over,
            "wl_line": line if over > 0 else None, "url": url, "over_map": over_map}


def _tg(msg):
    try:
        sys.path.insert(0, "/opt/cs2-analyst")
        import monitor as mon
        mon.telegram_send(msg)
    except Exception as e:  # noqa: BLE001
        print("tg fail", e)


def main():
    # свежие незакрытые сигналы (последние ~20)
    sigs = [json.loads(l) for l in LOG.read_text(encoding="utf-8").splitlines()[-20:] if l.strip()] if LOG.exists() else []
    # только те, что могли ещё жить (последний час)
    now = datetime.now(timezone.utc)
    fresh = [s for s in sigs if s.get("src") == "xg"]  # phase1: только xG-сигналы
    print(f"сигналов к проверке: {len(fresh)}")
    for s in fresh[-3:]:
        line = float(str(s.get("market", "ТБ 2.5")).replace("ТБ", "").strip())
        r = asyncio.run(winline_over(s["home"], s["away"], line))
        print(f"  {s['home']} vs {s['away']} ТБ{line}: {r}")
        if r.get("found"):
            with CLV.open("a", encoding="utf-8") as f:   # log CLV for EVERY matched signal (measure edge)
                f.write(json.dumps({**s, "wl_over": r["over"], "wl_line": r.get("wl_line"),
                                    "wl_url": r.get("url"), "checked_at": now.isoformat()},
                                   ensure_ascii=False) + "\n")
        if r.get("found") and r.get("over", 0) >= MIN_ODDS:
            link = f'\n   🔗 <a href="{r["url"]}">Открыть матч на winline</a>' if r.get("url") else ""
            msg = (f"💰 <b>СТАВКА — ТБ {line} @ {r['over']:.2f}</b>  (гейт ≥{MIN_ODDS} ✅)\n"
                   f"🏟 <b>{s['home']} — {s['away']}</b> · {s.get('score')} {s.get('minute')}'\n"
                   f"   наш сигнал: {s.get('market')} | модель p={s.get('p_model')} fair={s.get('fair')}\n"
                   f"   winline коэф <b>{r['over']:.2f}</b> на «Больше {line}» → жирный value{link}\n"
                   f"   ⚡ жми ссылку → 1 клик на «Больше {line}»")
            _tg(msg)


if __name__ == "__main__":
    main()
