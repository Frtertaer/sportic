import sqlite3
c = sqlite3.connect("file:/opt/sportsbot/sportsbot.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
tables = [r["name"] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]
print("scanning", len(tables), "tables for corner/shot/card/xg columns...\n")
KEY = ("corner", "corners", "shot", "card", "xg", "possession", "foul")
for t in tables:
    cols = [r["name"] for r in c.execute(f"PRAGMA table_info({t})")]
    hits = [col for col in cols if any(k in col.lower() for k in KEY)]
    if hits:
        print(f"  {t}: {hits}")
print("\n=== historical_matches columns (does it store match stats beyond goals?) ===")
print([r["name"] for r in c.execute("PRAGMA table_info(historical_matches)")])
print("\n=== historical_team_snapshots columns ===")
print([r["name"] for r in c.execute("PRAGMA table_info(historical_team_snapshots)")])
print("\n=== prediction_features columns ===")
print([r["name"] for r in c.execute("PRAGMA table_info(prediction_features)")])
c.close()
