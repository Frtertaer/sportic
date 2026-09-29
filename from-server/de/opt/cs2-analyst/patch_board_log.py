#!/usr/bin/env python3
"""Log board VALUE picks + strong anti-sweeps to prematch_board_log.jsonl (dedup per match+side) so
they can be graded into a real win-rate track once the result source is wired."""
import pathlib
pb = pathlib.Path("/opt/cs2-analyst/prematch_board.py")
t = pb.read_text(encoding="utf-8")

fn = '''def _log_picks(board, now):
    LOG = ROOT / "data" / "prematch_board_log.jsonl"
    existing = set()
    if LOG.exists():
        for l in LOG.read_text(encoding="utf-8").splitlines():
            try:
                r = json.loads(l); existing.add((r.get("t1"), r.get("t2"), r.get("side"), r.get("start_date")))
            except Exception:
                pass
    new = []
    for r in board:
        picks = []
        if r.get("verdict") == "VALUE" and r.get("bet_side"):
            picks.append(("ml", r["bet_side"], r.get("bet_odd")))
        asw = r.get("antisweep") or ["", 0]
        if (asw[1] or 0) >= 0.85:
            picks.append(("antisweep", asw[0], None))
        for market, side, odd in picks:
            if (r["t1"], r["t2"], side, r.get("start_date")) in existing:
                continue
            new.append({"t1": r["t1"], "t2": r["t2"], "market": market, "side": side, "odd": odd,
                        "pm_fav": r.get("pm_fav"), "news": r.get("news", ""),
                        "start_date": r.get("start_date"), "logged_at": now.isoformat()})
    if new:
        with LOG.open("a", encoding="utf-8") as f:
            for r in new:
                f.write(json.dumps(r, ensure_ascii=False) + "\\n")
    return len(new)


def build() -> int:'''
assert t.count("def build() -> int:") == 1
t = t.replace("def build() -> int:", fn, 1)

# call _log_picks right after writing the board json
t = t.replace(
    '    OUT.write_text(json.dumps({"updated": now.isoformat(), "matches": board}, ensure_ascii=False),\n'
    '                   encoding="utf-8")\n',
    '    OUT.write_text(json.dumps({"updated": now.isoformat(), "matches": board}, ensure_ascii=False),\n'
    '                   encoding="utf-8")\n'
    '    try:\n'
    '        nlog = _log_picks(board, now)\n'
    '        if nlog:\n'
    '            print(f"logged {nlog} new board picks for tracking")\n'
    '    except Exception as _e:\n'
    '        print("log_picks err:", _e)\n', 1)
assert "_log_picks(board, now)" in t
pb.write_text(t, encoding="utf-8")
print("prematch_board.py: board-pick logging added")
