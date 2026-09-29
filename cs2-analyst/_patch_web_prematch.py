import shutil, os

W = "/opt/cs2-analyst/web.py"
s = open(W, encoding="utf-8").read()
if not os.path.exists(W + ".bak_prematch"):
    shutil.copy(W, W + ".bak_prematch")

if "def _prematch(" not in s:
    func = (
        'def _prematch() -> dict:\n'
        '    res = _read_jsonl(ROOT / "data" / "prematch_ft_results.jsonl")\n'
        '    n = len(res)\n'
        '    wins = sum(1 for r in res if r.get("outcome") == "win")\n'
        '    pl = sum(float(r.get("pl_units", 0)) for r in res)\n'
        '    pend = len(_read_jsonl(ROOT / "data" / "prematch_ft_log.jsonl")) - n\n'
        '    return {"n": n, "wins": wins, "winrate": round(wins / n * 100) if n else 0,\n'
        '            "pl": round(pl, 2), "roi": round(pl / n * 100, 1) if n else 0, "pending": max(0, pend)}\n\n\n'
    )
    s = s.replace("def _roi() -> dict:", func + "def _roi() -> dict:", 1)

if '"prematch": _prematch()' not in s:
    s = s.replace('"roi": _roi(),', '"roi": _roi(), "prematch": _prematch(),', 1)

if "пре-матч скан" not in s:
    anchor = ('<div class="roi {lcls}">ROI {lg[\'roi\']:+}%</div>'
              '<div class=muted>{lg[\'pl\']:+} ед. · near-certain live (P серии ≥85%, край ≥8%)</div></div>""")')
    card = (anchor + '\n\n    pm = d["prematch"]\n'
            '    pcls = "pos" if pm["roi"] >= 0 else "neg"\n'
            '    parts.append(f"""<h2>🔮 Пре-матч скан (аномалии/договорняки → value)</h2><div class=card>\n'
            '<div class=row><span>ставок: {pm[\'n\']} · винрейт <b>{pm[\'winrate\']}%</b> · ждут: {pm[\'pending\']}</span></div>\n'
            '<div class="roi {pcls}">ROI {pm[\'roi\']:+}%</div>'
            '<div class=muted>{pm[\'pl\']:+} ед. · модель↔линия расхождение 7-15%, странные матчи в Telegram</div></div>""")')
    assert anchor in s, "livelag card anchor not found"
    s = s.replace(anchor, card, 1)

open(W, "w", encoding="utf-8").write(s)
print("web.py: pre-match scan section added")
