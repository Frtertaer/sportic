sp = "/opt/cs2-analyst/prematch_scan.py"
s = open(sp, encoding="utf-8").read()
old = '"bet_side": bet_side, "bet_odd": bet_odd,\n            "antisweep"'
new = '"bet_side": bet_side, "bet_odd": bet_odd, "start_date": m.get("start_date"),\n            "antisweep"'
if '"start_date": m.get("start_date")' in s:
    print("already logs start_date in rec")
elif old in s:
    open(sp, "w", encoding="utf-8").write(s.replace(old, new, 1))
    print("prematch_scan rec now logs start_date")
else:
    print("anchor not found — rec structure differs")
