wp = "/opt/cs2-analyst/web.py"
w = open(wp, encoding="utf-8").read()

OLD = '''        sd = r.get("start_date")
        if sd:
            try:
                if datetime.fromisoformat(str(sd).replace("Z", "+00:00")) < datetime.now(timezone.utc):
                    continue
            except Exception:
                pass'''
NEW = '''        sd = r.get("start_date")
        if not sd:
            continue
        try:
            if datetime.fromisoformat(str(sd).replace("Z", "+00:00")) <= datetime.now(timezone.utc):
                continue
        except Exception:
            continue'''

if NEW in w:
    print("web.py already strict-future")
elif OLD in w:
    open(wp, "w", encoding="utf-8").write(w.replace(OLD, NEW, 1))
    print("web.py: _prematch_picks now shows ONLY upcoming picks (start_date in future)")
else:
    print("anchor not found")
