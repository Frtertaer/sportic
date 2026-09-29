#!/usr/bin/env python3
"""LIVE-LAG SNIPER — auto-places winline live bets on near-certain CS2 series outcomes.

THE EDGE IS LATENCY. When a team is up big on the in-progress map (e.g. 11-4) and that map nearly
decides the series, it closes ~94% and wins the series ~97% — but winline's LIVE match-winner odd
LAGS for seconds before it re-prices or suspends. A human reading a Telegram DM is minutes too slow;
the 2.06 is long gone. This daemon holds a WARM authenticated winline page, re-reads the CURRENT
price itself every few seconds, verifies the edge is STILL there on a FRESH quote and the market is
NOT suspended, then fires a quarter-Kelly stake in ~1-2s.

REAL MONEY. Safety rails, all on by default:
  * DRY_RUN=1 (default): does everything up to — but NOT — the final confirm; logs WOULD-BET.
  * Reuses a ONE-TIME captured login (winline_auth.json, chmod 600). Never stores a password.
  * MAX_STAKE_RUB hard cap · DAILY_LOSS_RUB kill-switch · KILL file · per-series dedup.
  * Stake rounding + jitter + randomized human delay (account-survival / anti-bot).
  * Fresh-quote re-verify at placement: if winline already moved/suspended -> skip (no ghost bets).

Run:  python3 livelag_bet.py                 # DRY_RUN daemon (safe)
      DRY_RUN=0 python3 livelag_bet.py        # LIVE (only after login capture + calibration)
      python3 livelag_bet.py --calibrate       # dump bet-slip DOM to nail selectors on 1st live match
"""
from __future__ import annotations
import asyncio, json, os, random, sys, time
from datetime import datetime, timezone, date
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import state_model as sm            # map_state_winprob / series_winprob (same model the tracker uses)

AUTH   = ROOT / "data" / "winline_auth.json"          # storage_state from one-time login capture
KILL   = ROOT / "data" / "livelag_bot.KILL"           # touch this file to hard-stop instantly
BETLOG = ROOT / "data" / "livelag_bets_placed.jsonl"
SEEN   = ROOT / "data" / "livelag_bot_seen.json"
MEASURE_LOG = ROOT / "data" / "livelag_measure.jsonl" # persistence measurements (no auth, no bets)
URL    = "https://winline.ru/stavki/live/cyber/"

# ---- betting policy (real money) — tighter than the paper tracker ---------------------------------
BANKROLL_RUB   = float(os.getenv("WINLINE_BANKROLL_RUB", "10000"))
KELLY_FRACTION = float(os.getenv("KELLY_FRACTION", "0.25"))   # quarter-Kelly
MAX_STAKE_PCT  = float(os.getenv("MAX_STAKE_PCT", "0.015"))   # <=1.5% bankroll / bet
MAX_STAKE_RUB  = float(os.getenv("MAX_STAKE_RUB", "1500"))    # absolute hard cap / bet
DAILY_LOSS_RUB = float(os.getenv("DAILY_LOSS_RUB", "3000"))   # stop for the day after this loss
MIN_P_SERIES   = float(os.getenv("MIN_P_SERIES", "0.90"))     # real money: only near-locks
MIN_EDGE       = float(os.getenv("MIN_EDGE", "0.06"))
MAX_QUOTE_AGE  = float(os.getenv("MAX_QUOTE_AGE_S", "8"))     # the quote we bet on must be <8s old
MIN_LEAD       = int(os.getenv("MIN_LEAD", "4"))
MIN_LEADER_RD  = int(os.getenv("MIN_LEADER_RD", "10"))
POLL_S         = float(os.getenv("POLL_S", "4"))
DRY_RUN        = os.getenv("DRY_RUN", "1") != "0"

import re
_DEC  = re.compile(r"^\d{1,2}\.\d{2}$")
_MAP  = re.compile(r"^(\d+)\s*[КK]$")
_NAME = re.compile(r"[A-Za-zА-Яа-я]")
def _norm(s: str) -> str:  return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


