import os

p = "/opt/sportsbot/scripts/forward_test.py"
s = open(p, encoding="utf-8").read()
import shutil
if not os.path.exists(p + ".bak_maxedge"):
    shutil.copy(p, p + ".bak_maxedge")

# 1) add MAX_EDGE constant
anchor1 = 'MIN_EDGE = float(os.getenv("FT_MIN_EDGE", "0.05"))  # +5% model-vs-market edge'
add1 = ('MIN_EDGE = float(os.getenv("FT_MIN_EDGE", "0.05"))  # +5% model-vs-market edge\n'
        'MAX_EDGE = float(os.getenv("FT_MAX_EDGE", "0.22"))  # cap: a bigger "edge" vs a 24-book market\n'
        '# is model OVER-confidence (esp. Over 2.5 on internationals), not real value -> reject as a bug.\n'
        '# Week-1 forward-test -7.2% ROI was sunk by +31/34/83% "edge" overs that lost. (2026-07)')

if "MAX_EDGE" in s:
    print("already has MAX_EDGE")
else:
    assert anchor1 in s, "MIN_EDGE anchor missing"
    s = s.replace(anchor1, add1, 1)

    # 2) add the cap check right after the MIN_EDGE gate
    anchor2 = ('        if edge is None or edge < MIN_EDGE:\n'
               '            return {**base, "skipped": f"edge<{MIN_EDGE:+.0%}"}')
    add2 = (anchor2 + '\n'
            '        if edge > MAX_EDGE:\n'
            '            return {**base, "skipped": f"edge>{MAX_EDGE:+.0%} (fake — model over-confidence, not value)"}')
    assert anchor2 in s, "gate anchor missing"
    s = s.replace(anchor2, add2, 1)
    open(p, "w", encoding="utf-8").write(s)
    print("forward_test.py: MAX_EDGE cap added (rejects >22% fake edges)")
