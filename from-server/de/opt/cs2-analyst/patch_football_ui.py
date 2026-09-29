#!/usr/bin/env python3
"""Fix football-tab display: (1) every card headlined the SAME 'Over 2.5' (the model's worst market,
43% accuracy) — lead instead with the per-match 1X2 lean (calibrated ~53%), O/U demoted to a muted
secondary line; (2) the 'Value-ставок' hero showed 21 while 0 rendered — count actionable (pending)
value bets so the number matches the cards."""
import pathlib
u = pathlib.Path("/opt/cs2-analyst/web_ui.py")
t = u.read_text(encoding="utf-8")
orig = t

# 1) add the _flean helper just before render_football
helper = '''def _flean(r: dict) -> str:
    """Model's calibrated 1X2 lean (varies per match, ~53% accurate) — shown instead of the O/U
    rec, which is the model's weakest market (~43%). Returns 'П1 · Team · 57%' style."""
    w1, dr, w2 = r.get("prob_w1") or 0, r.get("prob_draw") or 0, r.get("prob_w2") or 0
    best = max(w1, dr, w2)
    if best <= 0:
        return "\\u2014"
    if best == w1:
        return f'П1 · {html.escape(str(r.get("team1", "?")))} · {w1:.0f}%'
    if best == w2:
        return f'П2 · {html.escape(str(r.get("team2", "?")))} · {w2:.0f}%'
    return f'Ничья · {dr:.0f}%'


def render_football(d: dict) -> str:'''
assert t.count("def render_football(d: dict) -> str:") == 1, "render_football signature not unique"
t = t.replace("def render_football(d: dict) -> str:", helper, 1)

# 2) hero Value-ставок: count actionable (pending) value bets instead of the cumulative 21
t = t.replace('    nval = f.get("value_count", 0)',
              '    nval = sum(1 for r in pend if _parse_vb(r.get("value_bets")))')
t = t.replace('<div class=stat><div class=k>Value-ставок</div><div class="v blu">{nval}</div><div class=s>найдено моделью</div></div>',
              '<div class=stat><div class=k>Value-ставок</div><div class="v blu">{nval}</div><div class=s>в ближайших матчах</div></div>')

# 3) pending card: lead with the varying 1X2 lean, demote O/U to a muted secondary line
old_rec = '<div class=mrow><span class=mut>рекомендация</span><b>{e(str(r.get("top_rec") or "—"))} · {conf:.0f}%</b></div>'
new_rec = ('<div class=mrow><span class=mut>лучший исход</span><b>{_flean(r)}</b></div>\n'
           '<div class=mrow><span class=mut>тотал 2.5</span><span class=mut>{e(str(r.get("top_rec") or "—"))} · {conf:.0f}%</span></div>')
assert t.count(old_rec) == 1, f"pending rec line count={t.count(old_rec)}"
t = t.replace(old_rec, new_rec, 1)

assert t != orig, "no changes applied"
u.write_text(t, encoding="utf-8")
print("patched web_ui.py: 1X2 lean headline + honest value-bet hero")