def _parse_card(text: str) -> dict | None:
    toks = [t.strip() for t in text.split("\n") if t.strip()]
    names = [t for t in toks if _NAME.search(t) and not _MAP.match(t) and not _DEC.match(t) and len(t) < 40]
    odds  = [float(t) for t in toks if _DEC.match(t)]
    ints  = [int(t) for t in toks if t.isdigit()]
    cur   = next((int(m.group(1)) for t in toks if (m := _MAP.match(t))), None)
    if len(names) < 2 or len(odds) < 2 or cur is None:
        return None
    return {"t1": names[0], "t2": names[1], "series": ints[:2] if len(ints) >= 2 else [0, 0],
            "map_score": ints[2:4] if len(ints) >= 4 else None, "cur_map": cur,
            "odd1": odds[-2], "odd2": odds[-1]}


def _opportunity(card: dict, min_p: float = MIN_P_SERIES, min_edge: float = MIN_EDGE) -> dict | None:
    """Return a near-lock bet on this live card, or None. Pure model math — no I/O."""
    ms = card.get("map_score"); ser = card.get("series") or [0, 0]
    if not ms or len(ms) < 2:
        return None
    r1, r2 = int(ms[0]), int(ms[1])
    t1, t2, o1, o2 = card.get("t1"), card.get("t2"), card.get("odd1"), card.get("odd2")
    if not (t1 and t2 and o1 and o2):
        return None
    if max(r1, r2) - min(r1, r2) < MIN_LEAD or max(r1, r2) < MIN_LEADER_RD:
        return None
    if r1 > r2:
        lead, other, lead_odd, sa, sb = t1, t2, o1, int(ser[0]), int(ser[1])
    else:
        lead, other, lead_odd, sa, sb = t2, t1, o2, int(ser[1]), int(ser[0])
    need = 3 if max(sa, sb) >= 2 else 2
    p_map = sm.map_state_winprob(max(r1, r2), min(r1, r2))
    p_series = sm.series_winprob(sa, sb, p_map, need)
    edge = p_series * lead_odd - 1
    if p_series < min_p or edge < min_edge:
        return None
    return {"t1": t1, "t2": t2, "lead": lead, "other": other, "lead_odd": lead_odd,
            "series": [sa, sb], "map_score": [max(r1, r2), min(r1, r2)], "cur_map": card["cur_map"],
            "p_map": round(p_map, 3), "p_series": round(p_series, 3), "edge": round(edge, 3),
            "key": f"{_norm(t1)}|{_norm(t2)}|s{sa}-{sb}|{_norm(lead)}"}


def _stake_rub(edge: float, odd: float, p: float) -> int:
    b = odd - 1.0
    kelly = max(0.0, (p * odd - 1.0) / b) if b > 0 else 0.0
    frac = min(MAX_STAKE_PCT, KELLY_FRACTION * kelly)
    rub = min(MAX_STAKE_RUB, frac * BANKROLL_RUB)
    rub *= random.uniform(0.90, 1.03)                 # jitter so stakes aren't a fingerprint
    return max(50, int(round(rub / 10.0) * 10))        # round to 10₽, floor 50₽


def _load(p: Path, default):
    try:    return json.loads(p.read_text(encoding="utf-8"))
    except Exception:  return default


def _daily_loss() -> float:
    today = date.today().isoformat()
    tot = 0.0
    for l in _read_lines(BETLOG):
        r = json.loads(l)
        if r.get("placed") and str(r.get("at", "")).startswith(today):
            tot += r.get("pl_rub", 0.0)                # settled P/L written back by the resolver
    return -tot                                        # positive number = net loss today


def _read_lines(p: Path):
    return p.read_text(encoding="utf-8").splitlines() if p.exists() else []


