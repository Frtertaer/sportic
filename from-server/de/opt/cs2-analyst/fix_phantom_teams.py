#!/usr/bin/env python3
"""Fix phantom 'NК vs NК' teams: winline map markers (Карта 2 = '2К') leaked in as team names.
1) reject the number+К pattern in the winline_odds scraper, 2) defensively skip it in the anomaly
detector, 3) purge already-polluted odds_history.jsonl + line_steam_log.jsonl."""
import json, re, pathlib
ROOT = pathlib.Path("/opt/cs2-analyst")

JUNK = re.compile(r"^\s*\d{1,2}\s*[КKкk]\s*$")   # "2К", "3 К", map/format markers (NOT 9INE/3DMAX)

# 1) scraper: add the reject to isName
wo = ROOT / "winline_odds.py"
s = wo.read_text(encoding="utf-8")
old = ("    && !/^матч$|карта|тотал|фора|сегодня|завтра|период|^пер\\./i.test(s)\n"
       "    && s.length > 1 && s.length < 40 && !/^\\d{1,2}\\.\\d{2}$/.test(s);")
new = ("    && !/^матч$|карта|тотал|фора|сегодня|завтра|период|^пер\\./i.test(s)\n"
       "    && !/^\\d{1,2}\\s*[КKкk]$/.test(s)\n"
       "    && s.length > 1 && s.length < 40 && !/^\\d{1,2}\\.\\d{2}$/.test(s);")
assert s.count(old) == 1, "isName filter not found"
wo.write_text(s.replace(old, new, 1), encoding="utf-8")
print("winline_odds.py: reject number+К map markers")

# 2) detector: defensive skip
la = ROOT / "line_anomaly.py"
t = la.read_text(encoding="utf-8")
old2 = ('    for r in rows:\n'
        '        o1, o2 = r.get("ml1"), r.get("ml2")\n'
        '        if o1 and o2 and 1.01 < o1 < 15 and 1.01 < o2 < 15:\n'
        '            m[(r.get("t1"), r.get("t2"))].append(r)')
new2 = ('    import re as _re\n'
        '    _junk = lambda x: bool(_re.match(r"^\\s*\\d{1,2}\\s*[КKкk]\\s*$", str(x or "")))\n'
        '    for r in rows:\n'
        '        o1, o2 = r.get("ml1"), r.get("ml2")\n'
        '        if _junk(r.get("t1")) or _junk(r.get("t2")):\n'
        '            continue\n'
        '        if o1 and o2 and 1.01 < o1 < 15 and 1.01 < o2 < 15:\n'
        '            m[(r.get("t1"), r.get("t2"))].append(r)')
assert t.count(old2) == 1, "detector loop not found"
la.write_text(t.replace(old2, new2, 1), encoding="utf-8")
print("line_anomaly.py: defensive junk skip")

# 3) purge polluted data
def purge(path):
    p = ROOT / "data" / path
    if not p.exists():
        return
    rows = [l for l in p.read_text(encoding="utf-8").splitlines() if l.strip()]
    keep = []
    dropped = 0
    for l in rows:
        try:
            r = json.loads(l)
        except Exception:
            continue
        if JUNK.match(str(r.get("t1") or "")) or JUNK.match(str(r.get("t2") or "")):
            dropped += 1; continue
        keep.append(l)
    p.write_text("\n".join(keep) + ("\n" if keep else ""), encoding="utf-8")
    print(f"  {path}: {len(rows)} -> {len(keep)} (dropped {dropped} phantom)")

print("purge polluted data:")
purge("odds_history.jsonl")
purge("line_steam_log.jsonl")
# reset seen so re-fires are clean
(ROOT / "data" / "line_steam_seen.json").write_text("{}", encoding="utf-8")
print("  line_steam_seen.json reset")
