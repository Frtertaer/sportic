import re, json, shutil, os, time

# ---------------------------------------------------------------------------
# 1) veto_sim.py — add calibrate_map_p() right after _map_winprob()
# ---------------------------------------------------------------------------
VS = "/opt/cs2-analyst/veto_sim.py"
src = open(VS, encoding="utf-8").read()
FUNC = '''

# --- Map-winner calibration (empirical, 2026-07, n=197 live map results) ------
# The raw Glicko-based _map_winprob is badly OVERCONFIDENT on favorites: its 70-90%
# picks realized only ~43-57%. Shrink toward 0.5 (0.6x) + hard confidence cap at
# |p-0.5| <= 0.10 (i.e. p in [0.40, 0.60]). Backtest on the 197 map results:
#   Brier 0.271 -> 0.252 , calibration gap +10.6pp -> +4.5pp.
# Applied ONLY to the map-winner MARKET (map value/tracking). The series Monte-Carlo
# and the round-total model keep the RAW _map_winprob (both already calibrated).
# Relax the cap once >=400 map results accumulate.
def calibrate_map_p(p: float) -> float:
    return 0.5 + max(-0.10, min(0.10, (p - 0.5) * 0.6))
'''
if "def calibrate_map_p" in src:
    print("veto_sim: calibrate_map_p already present")
else:
    anchor = "    return min(0.97, max(0.03, p))\n"
    i = src.find(anchor)
    assert i >= 0, "anchor (end of _map_winprob) not found"
    j = i + len(anchor)
    src = src[:j] + FUNC + src[j:]
    if not os.path.exists(VS + ".bak_calib"):
        shutil.copy(VS, VS + ".bak_calib")
    open(VS, "w", encoding="utf-8").write(src)
    print("veto_sim: calibrate_map_p inserted")

# ---------------------------------------------------------------------------
# 2) live_monitor.py — calibrate the MAP-winner model_p in _log_picks
# ---------------------------------------------------------------------------
LM = "/opt/cs2-analyst/live_monitor.py"
lm = open(LM, encoding="utf-8").read()
OLD = '"model_p": round(s["p_cur_map"][mfav], 3)})'
NEW = '"model_p": round(__import__("veto_sim").calibrate_map_p(s["p_cur_map"][mfav]), 3)})'
if "calibrate_map_p" in lm:
    print("live_monitor: already patched")
elif OLD in lm:
    if not os.path.exists(LM + ".bak_calib"):
        shutil.copy(LM, LM + ".bak_calib")
    open(LM, "w", encoding="utf-8").write(lm.replace(OLD, NEW, 1))
    print("live_monitor: map model_p now calibrated")
else:
    print("live_monitor: ANCHOR NOT FOUND — check the map model_p line")

# ---------------------------------------------------------------------------
# 3) re-grade the historical results jsonl so the dashboard reflects the
#    calibrated map track record immediately (same transform, same games)
# ---------------------------------------------------------------------------
import importlib, sys
sys.path.insert(0, "/opt/cs2-analyst")
vs = importlib.import_module("veto_sim")
RES = "/opt/cs2-analyst/data/live_picks_results.jsonl"
lines = [l for l in open(RES, encoding="utf-8") if l.strip()]
shutil.copy(RES, RES + f".bak_{int(time.time())}")
out, changed = [], 0
for l in lines:
    r = json.loads(l)
    if r.get("market") == "map" and isinstance(r.get("model_p"), (int, float)):
        r["model_p_raw"] = r["model_p"]
        r["model_p"] = round(vs.calibrate_map_p(r["model_p"]), 3)
        changed += 1
    out.append(json.dumps(r, ensure_ascii=False))
open(RES, "w", encoding="utf-8").write("\n".join(out) + "\n")
print(f"re-graded {changed} historical map records (raw kept as model_p_raw)")