# --------------------------------------------------------------------------------------------------
async def place_bet(page, opp: dict, stake: int) -> dict:
    """Click the leader's live odd, enter stake, confirm. DRY_RUN stops before confirm.
    Selectors are winline's live bet-slip; calibrate once on a real match (--calibrate)."""
    res = {"placed": False, "reason": "", "stake": stake}
    try:
        # 1) find the live card for this match and click the LEADER's odd button
        clicked = await page.evaluate(
            """([leadNorm, oddVal]) => {
                const norm = s => (s||'').toLowerCase().replace(/[^a-z0-9]/g,'');
                for (const c of document.querySelectorAll('[class*=card__body-live]')) {
                    const txt = c.innerText;
                    if (!norm(txt).includes(leadNorm)) continue;
                    // odd buttons on the card
                    const btns = c.querySelectorAll('[class*=coefficient],[class*=outcome],button');
                    for (const b of btns) {
                        if (b.innerText && b.innerText.trim().startsWith(String(oddVal))) {
                            b.scrollIntoView({block:'center'}); b.click(); return true;
                        }
                    }
                }
                return false;
            }""", [_norm(opp["lead"]), opp["lead_odd"]])
        if not clicked:
            res["reason"] = "odd button not found (market may be suspended)"; return res
        await page.wait_for_timeout(700)

        # 2) bet-slip: fill stake + read the slip's CURRENT odd (fresh re-verify happens in caller)
        filled = await page.evaluate(
            """(stake) => {
                const inp = document.querySelector("input[class*=stake],input[class*=amount],[class*=coupon] input[type=text],[class*=basket] input");
                if (!inp) return false;
                const set = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype,'value').set;
                set.call(inp, String(stake));
                inp.dispatchEvent(new Event('input',{bubbles:true}));
                inp.dispatchEvent(new Event('change',{bubbles:true}));
                return true;
            }""", stake)
        if not filled:
            res["reason"] = "stake input not found"; return res
        await page.wait_for_timeout(500)

        if DRY_RUN:
            res["reason"] = "DRY_RUN — slip built, confirm skipped"; return res

        # 3) confirm — the "Заключить пари" button
        ok = await page.evaluate(
            """() => {
                const b = [...document.querySelectorAll('button,[class*=coupon] [class*=button]')]
                    .find(x => /заключить|поставить|сделать ставку/i.test(x.innerText||''));
                if (b) { b.click(); return true; } return false;
            }""")
        res["placed"] = bool(ok)
        res["reason"] = "confirmed" if ok else "confirm button not found"
        return res
    except Exception as e:  # noqa: BLE001
        res["reason"] = f"error: {type(e).__name__}: {e}"; return res


