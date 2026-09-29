import sqlite3
c = sqlite3.connect("file:/opt/sportsbot/sportsbot.db?mode=ro", uri=True)
c.row_factory = sqlite3.Row
n = c.execute("SELECT COUNT(*) n FROM historical_team_snapshots").fetchone()["n"]
nc = c.execute("SELECT COUNT(*) n FROM historical_team_snapshots WHERE team1_corners_avg IS NOT NULL").fetchone()["n"]
print(f"team snapshots: {n} | with corners_avg populated: {nc} ({100*nc/n:.0f}%)" if n else "empty")
print("\nsample corner rows:")
for r in c.execute("SELECT team1_corners_avg,team1_corners_allowed_avg,team2_corners_avg,team2_corners_allowed_avg "
                   "FROM historical_team_snapshots WHERE team1_corners_avg IS NOT NULL LIMIT 6"):
    print("  t1_for=%.2f t1_allowed=%.2f | t2_for=%.2f t2_allowed=%.2f" % (
        r["team1_corners_avg"] or 0, r["team1_corners_allowed_avg"] or 0,
        r["team2_corners_avg"] or 0, r["team2_corners_allowed_avg"] or 0))
# any table storing ACTUAL corners outcome?
print("\nsearching for actual-corner-result columns anywhere...")
for t in [x["name"] for x in c.execute("SELECT name FROM sqlite_master WHERE type='table'")]:
    cols = [x["name"] for x in c.execute(f"PRAGMA table_info({t})")]
    res = [col for col in cols if "corner" in col.lower() and ("actual" in col.lower() or "result" in col.lower() or "final" in col.lower() or "total" in col.lower())]
    if res:
        print(f"  {t}: {res}")
print("(none above = no actual corner OUTCOMES stored -> cannot backtest corner model)")
c.close()
