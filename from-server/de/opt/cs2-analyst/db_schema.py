import sqlite3, json
c = sqlite3.connect("file:/opt/sportsbot/sportsbot.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
print("=== TABLES ===")
for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
    print(" ", t["name"])
print("\n=== predictions COLUMNS ===")
cols = [r["name"] for r in c.execute("PRAGMA table_info(predictions)")]
print(cols)
print("\n=== one resolved row (all columns) ===")
r = c.execute("SELECT * FROM predictions WHERE actual_result IS NOT NULL AND actual_result!='' "
              "ORDER BY id DESC LIMIT 1").fetchone()
if r:
    for k in r.keys():
        v = str(r[k])
        print(f"  {k:24}: {v[:90]}")
c.close()
