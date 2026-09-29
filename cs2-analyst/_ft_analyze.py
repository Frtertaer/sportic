import json, collections

ft = [json.loads(l) for l in open("/opt/cs2-analyst/data/totals_ft_results.jsonl") if l.strip()]
print("keys:", list(ft[0].keys()) if ft else "empty")
print("sample:", json.dumps(ft[0], ensure_ascii=False)[:400] if ft else "")
res = [r for r in ft if r.get("outcome") in ("win", "loss")]
def wr(g):
    n = len(g); w = sum(1 for r in g if r["outcome"] == "win"); return n, (w / n * 100 if n else 0)
n, p = wr(res)
print(f"\nPLACED value bets: n={n} winrate={p:.1f}%")

for key in ["map", "side", "line"]:
    print(f"  by {key}:")
    by = collections.defaultdict(list)
    for r in res:
        by[r.get(key)].append(r)
    for k, g in sorted(by.items(), key=lambda x: -wr(x[1])[1]):
        gn, gp = wr(g)
        print(f"    {str(k):10} n={gn:3d} wr={gp:4.0f}%")

# edge / model_p buckets if present
for f in ["edge", "model_p", "exp"]:
    vals = [r for r in res if isinstance(r.get(f), (int, float))]
    if not vals:
        continue
    print(f"  by {f}:")
    by = collections.defaultdict(list)
    for r in vals:
        v = r[f]
        b = round(v * 100 // 3 * 3) if f != "exp" else round(v)
        by[b].append(r)
    for b in sorted(by):
        gn, gp = wr(by[b])
        print(f"    {f}={b}  n={gn:3d} wr={gp:4.0f}%")
