import shutil, os

p = "/opt/cs2-analyst/web.py"
s = open(p, encoding="utf-8").read()
if not os.path.exists(p + ".bak_fb"):
    shutil.copy(p, p + ".bak_fb")

# 1) import render_football
if "render_football" not in s.split("def _football")[0]:
    s = s.replace("from web_ui import render\n", "from web_ui import render, render_football\n", 1)

# 2) _football() function
FUNC = '''def _football() -> dict:
    """Read-only football predictions from sportsbot.db -> accuracy + upcoming + resolved."""
    import sqlite3
    DB = "/opt/sportsbot/sportsbot.db"
    try:
        c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        c.row_factory = sqlite3.Row
        rows = [dict(r) for r in c.execute(
            "SELECT team1,team2,predicted_at,prob_w1,prob_draw,prob_w2,prob_over_2_5,prob_btts_yes,"
            "top_rec,top_confidence_pct,actual_result,actual_score,actual_goals_home,actual_goals_away,"
            "value_bets,risk_level FROM predictions ORDER BY id DESC LIMIT 80")]
        c.close()
    except Exception:  # noqa: BLE001
        return {"n": 0, "acc": {}, "pending": [], "resolved": [], "value_count": 0}
    resolved = [r for r in rows if r.get("actual_result")]

    def amax(r):
        w1, dr, w2 = r.get("prob_w1") or 0, r.get("prob_draw") or 0, r.get("prob_w2") or 0
        return "W1" if w1 >= dr and w1 >= w2 else ("DRAW" if dr >= w2 else "W2")
    n1x2 = [r for r in resolved if r.get("actual_result") in ("W1", "DRAW", "W2")]
    w1x2 = sum(1 for r in n1x2 if amax(r) == r["actual_result"])
    ou = [r for r in resolved if r.get("actual_goals_home") is not None
          and r.get("actual_goals_away") is not None and r.get("prob_over_2_5") is not None]
    wou = sum(1 for r in ou if (r["actual_goals_home"] + r["actual_goals_away"] > 2.5) == ((r["prob_over_2_5"] or 0) >= 50))
    bt = [r for r in resolved if r.get("actual_goals_home") is not None
          and r.get("actual_goals_away") is not None and r.get("prob_btts_yes") is not None]
    wbt = sum(1 for r in bt if (r["actual_goals_home"] > 0 and r["actual_goals_away"] > 0) == ((r["prob_btts_yes"] or 0) >= 50))
    acc = {"1x2": (len(n1x2), round(w1x2 / len(n1x2) * 100) if n1x2 else 0),
           "ou": (len(ou), round(wou / len(ou) * 100) if ou else 0),
           "btts": (len(bt), round(wbt / len(bt) * 100) if bt else 0)}
    vc = sum(1 for r in rows if r.get("value_bets") and r["value_bets"] not in ("[]", ""))
    return {"n": len(rows), "acc": acc,
            "pending": [r for r in rows if not r.get("actual_result")],
            "resolved": resolved, "value_count": vc}


'''
if "def _football(" not in s:
    assert "def state(" in s, "state anchor missing"
    s = s.replace("def state(", FUNC + "def state(", 1)

# 3) add to state dict
if '"football":' not in s:
    assert '"track": _track(), "ts": int(time.time())}' in s, "state dict anchor missing"
    s = s.replace('"track": _track(), "ts": int(time.time())}',
                  '"track": _track(), "football": _football(), "ts": int(time.time())}', 1)

# 4) route /football
OLD = '''        else:
            body = render(d).encode()
            ctype = "text/html; charset=utf-8"'''
NEW = '''        else:
            body = (render_football(d) if self.path.startswith("/football") else render(d)).encode()
            ctype = "text/html; charset=utf-8"'''
if "/football" not in s.split("def do_GET")[1][:600] and OLD in s:
    s = s.replace(OLD, NEW, 1)

open(p, "w", encoding="utf-8").write(s)
print("web.py: _football + route + import wired")
