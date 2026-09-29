#!/usr/bin/env python3
"""CS2 SELF-LEARNING loop (Hermes-style, ported to betting). Periodically re-derives the optimal
tunable params from the RESOLVED bet history, backtests proposed-vs-current, APPLIES only a clearly
better setting (enough sample + margin + safe bounds), and REFLECTS every finding into the Hermes
learn-svc (:8909) as a recallable skill so a curator can consolidate CS2+football lessons. This
automates exactly the manual tuning we did (map calibration, totals MIN_EDGE, series gate) and keeps
improving as data grows. Fail-safe: bad config -> modules fall back to defaults; nothing is deleted."""
from __future__ import annotations
import json, sys, urllib.request
from pathlib import Path
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import cs2_params            # noqa: E402
import monitor as mon        # noqa: E402

LEARN = "http://127.0.0.1:8909"
DATA = ROOT / "data"


def _read(name: str) -> list[dict]:
    p = DATA / name
    if not p.exists():
        return []
    return [json.loads(l) for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]


def _reflect(task: str, outcome: str, success: bool) -> None:
    try:
        body = json.dumps({"task": task, "outcome": outcome, "success": success,
                           "model": "claude-code", "tags": ["cs2", "betting", "autotune"]}).encode()
        req = urllib.request.Request(LEARN + "/reflect", data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=20).read()
    except Exception as e:  # noqa: BLE001 — learning is best-effort, never blocks tuning
        print("  reflect failed:", type(e).__name__)


# ---------------------------------------------------------------- learners ----
def learn_totals_min_edge() -> dict | None:
    res = [r for r in _read("totals_ft_results.jsonl")
           if r.get("outcome") in ("win", "loss") and isinstance(r.get("edge"), (int, float))]
    if len(res) < 25:
        return {"skip": f"totals n={len(res)}<25"}
    def roi(th):
        g = [r for r in res if r["edge"] >= th]
        if len(g) < 12:
            return None, 0
        pl = sum((r["odd"] - 1) if r["outcome"] == "win" else -1 for r in g)
        return pl / len(g), len(g)
    cur = cs2_params.get("totals_min_edge", 0.06)
    best, best_roi, best_n = cur, roi(cur)[0] or -9, roi(cur)[1]
    for th in (0.04, 0.05, 0.06, 0.07, 0.08):
        r, n = roi(th)
        if r is not None and r > best_roi + 0.03 and n >= 12:   # +3pp ROI margin
            best, best_roi, best_n = th, r, n
    if best != cur and abs(best - cur) <= 0.03:                  # safe step
        return {"key": "totals_min_edge", "old": cur, "new": round(best, 3),
                "why": f"ROI {best_roi*100:+.0f}% at edge>={best*100:.0f}% (n={best_n}) beats current"}
    return {"noop": f"totals_min_edge stays {cur} (best backtest {best_roi*100:+.0f}%)"}


def learn_map_calibration() -> dict | None:
    recs = [(r.get("model_p_raw", r["model_p"]), 1 if r["outcome"] == "win" else 0)
            for r in _read("live_picks_results.jsonl")
            if r.get("market") == "map" and r.get("outcome") in ("win", "loss")
            and isinstance(r.get("model_p_raw", r.get("model_p")), (int, float))]
    if len(recs) < 120:
        return {"skip": f"map n={len(recs)}<120"}
    def brier(sh, cap):
        def f(p):
            return 0.5 + max(-cap, min(cap, (p - 0.5) * sh))
        return sum((f(p) - o) ** 2 for p, o in recs) / len(recs)
    cur_sh, cur_cap = cs2_params.get("map_calib_shrink", 0.6), cs2_params.get("map_calib_cap", 0.10)
    cur_b = brier(cur_sh, cur_cap)
    best = (cur_sh, cur_cap, cur_b)
    for sh in (0.5, 0.55, 0.6, 0.7, 0.85, 1.0):
        for cap in (0.08, 0.10, 0.12, 0.15, 0.20):
            b = brier(sh, cap)
            if b < best[2] - 0.002:                              # meaningful Brier gain
                best = (sh, cap, b)
    if (best[0], best[1]) != (cur_sh, cur_cap):
        return {"multi": [("map_calib_shrink", cur_sh, best[0]), ("map_calib_cap", cur_cap, best[1])],
                "why": f"Brier {cur_b:.3f}->{best[2]:.3f} (n={len(recs)})"}
    return {"noop": f"map calib stays shrink={cur_sh} cap={cur_cap} (Brier {cur_b:.3f})"}


def learn_series_gate() -> dict | None:
    recs = [(r["model_p"], 1 if r["outcome"] == "win" else 0)
            for r in _read("live_picks_results.jsonl")
            if r.get("market") == "series" and r.get("outcome") in ("win", "loss")
            and isinstance(r.get("model_p"), (int, float))]
    if len(recs) < 100:
        return {"skip": f"series n={len(recs)}<100"}
    def wr(floor):
        g = [o for p, o in recs if p >= floor]
        return (sum(g) / len(g), len(g)) if len(g) >= 30 else (None, 0)
    cur = cs2_params.get("series_gate_p", 0.53)
    base_w, base_n = wr(cur)
    best, best_w = cur, base_w or 0
    for fl in (0.50, 0.52, 0.53, 0.55, 0.57):
        w, n = wr(fl)
        if w is not None and w > best_w + 0.03 and n >= 30:
            best, best_w = fl, w
    if best != cur and abs(best - cur) <= 0.04:
        return {"key": "series_gate_p", "old": cur, "new": round(best, 3),
                "why": f"winrate {best_w*100:.0f}% at p>={best} beats current"}
    return {"noop": f"series_gate_p stays {cur}"}


def run(dry: bool = False) -> None:
    applied, notes = [], []
    for name, fn in [("totals MIN_EDGE", learn_totals_min_edge),
                     ("map calibration", learn_map_calibration),
                     ("series gate", learn_series_gate)]:
        try:
            r = fn()
        except Exception as e:  # noqa: BLE001
            print(f"{name}: ERROR {e}"); continue
        if not r:
            continue
        if r.get("skip") or r.get("noop"):
            print(f"{name}: {r.get('skip') or r.get('noop')}")
            notes.append(r.get("skip") or r.get("noop"))
            continue
        # apply
        changes = r.get("multi") or [(r["key"], r["old"], r["new"])]
        desc = " ".join(f"{k} {o}->{n}" for k, o, n in changes) + f" | {r['why']}"
        print(f"{name}: LEARN {desc}")
        if not dry:
            for k, o, n in changes:
                cs2_params.set_param(k, n, reason=r["why"])
            _reflect(f"Auto-tune CS2 {name}", f"APPLIED {desc}", True)
            mon.telegram_send(f"🧠 <b>CS2 самообучение</b>: {desc}")
        applied.append(desc)
    if not dry and notes:
        _reflect("CS2 autotune audit", "no change: " + " ; ".join(notes[:4]), True)
    print(f"\nautolearn done — applied {len(applied)} change(s)")


if __name__ == "__main__":
    run(dry="--dry" in sys.argv)
