#!/usr/bin/env python3
"""Grade board VALUE/anti-sweep picks (prematch_board_log.jsonl) vs bo3 finished results ->
prematch_board_results.jsonl + running win-rate. ml pick wins if the side wins the series; antisweep
wins if the side takes >=1 map. bo3 = partial coverage (misses some winline-only tournaments)."""
from __future__ import annotations
import json, sys, unicodedata
from pathlib import Path
from datetime import datetime, timezone
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3      # noqa: E402
import veto_sim as vs         # noqa: E402
import gcs_lookup             # noqa: E402
LOG = ROOT / "data" / "prematch_board_log.jsonl"
RES = ROOT / "data" / "prematch_board_results.jsonl"


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s if c.isalnum()).upper()


def _read(p):
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()] if p.exists() else []


def _finished():
    try:
        return bo3._get("/matches", {"filter[matches.status][eq]": "finished",
                                     "filter[matches.discipline_id][eq]": 1,
                                     "page[limit]": 100, "sort": "-start_date"}).get("results", [])
    except Exception as e:  # noqa: BLE001
        print("bo3 fail:", e); return []


def _summary():
    res = _read(RES)
    if not res:
        print("board track: 0 resolved yet"); return
    for mk in ("ml", "antisweep"):
        rr = [r for r in res if r.get("market") == mk]
        if rr:
            w = sum(1 for r in rr if r["outcome"] == "win")
            print(f"  {mk:9}: {w}/{len(rr)} = {100*w/len(rr):.0f}% winrate")


def run():
    bets = _read(LOG)
    done = {f"{r['t1']}|{r['t2']}|{r['side']}|{r.get('logged_at')}" for r in _read(RES)}
    pending = [b for b in bets if f"{b['t1']}|{b['t2']}|{b['side']}|{b.get('logged_at')}" not in done]
    if not pending:
        print("no pending board picks"); _summary(); return
    gcs_res = gcs_lookup.load()
    fin = _finished(); conn = vs._conn(); names = {}

    def nm(t):
        if t not in names:
            try:
                names[t] = vs.team_name(conn, t)
            except Exception:  # noqa: BLE001
                names[t] = ""
        return names[t]

    out = []
    for b in pending:
        b1, b2, side = _norm(b["t1"]), _norm(b["t2"]), _norm(b["side"])
        g = gcs_lookup.find(b["t1"], b["t2"], gcs_res)
        if g:
            side_is_g1 = bool(side and (side in g["g1n"] or g["g1n"] in side))
            side_score = g["s1"] if side_is_g1 else g["s2"]
            won = (side_score >= 1) if b["market"] == "antisweep" else bool(side and (side in g["winner_n"] or g["winner_n"] in side))
            rec = dict(b); rec["outcome"] = "win" if won else "loss"
            rec["series_final"] = f'{g["s1"]}-{g["s2"]}'; rec["source"] = "gcs"
            rec["pl_units"] = (round((b["odd"] - 1) if won else -1.0, 2) if b["market"] == "ml" and b.get("odd") else None)
            rec["settled_at"] = datetime.now(timezone.utc).isoformat(); out.append(rec); continue
        match = None
        for m in fin:
            n1, n2 = _norm(nm(m.get("team1_id"))), _norm(nm(m.get("team2_id")))
            if not (n1 and n2):
                continue
            if (any(x and (x in n1 or n1 in x) for x in (b1, b2)) and
                    any(x and (x in n2 or n2 in x) for x in (b1, b2))):
                match = m; break
        if not match:
            continue
        s1, s2 = int(match.get("team1_score") or 0), int(match.get("team2_score") or 0)
        win = _norm(nm(match.get("winner_team_id")))
        nt1 = _norm(nm(match.get("team1_id")))
        side_is_t1 = bool(side and (side in nt1 or nt1 in side))
        side_score = s1 if side_is_t1 else s2
        won = (side_score >= 1) if b["market"] == "antisweep" else bool(win and (side in win or win in side))
        rec = dict(b)
        rec["outcome"] = "win" if won else "loss"
        rec["series_final"] = f"{s1}-{s2}"
        rec["pl_units"] = (round((b["odd"] - 1) if won else -1.0, 2)
                           if b["market"] == "ml" and b.get("odd") else None)
        rec["settled_at"] = datetime.now(timezone.utc).isoformat()
        out.append(rec)
    conn.close()
    if out:
        with RES.open("a", encoding="utf-8") as f:
            for r in out:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"graded {len(out)} board picks (pending was {len(pending)})")
    _summary()


if __name__ == "__main__":
    run()
