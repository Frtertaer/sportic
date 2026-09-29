import json, collections

recs = [json.loads(l) for l in open("/opt/cs2-analyst/data/live_picks_results.jsonl") if l.strip()]
mp = [(r["model_p"], 1 if r["outcome"] == "win" else 0)
      for r in recs if r["market"] == "map" and r.get("outcome") in ("win", "loss")
      and isinstance(r.get("model_p"), (int, float))]
sr = [(r["model_p"], 1 if r["outcome"] == "win" else 0)
      for r in recs if r["market"] == "series" and r.get("outcome") in ("win", "loss")
      and isinstance(r.get("model_p"), (int, float))]

def brier(rs, f):
    return sum((f(p) - o) ** 2 for p, o in rs) / len(rs)
def gap(rs, f):  # avg predicted - avg realized  (0 = calibrated)
    n = len(rs); return sum(f(p) for p, o in rs) / n - sum(o for p, o in rs) / n
def logloss(rs, f):
    import math
    s = 0.0
    for p, o in rs:
        q = min(0.999, max(0.001, f(p)))
        s += -(o * math.log(q) + (1 - o) * math.log(1 - q))
    return s / len(rs)

def cap(c):
    return lambda p: 0.5 + max(-c, min(c, p - 0.5))       # confidence ceiling |p-.5|<=c
def shrink(k):
    return lambda p: 0.5 + (p - 0.5) * k                  # shrink toward 0.5
def shrink_cap(k, c):
    return lambda p: 0.5 + max(-c, min(c, (p - 0.5) * k))

transforms = {
    "identity": lambda p: p,
    "cap@0.65": cap(0.15),
    "cap@0.60": cap(0.10),
    "shrink0.7": shrink(0.7),
    "shrink0.5": shrink(0.5),
    "shrink0.7+cap0.65": shrink_cap(0.7, 0.15),
    "shrink0.6+cap0.60": shrink_cap(0.6, 0.10),
    "shrink0.55+cap0.62": shrink_cap(0.55, 0.12),
}
print(f"MAP market (n={len(mp)}):")
print(f"  {'transform':22} {'brier':>7} {'logloss':>8} {'gap':>7}")
for name, f in transforms.items():
    print(f"  {name:22} {brier(mp,f):7.4f} {logloss(mp,f):8.4f} {gap(mp,f)*100:+6.1f}pp")

print(f"\nSERIES market (n={len(sr)}) — check the SAME transform does NOT hurt series:")
for name, f in transforms.items():
    print(f"  {name:22} {brier(sr,f):7.4f} {logloss(sr,f):8.4f} {gap(sr,f)*100:+6.1f}pp")

# calibration curve of the best-looking transform
best = shrink_cap(0.6, 0.10)
print("\nMAP calibration under shrink0.6+cap0.60:")
bk = collections.defaultdict(lambda: [0, 0])
for p, o in mp:
    b = min(int(best(p) * 20) / 20, 0.95); bk[b][0] += 1; bk[b][1] += o
for b in sorted(bk):
    c, w = bk[b]; print(f"   pred {b*100:.0f}%: n={c:3d} real={w/c*100:4.0f}%")
