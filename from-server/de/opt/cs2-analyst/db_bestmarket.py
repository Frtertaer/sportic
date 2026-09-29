import sqlite3
c = sqlite3.connect("file:/opt/sportsbot/sportsbot.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row

print("=== market families by EDGE vs bookmaker (trust_score), GLOBAL, sample>=1000 ===")
for r in c.execute("SELECT family,sample_size,model_brier,market_brier,brier_delta,trust_score "
                   "FROM market_family_quality_profiles WHERE scope='GLOBAL' AND sample_size>=1000 "
                   "ORDER BY trust_score DESC"):
    edge = "model BEATS market" if r["trust_score"] > 0 else "model worse"
    print(f"  {r['family']:8} n={r['sample_size']:5}  trust={r['trust_score']:+.3f}  brierΔ={r['brier_delta']:+.5f}  ({edge})")

print("\n=== best per-competition family edges (trust_score>0, sample>=500) ===")
for r in c.execute("SELECT competition_key,family,sample_size,trust_score FROM market_family_quality_profiles "
                   "WHERE scope='COMPETITION' AND sample_size>=500 AND trust_score>0 ORDER BY trust_score DESC LIMIT 12"):
    print(f"  {r['competition_key']:16} {r['family']:8} n={r['sample_size']:5}  trust={r['trust_score']:+.2f}")

print("\n=== distinct markets tracked in recalibration ===")
mk = [r["market"] for r in c.execute("SELECT DISTINCT market FROM numeric_recalibration_profiles ORDER BY market")]
print(" ", mk)

print("\n=== BEST calibrated edges: market+bucket where reality >> prediction (GLOBAL, sample>=200) ===")
for r in c.execute("SELECT market,bucket_label,sample_size,avg_pred_pct,actual_hit_pct,delta_pct "
                   "FROM numeric_recalibration_profiles WHERE scope='GLOBAL' AND sample_size>=200 "
                   "ORDER BY delta_pct DESC LIMIT 12"):
    print(f"  {r['market']:22} {r['bucket_label']:7} n={r['sample_size']:5}  pred {r['avg_pred_pct']:.0f}% -> real {r['actual_hit_pct']:.0f}%  (Δ{r['delta_pct']:+.1f})")

print("\n=== HIGHEST actual hit-rate buckets (GLOBAL, sample>=200) — highest raw winrate spots ===")
for r in c.execute("SELECT market,bucket_label,sample_size,avg_pred_pct,actual_hit_pct,delta_pct "
                   "FROM numeric_recalibration_profiles WHERE scope='GLOBAL' AND sample_size>=200 "
                   "ORDER BY actual_hit_pct DESC LIMIT 12"):
    print(f"  {r['market']:22} {r['bucket_label']:7} n={r['sample_size']:5}  real {r['actual_hit_pct']:.0f}%  (pred {r['avg_pred_pct']:.0f}%, Δ{r['delta_pct']:+.1f})")
c.close()
