import shutil, os

p = "/opt/cs2-analyst/web.py"
s = open(p, encoding="utf-8").read()
if not os.path.exists(p + ".bak_picks"):
    shutil.copy(p, p + ".bak_picks")

FUNC = '''def _prematch_picks() -> list:
    """Current (unresolved, recent) pre-match VALUE bets -> the teams to actually bet."""
    from datetime import datetime, timezone, timedelta
    log = _read_jsonl(ROOT / "data" / "prematch_ft_log.jsonl")
    done = {r.get("slug") for r in _read_jsonl(ROOT / "data" / "prematch_ft_results.jsonl")}
    cut = datetime.now(timezone.utc) - timedelta(hours=30)
    out = []
    for r in log:
        if r.get("slug") in done or not r.get("bet_side"):
            continue
        try:
            if datetime.fromisoformat(str(r.get("logged_at")).replace("Z", "+00:00")) < cut:
                continue
        except Exception:
            pass
        out.append({"t1": r.get("t1"), "t2": r.get("t2"), "side": r.get("bet_side"),
                    "odd": r.get("bet_odd"), "edge": round(abs(r.get("edge1", 0)) * 100)})
    return out[-6:]


'''

if "_prematch_picks" not in s:
    assert "def _track(" in s, "anchor _track missing"
    s = s.replace("def _track(", FUNC + "def _track(", 1)
if '"prematch_picks"' not in s:
    assert '"prematch": _prematch()' in s, "state anchor missing"
    s = s.replace('"prematch": _prematch()', '"prematch": _prematch(), "prematch_picks": _prematch_picks()', 1)
open(p, "w", encoding="utf-8").write(s)
print("web.py: _prematch_picks added + wired to state")
