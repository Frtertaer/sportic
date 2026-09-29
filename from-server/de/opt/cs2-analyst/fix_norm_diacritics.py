#!/usr/bin/env python3
"""Fix name matching: fold diacritics before stripping so 'Västerås' -> 'VASTERAS' (was 'VSTERS' and
never matched winline). Applies to the steam resolver, backtest, and line detector."""
import pathlib

NEW_RESOLVE = '''def _norm(s: str) -> str:
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s if c.isalnum()).upper()'''

OLD_RESOLVE = '''def _norm(s: str) -> str:
    return "".join(c for c in (s or "").upper() if c.isalnum())'''

NEW_BT = '''def _norm(s):
    import unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s if c.isalnum()).upper()'''

OLD_BT = '''def _norm(s):
    return "".join(c for c in (s or "").upper() if c.isalnum())'''

OLD_LA = '''def _norm(s):
    import re
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())'''
NEW_LA = '''def _norm(s):
    import re, unicodedata
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9а-я]", "", s.lower())'''

for path, old, new in [("line_steam_resolve.py", OLD_RESOLVE, NEW_RESOLVE),
                       ("line_steam_backtest.py", OLD_BT, NEW_BT),
                       ("line_anomaly.py", OLD_LA, NEW_LA)]:
    p = pathlib.Path("/opt/cs2-analyst") / path
    t = p.read_text(encoding="utf-8")
    if old not in t:
        print(f"  {path}: marker NOT found (skip)"); continue
    p.write_text(t.replace(old, new, 1), encoding="utf-8")
    print(f"  {path}: _norm now folds diacritics")
