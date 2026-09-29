#!/usr/bin/env python3
"""CS2 ROUND-TOTALS value monitor: compare winline's per-map round-total line+odds to our
CALIBRATED empirical base-rate (round_totals.avg_over) and DM the user when the book's
price drifts above our fair odds (real, honest value — not a model fantasy).

For each scraped total (line L, over_odd, under_odd): our P(over L) = pooled empirical
over-rate. edge_over = P*over_odd-1, edge_under = (1-P)*under_odd-1. If max >= MIN_EDGE,
push the pick. Dedups by (match, line, side). The map is unknown pre-match but all maps
average ~21.4 rounds, so the pooled base-rate is the right reference.

  python cs2_totals_monitor.py [--dry]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import monitor as mon  # noqa: E402  (telegram_send)
import round_totals as rtot  # noqa: E402

TOTALS = ROOT / "data" / "winline_totals.json"
STATE = ROOT / "data" / "totals_seen.json"
FT_LOG = ROOT / "data" / "totals_ft_log.jsonl"
MIN_EDGE = 0.04
MAX_EDGE = 0.12          # cap: bigger "edge" = the book's NON-standard line encodes a
                         # matchup read our base-rate is blind to (blowout/long map) — skip.
NEUTRAL = (21.0, 22.0)   # only trust the book's NEUTRAL main line (~21.5); off it the book
                         # is signalling a closeness lean we can't refute.


def _load(p: Path) -> dict | list:
    if p.exists():
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            return {} if p == STATE else []
    return {} if p == STATE else []


def _log_ft(rec: dict) -> None:
    from datetime import datetime, timezone
    rec["logged_at"] = datetime.now(timezone.utc).isoformat()
    with FT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def run(dry: bool) -> None:
    rows = _load(TOTALS)
    seen = _load(STATE)
    print(f"matches with totals: {len(rows)}")
    pushed = 0
    for r in rows:
        t1, t2 = r.get("t1"), r.get("t2")
        # only the NEUTRAL main line per match (closest to 21.5 within the neutral band)
        cands = [tl for tl in (r.get("totals") or []) if NEUTRAL[0] <= tl["line"] <= NEUTRAL[1]]
        if not cands:
            continue
        tl = min(cands, key=lambda x: abs(x["line"] - 21.5))
        line, over, under = tl["line"], tl["over"], tl["under"]
        p_over = rtot.avg_over(line)
        if p_over is None:
            continue
        e_over = p_over * over - 1
        e_under = (1 - p_over) * under - 1
        side, edge, odd, p = (("ТБ", e_over, over, p_over) if e_over >= e_under
                              else ("ТМ", e_under, under, 1 - p_over))
        if not (MIN_EDGE <= edge <= MAX_EDGE):
            continue
        key = f"{t1}|{t2}|{line}|{side}"
        if seen.get(key):
            continue
        msg = (f"🎲 <b>CS2 тотал раундов — value</b>\n"
               f"{t1} vs {t2} (карта {tl['map_no'] or '1'})\n"
               f"📊 наш расчёт: P(тотал {side} {line}) = {p*100:.0f}% "
               f"(база ~9.5к карт)\n"
               f"🏦 winline: {side} {line} @ <b>{odd}</b>  | наш справедл. {round(1/p,2)}\n"
               f"💎 край <b>+{edge*100:.0f}%</b> → ставь {side} {line} @ {odd}\n"
               f"<i>калибр. тотал-модель; только нейтральная линия ~21.5, край ≤12% (большой = бук знает про матч больше)</i>")
        if dry:
            print("=" * 50 + "\n" + msg)
            seen[key] = True
        else:
            if mon.telegram_send(msg):
                seen[key] = True
                pushed += 1
                _log_ft({"t1": t1, "t2": t2, "map_no": tl["map_no"], "line": line,
                         "side": side, "odd": odd, "model_p": round(p, 3), "edge": round(edge, 3)})
                print(f"  pushed {key} edge +{edge*100:.0f}%")
    if not dry:
        STATE.write_text(json.dumps(seen), encoding="utf-8")
    print(f"done. pushed={pushed}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry", action="store_true")
    run(ap.parse_args().dry)


if __name__ == "__main__":
    main()
