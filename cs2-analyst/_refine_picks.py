import os

# 1) prematch_scan.py: log start_date so we can hide already-started matches
sp = "/opt/cs2-analyst/prematch_scan.py"
s = open(sp, encoding="utf-8").read()
old = '"bet_side": bet_side, "bet_odd": bet_odd,'
new = '"bet_side": bet_side, "bet_odd": bet_odd, "start_date": m.get("start_date"),'
if "start_date" not in s and old in s:
    s = s.replace(old, new, 1)
    open(sp, "w", encoding="utf-8").write(s)
    print("prematch_scan: logs start_date")
else:
    print("prematch_scan: already has start_date or anchor missing")

# 2) web.py _prematch_picks: skip picks whose match already started (when start_date known)
wp = "/opt/cs2-analyst/web.py"
w = open(wp, encoding="utf-8").read()
anchor = '''        try:
            if datetime.fromisoformat(str(r.get("logged_at")).replace("Z", "+00:00")) < cut:
                continue
        except Exception:
            pass'''
add = '''        try:
            if datetime.fromisoformat(str(r.get("logged_at")).replace("Z", "+00:00")) < cut:
                continue
        except Exception:
            pass
        sd = r.get("start_date")
        if sd:
            try:
                if datetime.fromisoformat(str(sd).replace("Z", "+00:00")) < datetime.now(timezone.utc):
                    continue
            except Exception:
                pass'''
if "start_date" not in w.split("_prematch_picks")[1][:600] and anchor in w:
    w = w.replace(anchor, add, 1)
    open(wp, "w", encoding="utf-8").write(w)
    print("web.py: _prematch_picks hides started matches")
else:
    print("web.py: already refined or anchor missing")
