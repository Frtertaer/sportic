import shutil, os

# =============================================================================
# WIN-RATE IMPROVEMENTS (2026-07, backtested + unanimously validated by the 4-AI council)
#   1. totals value: MIN_EDGE 0.04 -> 0.06  (edge>=6% realized 58% wr / +13% ROI vs 54%/+5%)
#   2. totals value: quarter-Kelly stake (0.25x, cap 2u/100u bankroll)  — replaces flat 1u
#   3. series value: gate model_p >= 0.53   (marginal <0.53 realized 46% wr = -EV coin flips)
#   (Nuke ban HELD — all 4 AIs called n=21 pure variance, not signal.)
# =============================================================================

# ---- 1+2) totals_monitor.py --------------------------------------------------
TM = "/opt/cs2-analyst/totals_monitor.py"
s = open(TM, encoding="utf-8").read()
if not os.path.exists(TM + ".bak_wr"):
    shutil.copy(TM, TM + ".bak_wr")

assert "MIN_EDGE = 0.04" in s, "MIN_EDGE anchor missing"
s = s.replace(
    "MIN_EDGE = 0.04",
    "MIN_EDGE = 0.06        # raised 0.04->0.06 (2026-07): edge>=6% => 58% wr / +13% ROI "
    "vs 54%/+5% at 4% (backtest n=44, council-validated). low edge = noise, not mispricing.",
    1)

# quarter-Kelly stake right after the edge gate
anchor_key = '        key = f"{t1}|{t2}|{line}|{side}"'
stake_block = (
    '        kelly = (p * odd - 1) / (odd - 1) if odd > 1 else 0.0\n'
    '        stake = round(min(2.0, max(0.0, 0.25 * kelly * 100)), 2)  # 1/4-Kelly, 100u bankroll, cap 2u\n'
    + anchor_key)
assert anchor_key in s, "key anchor missing"
s = s.replace(anchor_key, stake_block, 1)

# show stake in the alert
msg_anchor = 'f"\U0001F48E край <b>+{edge*100:.0f}%</b> → ставь {side} {line} @ {odd}\\n"'
assert msg_anchor in s, "msg anchor missing"
s = s.replace(msg_anchor, msg_anchor + '\n               f"\U0001F4B0 ставка <b>{stake}u</b> (¼-Kelly, банк 100u)\\n"', 1)

# log the stake
log_anchor = '"model_p": round(p, 3), "edge": round(edge, 3)})'
assert log_anchor in s, "log anchor missing"
s = s.replace(log_anchor, '"model_p": round(p, 3), "edge": round(edge, 3), "stake": stake})', 1)

open(TM, "w", encoding="utf-8").write(s)
print("totals_monitor.py: MIN_EDGE=0.06 + quarter-Kelly stake shipped")

# ---- 3) live_monitor.py: series model_p >= 0.53 gate -------------------------
LM = "/opt/cs2-analyst/live_monitor.py"
lm = open(LM, encoding="utf-8").read()
if not os.path.exists(LM + ".bak_wr"):
    shutil.copy(LM, LM + ".bak_wr")
old = "            if e >= MIN_EDGE:"
new = "            if e >= MIN_EDGE and p >= 0.53:  # skip near-coin-flip series (model_p<0.53 realized 46% wr = -EV)"
assert old in lm, "series gate anchor missing"
lm = lm.replace(old, new, 1)
open(LM, "w", encoding="utf-8").write(lm)
print("live_monitor.py: series value gate model_p>=0.53 shipped")
