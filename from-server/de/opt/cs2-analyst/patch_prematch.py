#!/usr/bin/env python3
"""Fix pre-match anomalies: also surface ANTI-SWEEP near-locks (team takes >=1 map, winline
'Фора карт +1.5' — real placeable odds) alongside the rare ML-value picks, so the section
reliably shows bettable teams instead of being empty."""
import pathlib

# ---- web.py: _prematch_picks() also returns anti-sweep near-locks ----------------------------
w = pathlib.Path("/opt/cs2-analyst/web.py")
s = w.read_text(encoding="utf-8")
old_web = '''        out.append({"t1": r.get("t1"), "t2": r.get("t2"), "side": r.get("bet_side"),
                    "odd": r.get("bet_odd"), "edge": round(abs(r.get("edge1", 0)) * 100)})
    return out[-6:]'''
new_web = '''        out.append({"t1": r.get("t1"), "t2": r.get("t2"), "side": r.get("bet_side"),
                    "odd": r.get("bet_odd"), "edge": round(abs(r.get("edge1", 0)) * 100), "kind": "ml"})
    # anti-sweep near-locks: team should take >=1 map (winline "Фора карт +1.5") — real placeable odds
    now = datetime.now(timezone.utc)
    starts = {r["slug"]: r["start_date"] for r in log if r.get("slug") and r.get("start_date")}
    asw_done = {r.get("slug") for r in _read_jsonl(ROOT / "data" / "antisweep_ft_results.jsonl")}
    aseen = set()
    for r in _read_jsonl(ROOT / "data" / "antisweep_ft_log.jsonl"):
        slug = r.get("slug")
        if not slug or slug in asw_done or slug in aseen or not r.get("bet_team"):
            continue
        if (r.get("p_anti") or 0) < 0.72 or (r.get("edge") or 0) <= 0:
            continue
        sd = starts.get(slug)
        if sd:
            try:
                if datetime.fromisoformat(str(sd).replace("Z", "+00:00")) <= now:
                    continue
            except Exception:
                pass
        else:
            try:
                if datetime.fromisoformat(str(r.get("logged_at")).replace("Z", "+00:00")) <= now - timedelta(hours=8):
                    continue
            except Exception:
                continue
        aseen.add(slug)
        out.append({"t1": r.get("t1"), "t2": r.get("t2"), "side": r.get("bet_team"),
                    "odd": r.get("odd"), "edge": round((r.get("edge") or 0) * 100),
                    "p": round((r.get("p_anti") or 0) * 100), "kind": "anti"})
    return out[-8:]'''
assert s.count(old_web) == 1, f"web.py marker count={s.count(old_web)}"
w.write_text(s.replace(old_web, new_web, 1), encoding="utf-8")
print("patched web.py _prematch_picks (+anti-sweep)")

# ---- web_ui.py: ML loop skips anti; add a dedicated anti-sweep render loop -------------------
u = pathlib.Path("/opt/cs2-analyst/web_ui.py")
t = u.read_text(encoding="utf-8")

assert t.count("    for v in pmp:\n") == 1, "pmp loop not unique"
t = t.replace("    for v in pmp:\n",
              "    for v in [x for x in pmp if x.get('kind') != 'anti']:\n", 1)

anchor = "    P.append('<div class=sect>\\U0001f4ca Точность модели"
assert t.count(anchor) == 1, "track sect anchor not found"
anti_block = (
    "    for v in [x for x in pmp if x.get('kind') == 'anti']:\n"
    "        P.append(f'''<div class=\"card val\">\n"
    "<div class=mrow><span class=team>{e(v[\"t1\"])} vs {e(v[\"t2\"])}</span>"
    "<span class=\"badge b-val\">\\U0001f6e1 анти-свип · край +{v[\"edge\"]}%</span></div>\n"
    "<div class=pick><b class=pos>{e(v[\"side\"])} берёт 1+ карту (Фора карт +1.5) @ {v[\"odd\"]}</b>"
    "<span class=mut>наша вероятность {v.get(\"p\",\"?\")}%</span></div>\n"
    "</div>''')\n"
)
t = t.replace(anchor, anti_block + anchor, 1)
u.write_text(t, encoding="utf-8")
print("patched web_ui.py (anti-sweep render loop)")
