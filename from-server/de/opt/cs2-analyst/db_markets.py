import sqlite3
c = sqlite3.connect("file:/opt/sportsbot/sportsbot.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row

def dump(tbl, limit=40):
    try:
        cols = [r["name"] for r in c.execute(f"PRAGMA table_info({tbl})")]
        n = c.execute(f"SELECT COUNT(*) n FROM {tbl}").fetchone()["n"]
        print(f"\n===== {tbl} ({n} rows) =====")
        print("cols:", cols)
        for r in c.execute(f"SELECT * FROM {tbl} LIMIT {limit}"):
            print("  " + " | ".join(f"{k}={str(r[k])[:34]}" for k in r.keys()))
    except Exception as e:
        print(f"{tbl}: {e}")

dump("market_family_quality_profiles")
dump("numeric_recalibration_profiles", 20)
# historical market lines: what market types exist + counts
print("\n===== historical_market_lines: market types =====")
try:
    cols = [r["name"] for r in c.execute("PRAGMA table_info(historical_market_lines)")]
    print("cols:", cols)
    n = c.execute("SELECT COUNT(*) n FROM historical_market_lines").fetchone()["n"]
    print("total rows:", n)
    # find the market-type column
    for cand in ("market", "market_code", "market_family", "market_type", "market_name", "family"):
        if cand in cols:
            print(f"\n-- distinct {cand} (top 40 by count) --")
            for r in c.execute(f"SELECT {cand} k, COUNT(*) n FROM historical_market_lines GROUP BY {cand} ORDER BY n DESC LIMIT 40"):
                print(f"   {r['n']:6}  {r['k']}")
            break
except Exception as e:
    print("err:", e)
# predictions: what markets the bot actually recommends
print("\n===== predictions.top_rec distinct =====")
for r in c.execute("SELECT top_rec k, COUNT(*) n FROM predictions GROUP BY top_rec ORDER BY n DESC LIMIT 30"):
    print(f"   {r['n']:4}  {r['k']}")
c.close()
