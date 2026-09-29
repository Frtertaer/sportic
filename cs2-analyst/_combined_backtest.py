import json

res = [json.loads(l) for l in open("/opt/cs2-analyst/data/totals_ft_results.jsonl") if l.strip()]
res = [r for r in res if r.get("outcome") in ("win", "loss") and isinstance(r.get("edge"), (int, float))]

def qkelly_stake(r):
    k = (r["model_p"] * r["odd"] - 1) / (r["odd"] - 1)
    return min(2.0, max(0.0, 0.25 * k * 100))

def run(g, kelly):
    n = len(g); w = sum(1 for x in g if x["outcome"] == "win")
    pl = staked = 0.0
    for r in g:
        st = qkelly_stake(r) if kelly else 1.0
        staked += st
        pl += st * (r["odd"] - 1) if r["outcome"] == "win" else -st
    roi = pl / staked * 100 if staked else 0
    return n, (w / n * 100 if n else 0), pl, roi

old = res
new = [r for r in res if r["edge"] >= 0.06]
n0, w0, pl0, roi0 = run(old, False)
n1, w1, pl1, roi1 = run(new, True)
print("BEFORE (all placed, edge>=4%, flat 1u):")
print(f"   n={n0}  winrate={w0:.1f}%  P/L={pl0:+.2f}u  ROI={roi0:+.1f}%")
print("AFTER  (edge>=6% gate + quarter-Kelly stake):")
print(f"   n={n1}  winrate={w1:.1f}%  P/L={pl1:+.2f}u  ROI={roi1:+.1f}%")
print(f"\nwinrate: {w0:.1f}% -> {w1:.1f}%  ({w1-w0:+.1f}pp)")
print(f"ROI    : {roi0:+.1f}% -> {roi1:+.1f}%")
