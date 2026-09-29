#!/usr/bin/env python3
"""CS2 PRE-MATCH anomaly / fixed-match scanner (council-unanimous #1 signal, 2026-07).

Hours before start, for each upcoming CS2 match it de-vigs the winline ML, compares to the
veto-MC model, and classifies:
  • SUSPICIOUS/FIX  -> report + SKIP (extreme fav mismatch, underdog-crush, huge divergence, weird overround)
  • VALUE (BET)     -> model side, only on MODERATE divergence (edge 7-15%), clean
  • ANTI-SWEEP lock -> a team's P(win >=1 map) >= 0.90 (from score_dist) -> +1.5 map handicap near-lock
For flagged matches it live-searches news via grok (roster/stand-in/injury/motivation) and downgrades
to SKIP on a red flag. Alerts Telegram + logs BET picks to prematch_ft_log.jsonl for a real win-rate track.
Tier-2/3 CS2 is heavily fixed; honest pre-match ceiling on one book ~54-63%. This scanner's job is mostly
to SKIP the ~30% worst matches and surface the strange ones."""
from __future__ import annotations
import json, sys, re
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3       # noqa: E402
import veto_sim as vs          # noqa: E402
import winline_odds            # noqa: E402
import monitor as mon          # noqa: E402
import analyst                 # noqa: E402  (grok live-search news)

FT_LOG = ROOT / "data" / "prematch_ft_log.jsonl"
SEEN = ROOT / "data" / "prematch_seen.json"
HOURS_AHEAD = 8           # scan matches starting within this window
NEWS = True               # query grok live-search for flagged matches

# --- council-consensus thresholds ---
EDGE_BET_LO, EDGE_BET_HI = 0.07, 0.15     # moderate genuine value
EDGE_SUCKER = 0.18                          # above this you're the sucker / fix
OV_LO, OV_HI = 0.02, 0.12                  # normal overround band
FAV_MISMATCH_BOOK, FAV_MISMATCH_MODEL = 0.85, 0.65   # extreme fav mismatch
DOG_CRUSH_BOOK, DOG_CRUSH_MODEL = 0.18, 0.35         # underdog-crush fix pattern
ANTISWEEP_P = 0.90                          # P(win>=1 map) near-lock
MIN_RATED = 3


def _news_flag(n1: str, n2: str) -> tuple[str, str]:
    """Return RED/GREEN only for a strict provider answer; failures stay UNKNOWN."""
    q = (f"Live-search RIGHT NOW for the upcoming CS2 match {n1} vs {n2}. Any of: roster change, "
         f"stand-in/substitute, player injury/illness/visa/DNP, or motivation issue (dead rubber, "
         f"already qualified/eliminated, disband, unpaid)? Reply STRICTLY: 'RED: <reason>' if any such "
         f"red flag exists for either team, else 'GREEN: clean'. One line.")
    import time as _t
    for attempt in range(2):                 # grok worker is occasionally flaky (500) -> retry once
        try:
            r = analyst._strip_grok(analyst.call_omni("grok", q, max_tokens=120))
            match = re.match(r"^(RED|GREEN)\s*:?\s*(.*)$", r, flags=re.IGNORECASE)
            flag = match.group(1).upper() if match else "UNKNOWN"
            reason = (match.group(2) if match else r).strip(" -:.")
            if not reason:
                reason = "clean" if flag == "GREEN" else ("risk found" if flag == "RED" else "unrecognized response")
            return flag, reason[:180 if flag == "RED" else 120]
        except Exception as e:  # noqa: BLE001
            if attempt == 0:
                _t.sleep(3); continue
            return "UNKNOWN", f"news unavailable: {type(e).__name__}"


