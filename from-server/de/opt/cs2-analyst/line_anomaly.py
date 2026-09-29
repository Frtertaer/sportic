#!/usr/bin/env python3
"""Scan the ENTIRE winline CS2 board for pre-match LINE-MOVEMENT anomalies (договорняк / insider).
Uses data/odds_history.jsonl (timestamped ML odds per match). NO model needed -> covers ALL matches,
incl. the low-tier ones where fixes happen most. Detects:
  STEAM   — a side shortened hard & steadily (money/insider piling on one outcome) -> follow it
  REVERSE — the OPENING favourite drifted OUT sharply (sharp money on the dog) -> strong signal
  ERRATIC — wild bouncing / many reversals (manipulation churn) -> договорняк-risk, usually AVOID
Run standalone to score the current history; --top N to list the strongest.
"""
from __future__ import annotations
import json, sys, statistics
from collections import defaultdict
from pathlib import Path

HIST = Path(__file__).resolve().parent / "data" / "odds_history.jsonl"

STEAM_DRIFT = 0.18     # fav shortened >=18% steadily
REVERSE_DRIFT = 0.20   # opening fav lengthened >=20%
ERRATIC_REV = 4        # >=4 direction reversals = churn
ERRATIC_VOL = 0.22     # stdev/mean of the odds series


def _norm(s):
    import re, unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9а-я]", "", s.lower())


def _reversals(seq):
    d = [b - a for a, b in zip(seq, seq[1:]) if b != a]
    return sum(1 for a, b in zip(d, d[1:]) if (a > 0) != (b > 0))


def scan():
    rows = [json.loads(l) for l in HIST.read_text(encoding="utf-8").splitlines() if l.strip()]
    m = defaultdict(list)
    import re as _re
    _junk = lambda x: bool(_re.match(r"^\s*\d{1,2}\s*[КKкk]\s*$", str(x or "")))
    for r in rows:
        o1, o2 = r.get("ml1"), r.get("ml2")
        if _junk(r.get("t1")) or _junk(r.get("t2")):
            continue
        if o1 and o2 and 1.01 < o1 < 15 and 1.01 < o2 < 15:
            m[(r.get("t1"), r.get("t2"))].append(r)
    out = []
    for (t1, t2), snaps in m.items():
        snaps.sort(key=lambda r: r.get("ts", ""))
        s1 = [s["ml1"] for s in snaps]
        s2 = [s["ml2"] for s in snaps]
        if len(s1) < 3:
            continue
        open1, close1 = s1[0], s1[-1]
        open2, close2 = s2[0], s2[-1]
        rev = _reversals(s1)
        vol = statistics.pstdev(s1) / (statistics.mean(s1) or 1)
        swing = (max(s1) - min(s1)) / (min(s1) or 1)
        d1 = (close1 - open1) / open1                    # +ve = team1 drifted OUT
        fav_open = t1 if open1 < open2 else t2
        # recent velocity (last few snapshots) — EARLY catch, before the move fully prices in
        rec_side, rec_drift = None, 0.0
        w1, w2 = s1[-4:], s2[-4:]
        if len(w1) >= 3:
            rd1 = (w1[-1] - w1[0]) / (w1[0] or 1)
            steps = [b - a for a, b in zip(w1, w1[1:])]
            mono = all(x <= 0 for x in steps) or all(x >= 0 for x in steps)
            if mono and rd1 <= -0.12:                     # team1 shortening fast, right now
                rec_side, rec_drift = t1, abs(rd1)
            elif mono and rd1 >= 0.12:                    # team1 drifting out => team2 shortening
                rec_side, rec_drift = t2, abs((w2[-1] - w2[0]) / (w2[0] or 1))
        # classify
        typ = side = note = None
        strength = 0.0
        if rev >= ERRATIC_REV or vol >= ERRATIC_VOL:
            typ, strength = "ERRATIC", vol + rev * 0.05
            note = "качели/манипуляция — договорняк-риск, ставить опасно"
        elif d1 <= -STEAM_DRIFT:                          # team1 shortened
            typ, side, strength = "STEAM", t1, abs(d1)
            note = f"деньги на {t1}: {open1}→{close1}"
        elif -d1 <= -STEAM_DRIFT or (close2 - open2) / open2 <= -STEAM_DRIFT:  # team2 shortened
            typ, side, strength = "STEAM", t2, abs((close2 - open2) / open2)
            note = f"деньги на {t2}: {open2}→{close2}"
        elif fav_open == t1 and d1 >= REVERSE_DRIFT:      # opening fav (t1) drifted out -> sharp on t2
            typ, side, strength = "REVERSE", t2, d1
            note = f"фаворит {t1} поплыл {open1}→{close1} → умные на {t2}"
        elif fav_open == t2 and (close2 - open2) / open2 >= REVERSE_DRIFT:
            typ, side, strength = "REVERSE", t1, (close2 - open2) / open2
            note = f"фаворит {t2} поплыл {open2}→{close2} → умные на {t1}"
        elif rec_side and rec_drift >= 0.12:              # EARLY: a move just started, entry still juicy
            typ, side, strength = "EARLY", rec_side, rec_drift
            note = f"движение ТОЛЬКО НАЧАЛОСЬ на {rec_side} — лови коэф рано, пока высокий"
        if typ:
            out.append({"t1": t1, "t2": t2, "type": typ, "side": side, "strength": round(strength, 3),
                        "open": [open1, open2], "close": [close1, close2], "rev": rev,
                        "vol": round(vol, 3), "swing": round(swing, 2), "snaps": len(s1), "note": note})
    out.sort(key=lambda x: -x["strength"])
    return out


