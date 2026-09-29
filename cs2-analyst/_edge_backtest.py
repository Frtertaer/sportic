import json

ft = [json.loads(l) for l in open("/opt/cs2-analyst/data/totals_ft_results.jsonl") if l.strip()]
res = [r for r in ft if r.get("outcome") in ("win", "loss") and isinstance(r.get("edge"), (int, float))]

def report(min_edge):
    g = [r for r in res if r["edge"] >= min_edge]
    n = len(g)
    if not n:
        print(f"  edge>={min_edge*100:.0f}%: (no bets)"); return
    w = sum(1 for r in g if r["outcome"] == "win")
    pl = sum(r.get("pl_units", (r["odd"] - 1 if r["outcome"] == "win" else -1)) for r in g)
    roi = pl / n * 100
    print(f"  edge>={min_edge*100:>2.0f}%: n={n:3d}  winrate={w/n*100:4.1f}%  P/L={pl:+6.2f}u  ROI={roi:+5.1f}%")

print("Totals forward-test — raising MIN_EDGE (currently 3%):")
for me in (0.03, 0.045, 0.06, 0.075, 0.09):
    report(me)

# also: does stake-scaling by edge (proportional) beat flat?
print("\nStake = 1u flat vs edge-weighted (stake = edge/0.06, capped 2u), edge>=6%:")
g = [r for r in res if r["edge"] >= 0.06]
flat_pl = sum(r.get("pl_units", 0) for r in g)
ew_pl = 0.0
for r in g:
    stake = min(2.0, r["edge"] / 0.06)
    ew_pl += stake * (r["odd"] - 1 if r["outcome"] == "win" else -1)
print(f"  flat: P/L={flat_pl:+.2f}u over {len(g)} bets")
print(f"  edge-weighted: P/L={ew_pl:+.2f}u")
