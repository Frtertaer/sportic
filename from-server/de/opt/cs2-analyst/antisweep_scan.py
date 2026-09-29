#!/usr/bin/env python3
"""CS2 ANTI-SWEEP scanner — find the REAL winline "Фора карт +1.5" кол on "team wins >=1 map" and
bet it ONLY where the MODEL's chance is HIGHER than the book implies (model_P > 1/кол = value).
Checks BOTH teams' +1.5 side and takes the best-value one (near-lock favourite OR under-priced dog).
P(win>=1 map) comes from the veto-MC score_dist (accounts for veto correlation)."""
from __future__ import annotations
import json, sys
from pathlib import Path
from datetime import datetime, timezone, timedelta

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3      # noqa: E402
import veto_sim as vs         # noqa: E402
import winline_maps           # noqa: E402
import monitor as mon         # noqa: E402
import cs2_params             # noqa: E402

FT_LOG = ROOT / "data" / "antisweep_ft_log.jsonl"
SEEN = ROOT / "data" / "antisweep_seen.json"
HOURS_AHEAD = 10
MIN_P_ANTISWEEP = 0.72      # floor: at least one team is a near-anti-sweep
MAX_SCRAPE = 6
MIN_RATED = 3


def _norm(s: str) -> str:
    return "".join(c for c in (s or "").upper() if c.isalnum())


def _match(a: str, b: str) -> bool:
    a, b = _norm(a), _norm(b)
    return bool(a and b and (a in b or b in a or (len(a) >= 4 and a[:5] == b[:5])))


def scan(dry: bool = False) -> None:
    min_edge = cs2_params.get("antisweep_min_edge", 0.06)
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

    cands = []
    for m in ups:
        t1, t2, bo, slug = m.get("team1_id"), m.get("team2_id"), m.get("bo_type") or 3, m.get("slug")
        if not (t1 and t2 and slug) or bo < 3 or slug in seen:
            continue
        try:
            if datetime.fromisoformat(m["start_date"].replace("Z", "+00:00")) > horizon:
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
        anti = {n1: round(1 - pred["score_dist"].get("0-2", 0.0), 3),   # team1 wins >=1 map
                n2: round(1 - pred["score_dist"].get("2-0", 0.0), 3)}
        if max(anti.values()) >= MIN_P_ANTISWEEP:
            cands.append({"slug": slug, "n1": n1, "n2": n2, "anti": anti, "p_anti": max(anti.values()), "start_date": m.get("start_date")})
    cands.sort(key=lambda c: -c["p_anti"])
    cands = cands[:MAX_SCRAPE]
    print(f"anti-sweep candidates: {len(cands)}")
    if not cands:
        return

    fk = winline_maps.scrape_map_handicap([(c["n1"], c["n2"]) for c in cands])
    for c in cands:
        book = fk.get((c["n1"], c["n2"])) or fk.get((c["n2"], c["n1"]))
        if not book:
            continue
        # evaluate BOTH sides that carry a +1.5, bet where MODEL prob > book implied
        best = None
        for team, sides in book.items():
            odd = sides.get("+1.5")
            if not odd:
                continue
            p = next((v for k, v in c["anti"].items() if _match(k, team)), None)
            if p is None:
                continue
            edge = p * odd - 1                       # >0  <=>  model_P > 1/odd (book implied)
            if best is None or edge > best[3]:
                best = (team, odd, p, edge)
        if not best:
            continue
        team, odd, p, edge = best
        implied = 100 / odd
        if edge < min_edge:
            print(f"  {team} +1.5 @ {odd}: модель {p*100:.0f}% vs бук {implied:.0f}% → край {edge*100:+.0f}% < порог")
            seen[c["slug"]] = "novalue"
            continue
        msg = (f"🛡 <b>CS2 АНТИ-СВИП value</b> — {c['n1']} vs {c['n2']}\n"
               f"   <b>{team} возьмёт ≥1 карту</b>: модель <b>{p*100:.0f}%</b> > бук {implied:.0f}% (кол {odd})\n"
               f"   → ставь {team} +1.5 карт @ <b>{odd}</b> · край <b>+{edge*100:.0f}%</b>")
        print("-" * 50); print(msg.replace("<b>", "").replace("</b>", ""))
        if not dry:
            mon.telegram_send(msg)
            with FT_LOG.open("a", encoding="utf-8") as f:
                f.write(json.dumps({"slug": c["slug"], "t1": c["n1"], "t2": c["n2"], "bet_team": team,
                                    "market": "map+1.5", "p_anti": p, "odd": odd, "edge": round(edge, 3),
                                    "start_date": c.get("start_date"),
                                    "logged_at": datetime.now(timezone.utc).isoformat()},
                                   ensure_ascii=False) + "\n")
        seen[c["slug"]] = datetime.now(timezone.utc).isoformat()
    if not dry:
        SEEN.write_text(json.dumps(seen), encoding="utf-8")


if __name__ == "__main__":
    scan(dry="--dry" in sys.argv)