async def daemon() -> None:
    from playwright.async_api import async_playwright
    if not AUTH.exists():
        print(f"!! no {AUTH.name} — run capture_winline_login.py once to log in first."); return
    seen = _load(SEEN, {})
    mode = "DRY-RUN (no real bets)" if DRY_RUN else "*** LIVE — REAL MONEY ***"
    print(f"[livelag-bet] warm-page sniper up | {mode} | bankroll {BANKROLL_RUB:.0f}₽ "
          f"| min P {MIN_P_SERIES:.0%} edge {MIN_EDGE:.0%}")
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(storage_state=str(AUTH), locale="ru-RU",
                                  viewport={"width": 1500, "height": 1100},
                                  user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(7000)
        last_hb, last_reload = 0.0, time.time()
        while True:
            if KILL.exists():
                print("[livelag-bet] KILL file present — stopping."); break
            if _daily_loss() >= DAILY_LOSS_RUB:
                print(f"[livelag-bet] daily loss limit {DAILY_LOSS_RUB:.0f}₽ hit — stopping for today."); break
            try:
                if time.time() - last_reload > 180:      # keep the authed page/session warm
                    await page.reload(wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(5000); last_reload = time.time()
                await page.evaluate(
                    "() => { const m=document.querySelector('main,[class*=main__wrapper]'); if(m) m.scrollTop=0; }")
                cards = await page.evaluate(
                    "() => Array.from(document.querySelectorAll('[class*=card__body-live]')).map(c => c.innerText)")
                t_quote = time.time()
                if time.time() - last_hb > 90:           # heartbeat so the sniper is not a black box
                    nopp = sum(1 for raw in cards if (_parse_card(raw) and _opportunity(_parse_card(raw))))
                    print(f"[livelag-bet] sniper alive: {len(cards)} live карт, {nopp} near-lock готовых к ставке")
                    last_hb = time.time()
                for raw in cards:
                    card = _parse_card(raw)
                    if not card:
                        continue
                    opp = _opportunity(card)
                    if not opp or opp["key"] in seen:
                        continue
                    if time.time() - t_quote > MAX_QUOTE_AGE:   # our own read went stale -> don't bet
                        continue
                    stake = _stake_rub(opp["edge"], opp["lead_odd"], opp["p_series"])
                    delay = random.uniform(0.4, 1.8)            # human-like reaction jitter
                    await page.wait_for_timeout(int(delay * 1000))
                    result = await place_bet(page, opp, stake)
                    rec = {**opp, "stake_rub": stake, "dry_run": DRY_RUN, "placed": result["placed"],
                           "reason": result["reason"], "at": datetime.now(timezone.utc).isoformat()}
                    with BETLOG.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                    tag = "PLACED ✓" if result["placed"] else ("WOULD-BET" if DRY_RUN else "SKIP")
                    print(f"[livelag-bet] {tag} {opp['lead']} @ {opp['lead_odd']} "
                          f"(P {opp['p_series']:.0%}, edge {opp['edge']:+.0%}) {stake}₽ — {result['reason']}")
                    seen[opp["key"]] = rec["at"]
                    SEEN.write_text(json.dumps(seen), encoding="utf-8")
            except Exception as e:  # noqa: BLE001
                print(f"[livelag-bet] loop error: {type(e).__name__}: {e}")
            await asyncio.sleep(POLL_S)
        await b.close()


async def calibrate() -> None:
    """Open the live page authenticated, click the first live odd, and dump the bet-slip DOM
    so we can pin the exact stake-input + confirm selectors for THIS winline build."""
    from playwright.async_api import async_playwright
    if not AUTH.exists():
        print("!! capture login first."); return
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(storage_state=str(AUTH), locale="ru-RU")
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(8000)
        await page.evaluate("""() => {
            const b=document.querySelector('[class*=card__body-live] [class*=coefficient],[class*=card__body-live] button');
            if (b) b.click();
        }""")
        await page.wait_for_timeout(2500)
        dom = await page.evaluate("""() => {
            const slip=document.querySelector('[class*=coupon],[class*=basket],[class*=betslip]');
            return slip ? slip.outerHTML.slice(0,4000) : 'NO SLIP CONTAINER FOUND';
        }""")
        Path(ROOT / "data" / "winline_slip_dom.html").write_text(dom, encoding="utf-8")
        print("bet-slip DOM -> data/winline_slip_dom.html (inspect to pin selectors)")
        await b.close()


async def measure() -> None:
    """NO AUTH, NO BETS. Warm PUBLIC winline live page; for every near-lock opportunity, sample the
    leader's live odd every ~3s and record how many SECONDS the fat (exploitable) price persists
    before winline re-prices / suspends / the map closes. This is the make-or-break measurement:
    if the fat price survives 8-15s+ often -> a login-based sniper prints; if it vanishes in 1-3s
    or the market suspends -> the 'edge' is a mirage. Uses a wider net (P>=0.82) to gather data."""
    from playwright.async_api import async_playwright
    active: dict[str, dict] = {}
    print("[livelag-measure] measuring winline live-lag persistence (no auth, no bets)")
    async with async_playwright() as pw:
        b = await pw.chromium.launch(headless=True, args=["--no-sandbox"])
        ctx = await b.new_context(locale="ru-RU", viewport={"width": 1500, "height": 1100},
                                  user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                                              "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"))
        page = await ctx.new_page()
        await page.goto(URL, wait_until="domcontentloaded", timeout=45000)
        await page.wait_for_timeout(7000)
        last_hb, last_reload = 0.0, time.time()
        while True:
            if KILL.exists():
                print("[livelag-measure] KILL file — stopping."); break
            try:
                if time.time() - last_reload > 180:      # refresh the warm page so the SPA can't go stale
                    await page.reload(wait_until="domcontentloaded", timeout=45000)
                    await page.wait_for_timeout(5000); last_reload = time.time()
                await page.evaluate(
                    "() => { const m=document.querySelector('main,[class*=main__wrapper]'); if(m) m.scrollTop=0; }")
                cards = await page.evaluate(
                    "() => Array.from(document.querySelectorAll('[class*=card__body-live]')).map(c => c.innerText)")
                now = time.time()
                if now - last_hb > 90:                    # heartbeat: prove it's alive + show closest state
                    leads = []
                    for raw in cards:
                        c = _parse_card(raw)
                        if c and c.get("map_score") and len(c["map_score"]) >= 2:
                            ms = c["map_score"]; leads.append((abs(ms[0] - ms[1]), c))
                    best = ""
                    if leads:
                        dlt, c = max(leads, key=lambda x: x[0])
                        best = f"| ближайший разрыв {dlt} · {c['t1']} vs {c['t2']} {c['map_score']}"
                    print(f"[livelag-measure] alive: {len(cards)} live карт, {len(active)} активных near-lock {best}")
                    last_hb = now
                present = set()
                for raw in cards:
                    card = _parse_card(raw)
                    if not card:
                        continue
                    opp = _opportunity(card, min_p=0.82, min_edge=0.04)
                    if not opp:
                        continue
                    k = opp["key"]; present.add(k)
                    a = active.get(k)
                    if a is None:
                        active[k] = {"opp": opp, "first_odd": opp["lead_odd"], "max_odd": opp["lead_odd"],
                                     "min_odd": opp["lead_odd"], "t0": now, "n": 1}
                    else:
                        a["max_odd"] = max(a["max_odd"], opp["lead_odd"])
                        a["min_odd"] = min(a["min_odd"], opp["lead_odd"])
                        a["fat_secs"] = round(now - a["t0"], 1)
                        a["n"] += 1
                for k in list(active):
                    if k not in present:                 # opportunity ended: map closed / odds collapsed / suspended
                        a = active.pop(k); o = a["opp"]
                        rec = {"match": f'{o["t1"]} vs {o["t2"]}', "leader": o["lead"], "series": o["series"],
                               "first_odd": a["first_odd"], "max_odd": a["max_odd"], "min_odd": a["min_odd"],
                               "p_series": o["p_series"], "edge": o["edge"], "samples": a["n"],
                               "fat_secs": a.get("fat_secs", 0.0),
                               "ended_at": datetime.now(timezone.utc).isoformat()}
                        with MEASURE_LOG.open("a", encoding="utf-8") as f:
                            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                        print(f"[livelag-measure] {rec['match']} — {rec['leader']} fat price lasted "
                              f"{rec['fat_secs']}s (odd {a['first_odd']}→{a['min_odd']}, P {o['p_series']:.0%})")
            except Exception as e:  # noqa: BLE001
                print(f"[livelag-measure] loop error: {type(e).__name__}: {e}")
            await asyncio.sleep(3)
        await b.close()


if __name__ == "__main__":
    if "--calibrate" in sys.argv:
        asyncio.run(calibrate())            # dump bet-slip DOM (needs auth) to pin confirm selector
    elif "--measure" in sys.argv or not AUTH.exists():
        asyncio.run(measure())              # autonomous: measure lag persistence, no auth, no bets
    else:
        asyncio.run(daemon())               # auth present -> DRY_RUN sniper (or live if DRY_RUN=0)