ROOT = Path(__file__).resolve().parent
BOARD = ROOT / "data" / "winline_odds.json"
STEAM_LOG = ROOT / "data" / "line_steam_log.jsonl"
SEEN = ROOT / "data" / "line_steam_seen.json"
MIN_STRENGTH = 0.20    # only a real move
MIN_ENTRY = 1.35       # the steamed side must still pay >=1.35 (else move is over, no value left)
NEWS = True            # live-search news (grok + gemini) on each fresh steam to confirm side / spot a fix


def _news(t1, t2) -> str:
    """Live-search news via grok AND gemini — did the line move for a real reason (roster/stand-in/
    scandal), or is it pure insider (a stronger fix signal)? Two independent searches cross-check."""
    import analyst
    q = (f"Live-search news RIGHT NOW for the upcoming CS2 match {t1} vs {t2}. Any roster change, "
         f"stand-in, injury, visa/travel issue, dead-rubber motivation, or match-fixing / suspicious "
         f"betting chatter? Reply ONE short line: 'RED: <why>' if a red flag exists, else 'GREEN: clean'.")
    outs = []
    for w in ("grok", "gemini-cli"):
        try:
            r = (analyst.call_omni(w, q, max_tokens=120) or "").strip().replace("\n", " ")
            outs.append(f"{w.split('-')[0]}: {r[:130]}")
        except Exception as e:  # noqa: BLE001
            outs.append(f"{w.split('-')[0]}: (fail {type(e).__name__})")
    return "  |  ".join(outs)


def live():
    """Alert FRESH steams on matches still on the winline board (bettable now), where the steamed
    side still offers value (entry >= MIN_ENTRY). Logs for a real win-rate track + DMs Telegram."""
    import monitor as mon
    res = scan()
    try:
        board = {frozenset((_norm(r.get("t1")), _norm(r.get("t2"))))
                 for r in json.loads(BOARD.read_text(encoding="utf-8"))}
    except Exception:
        board = set()
    try:
        seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}
    except Exception:
        seen = {}
    from datetime import datetime, timezone
    fired = 0
    for r in res:
        thr = 0.12 if r["type"] == "EARLY" else MIN_STRENGTH
        if r["type"] not in ("STEAM", "EARLY") or r["strength"] < thr or not r["side"]:
            continue
        pair = frozenset((_norm(r["t1"]), _norm(r["t2"])))
        if pair not in board:                       # match gone / already started
            continue
        entry = r["close"][0] if _norm(r["side"]) == _norm(r["t1"]) else r["close"][1]
        if entry < MIN_ENTRY:                        # move fully priced, no value left
            continue
        key = f"{_norm(r['t1'])}|{_norm(r['t2'])}|{_norm(r['side'])}"
        if key in seen:
            continue
        openv = r["open"][0] if _norm(r["side"]) == _norm(r["t1"]) else r["open"][1]
        news = _news(r["t1"], r["t2"]) if NEWS else ""
        tag = "🚀 <b>CS2 РАННИЙ СТИМ</b>" if r["type"] == "EARLY" else "🔥 <b>CS2 СТИМ</b>"
        msg = (f"{tag} (движение линии) — {r['t1']} vs {r['t2']}\n"
               f"   умные деньги на <b>{r['side']}</b>: {openv} → <b>{entry}</b> "
               f"(сила {r['strength']*100:.0f}%, {r['snaps']} снимков, реверсов {r['rev']})\n"
               f"   → лови: ставь <b>{r['side']} @ {entry}</b> пока не схлопнулось")
        if news:
            msg += f"\n   📰 {news}"
        mon.telegram_send(msg)
        with STEAM_LOG.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"t1": r["t1"], "t2": r["t2"], "side": r["side"], "entry": entry,
                                "open": openv, "strength": r["strength"], "snaps": r["snaps"],
                                "mode": r["type"], "news": news,
                                "logged_at": datetime.now(timezone.utc).isoformat()},
                               ensure_ascii=False) + "\n")
        seen[key] = datetime.now(timezone.utc).isoformat()
        fired += 1
    SEEN.write_text(json.dumps(seen), encoding="utf-8")
    print(f"live steam scan: {fired} fresh actionable steams")


def movement_map():
    """{frozenset(norm t1, norm t2): {side,type,strength}} for directional moves (STEAM/EARLY/REVERSE),
    so other tools can cross-check a model pick against where the market money actually went."""
    out = {}
    for r in scan():
        if r["type"] in ("STEAM", "EARLY", "REVERSE") and r["side"]:
            out[frozenset((_norm(r["t1"]), _norm(r["t2"])))] = {
                "side": r["side"], "type": r["type"], "strength": r["strength"]}
    return out


if __name__ == "__main__":
    if "--live" in sys.argv:
        live(); sys.exit()
    n = 15
    if "--top" in sys.argv:
        n = int(sys.argv[sys.argv.index("--top") + 1])
    res = scan()
    byt = defaultdict(int)
    for r in res:
        byt[r["type"]] += 1
    print(f"scanned history -> {len(res)} anomalies  {dict(byt)}\n")
    for r in res[:n]:
        tag = {"STEAM": "🔥 СТИМ", "REVERSE": "↩️ РЕВЕРС", "ERRATIC": "⚠️ КАЧЕЛИ"}[r["type"]]
        bet = f" → ставь {r['side']}" if r["side"] else ""
        print(f"{tag}  {r['t1']} vs {r['t2']}  (сила {r['strength']}, {r['snaps']} снимков, реверсов {r['rev']})")
        print(f"     откр {r['open']} → тек {r['close']}  |  {r['note']}{bet}")