def scan(dry: bool = False, do_news: bool = NEWS) -> None:
    try:
        r = bo3._get("/matches", {"filter[matches.status][eq]": "upcoming",
                                  "filter[matches.discipline_id][eq]": 1,
                                  "page[limit]": 40, "sort": "start_date"})
        ups = r.get("results", r) or []
    except Exception as e:  # noqa: BLE001
        print("bo3 upcoming failed:", e); return
    try:
        seen = json.loads(SEEN.read_text(encoding="utf-8")) if SEEN.exists() else {}
    except Exception:  # noqa: BLE001
        seen = {}
    horizon = datetime.now(timezone.utc) + timedelta(hours=HOURS_AHEAD)

    reports = []
    for m in ups:
        t1, t2, bo = m.get("team1_id"), m.get("team2_id"), m.get("bo_type") or 3
        slug = m.get("slug")
        if not (t1 and t2 and slug) or bo < 3:
            continue
        try:
            sd = datetime.fromisoformat(m["start_date"].replace("Z", "+00:00"))
            if sd > horizon:
                continue
        except Exception:  # noqa: BLE001
            pass
        try:
            pred = vs.predict(t1, t2, bo)
        except Exception:  # noqa: BLE001
            continue
        if pred.get("rated1", 0) < MIN_RATED or pred.get("rated2", 0) < MIN_RATED:
            continue
        n1, n2 = pred["team1"], pred["team2"]
        wl = winline_odds.lookup(n1, n2)
        if not wl:
            continue
        ml1, ml2 = wl["odd1"], wl["odd2"]
        if not (ml1 and ml2 and ml1 > 1 and ml2 > 1):
            continue
        q1, q2 = 1 / ml1, 1 / ml2
        ov = q1 + q2 - 1
        pf1, pf2 = q1 / (q1 + q2), q2 / (q1 + q2)
        pm1, pm2 = pred["p1_win"], pred["p2_win"]
        edge1 = pm1 - pf1
        # book favourite
        if pf1 >= pf2:
            pf_fav, pm_fav, pf_dog, pm_dog = pf1, pm1, pf2, pm2
        else:
            pf_fav, pm_fav, pf_dog, pm_dog = pf2, pm2, pf1, pm1
        # anti-sweep (from MC score_dist)
        sweep1 = pred["score_dist"].get("0-2", 0.0)   # team1 won 0 maps
        sweep2 = pred["score_dist"].get("2-0", 0.0)   # team2 won 0 maps
        anti = [(n1, 1 - sweep1), (n2, 1 - sweep2)]

        verdict, reason, action = "PASS", "", ""
        bet_side = bet_odd = None
        if ov < OV_LO or ov > OV_HI:
            verdict, reason = "SUSPICIOUS", f"странный overround {ov*100:.0f}% (бук неуверен/лимит)"
        elif pf_fav >= FAV_MISMATCH_BOOK and pm_fav <= FAV_MISMATCH_MODEL:
            verdict, reason = "SUSPICIOUS", f"бук: фаворит {pf_fav*100:.0f}%, модель лишь {pm_fav*100:.0f}% (инсайд/лик — ты жертва)"
        elif pf_dog <= DOG_CRUSH_BOOK and pm_dog >= DOG_CRUSH_MODEL:
            verdict, reason = "SUSPICIOUS", f"удавка андердога: бук {pf_dog*100:.0f}%, модель {pm_dog*100:.0f}% (классика фикса)"
        elif abs(edge1) > EDGE_SUCKER:
            verdict, reason = "SUSPICIOUS", f"огромный край {abs(edge1)*100:.0f}% (модель≠бук — вероятно ты жертва)"
        elif EDGE_BET_LO <= abs(edge1) <= EDGE_BET_HI:
            side, po, pe = (n1, ml1, pf1) if edge1 > 0 else (n2, ml2, pf2)
            if 0.25 <= pe <= 0.78:
                verdict = "VALUE"
                bet_side, bet_odd = side, po
                reason = f"край +{abs(edge1)*100:.0f}% модель-сторона"
                action = f"BET {side} @ {po} (модель {(pm1 if edge1>0 else pm2)*100:.0f}% vs бук {pe*100:.0f}%)"
        # anti-sweep is an ADDITIONAL note
        aslock = max(anti, key=lambda x: x[1])
        as_note = (f"  🛡 анти-свип: {aslock[0]} берёт ≥1 карту {aslock[1]*100:.0f}% "
                   f"(фора +1.5 карты — near-lock, проверь линию вручную)") if aslock[1] >= ANTISWEEP_P else ""

        key = f"{slug}"
        if key in seen:
            continue
        if verdict == "PASS" and not as_note:
            continue

        # news check for flagged/actionable matches
        news = ""
        news_flag, news_reason = "UNKNOWN", ""
        if do_news and (verdict in ("VALUE", "SUSPICIOUS") or as_note):
            flag, why = _news_flag(n1, n2)
            news_flag, news_reason = flag, why
            news = f"  📰 {flag}: {why}"
            if flag == "RED":
                if verdict == "VALUE":
                    action = "SKIP (новостной ред-флаг перебивает край)"
                verdict = "SUSPICIOUS" if verdict == "VALUE" else verdict

        icon = {"VALUE": "✅", "SUSPICIOUS": "⚠️", "PASS": "🔎"}[verdict]
        msg = (f"{icon} <b>{n1} vs {n2}</b> (Bo{bo})\n"
               f"   {reason}\n"
               f"   бук: {n1} {ml1} / {n2} {ml2}  |  модель серия: {pm1*100:.0f}% / {pm2*100:.0f}%")
        if action:
            msg += f"\n   → <b>{action}</b>"
        if as_note:
            msg += f"\n{as_note}"
        if news:
            msg += f"\n{news}"
        reports.append((verdict, msg, {
            "slug": slug, "t1": n1, "t2": n2, "bo": bo, "ml1": ml1, "ml2": ml2,
            "pm1": round(pm1, 3), "pm2": round(pm2, 3), "edge1": round(edge1, 3),
            "verdict": verdict, "action": action, "bet_side": bet_side, "bet_odd": bet_odd,
            "news_flag": news_flag, "news_reason": news_reason,
            "antisweep": [aslock[0], round(aslock[1], 3)]}))
        seen[key] = datetime.now(timezone.utc).isoformat()

    # emit: VALUE + SUSPICIOUS first
    reports.sort(key=lambda x: {"VALUE": 0, "SUSPICIOUS": 1, "PASS": 2}[x[0]])
    print(f"scanned {len(ups)} upcoming, flagged {len(reports)}")
    for verdict, msg, rec in reports:
        print("-" * 50); print(msg.replace("<b>", "").replace("</b>", ""))
        if not dry:
            if verdict in ("VALUE", "SUSPICIOUS"):
                mon.telegram_send("🔮 <b>CS2 пре-матч скан</b>\n" + msg)
            if verdict == "VALUE" and "BET" in rec.get("action", ""):
                rec["logged_at"] = datetime.now(timezone.utc).isoformat()
                with FT_LOG.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    if not dry:
        SEEN.write_text(json.dumps(seen), encoding="utf-8")


if __name__ == "__main__":
    scan(dry="--dry" in sys.argv, do_news=("--nonews" not in sys.argv))
