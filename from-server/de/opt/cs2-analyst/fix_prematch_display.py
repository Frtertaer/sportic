#!/usr/bin/env python3
"""Fix pre-match display: (1) antisweep_scan now records start_date; (2) _prematch_picks decides
'upcoming' by future start_date OR presence in winline's live pre-match feed (winline_odds.json),
instead of a fragile 'logged <8h ago' guess that wrongly hid strong morning-scanned picks."""
import re, pathlib

# ---- antisweep_scan.py: carry + persist start_date --------------------------------------------
a = pathlib.Path("/opt/cs2-analyst/antisweep_scan.py")
s = a.read_text(encoding="utf-8")
s2 = s.replace(
    'cands.append({"slug": slug, "n1": n1, "n2": n2, "anti": anti, "p_anti": max(anti.values())})',
    'cands.append({"slug": slug, "n1": n1, "n2": n2, "anti": anti, "p_anti": max(anti.values()), "start_date": m.get("start_date")})')
s2 = s2.replace(
    '                                    "market": "map+1.5", "p_anti": p, "odd": odd, "edge": round(edge, 3),\n'
    '                                    "logged_at": datetime.now(timezone.utc).isoformat()},',
    '                                    "market": "map+1.5", "p_anti": p, "odd": odd, "edge": round(edge, 3),\n'
    '                                    "start_date": c.get("start_date"),\n'
    '                                    "logged_at": datetime.now(timezone.utc).isoformat()},')
assert s2 != s, "antisweep_scan markers not found"
a.write_text(s2, encoding="utf-8")
print("patched antisweep_scan.py (+start_date)")

# ---- web.py: rewrite _prematch_picks with the winline-feed gate -------------------------------
w = pathlib.Path("/opt/cs2-analyst/web.py")
t = w.read_text(encoding="utf-8")
new_fn = '''def _prematch_picks() -> list:
    """Upcoming, unresolved bettable pre-match picks: ML-value + anti-sweep near-locks (team takes
    >=1 map, winline 'Фора карт +1.5'). A match counts as 'upcoming' if its start_date is in the
    future OR its teams are in winline's live pre-match feed (winline_odds.json) — no recency guess."""
    import re as _re, json as _json
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    def nrm(x):
        return _re.sub(r"[^a-z0-9]", "", str(x or "").lower())
    up = set()
    try:
        for r in _json.loads((ROOT / "data" / "winline_odds.json").read_text(encoding="utf-8")):
            up.add(frozenset((nrm(r.get("t1")), nrm(r.get("t2")))))
    except Exception:
        pass
    def upcoming(t1, t2, sd):
        if sd:
            try:
                return datetime.fromisoformat(str(sd).replace("Z", "+00:00")) > now
            except Exception:
                pass
        return frozenset((nrm(t1), nrm(t2))) in up
    out = []
    pdone = {r.get("slug") for r in _read_jsonl(ROOT / "data" / "prematch_ft_results.jsonl")}
    for r in _read_jsonl(ROOT / "data" / "prematch_ft_log.jsonl"):
        if r.get("slug") in pdone or not r.get("bet_side"):
            continue
        if not upcoming(r.get("t1"), r.get("t2"), r.get("start_date")):
            continue
        out.append({"t1": r.get("t1"), "t2": r.get("t2"), "side": r.get("bet_side"),
                    "odd": r.get("bet_odd"), "edge": round(abs(r.get("edge1", 0)) * 100), "kind": "ml"})
    adone = {r.get("slug") for r in _read_jsonl(ROOT / "data" / "antisweep_ft_results.jsonl")}
    aseen = set()
    for r in _read_jsonl(ROOT / "data" / "antisweep_ft_log.jsonl"):
        slug = r.get("slug")
        if not slug or slug in adone or slug in aseen or not r.get("bet_team"):
            continue
        if (r.get("p_anti") or 0) < 0.70 or (r.get("edge") or 0) <= 0:
            continue
        if not upcoming(r.get("t1"), r.get("t2"), r.get("start_date")):
            continue
        aseen.add(slug)
        out.append({"t1": r.get("t1"), "t2": r.get("t2"), "side": r.get("bet_team"),
                    "odd": r.get("odd"), "edge": round((r.get("edge") or 0) * 100),
                    "p": round((r.get("p_anti") or 0) * 100), "kind": "anti"})
    return out[-12:]'''
t2, n = re.subn(r"def _prematch_picks\(\) -> list:.*?\n    return out\[-8:\]", new_fn, t, count=1, flags=re.S)
assert n == 1, f"_prematch_picks not replaced (n={n})"
w.write_text(t2, encoding="utf-8")
print("rewrote web.py _prematch_picks (winline-feed gate)")
