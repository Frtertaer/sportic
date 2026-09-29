import re

NEW_RENDER = r'''def render(d: dict) -> str:
    e = html.escape
    ts = time.strftime("%H:%M", time.localtime(d["ts"]))

    strats = [
        ("livelag", "⚡", "LIVE лаг-линии",
         "Ставит, когда исход карты почти решён (счёт 11-4), но бук не успел обновить live-коэффициент. Ловим задержку линии.",
         d["livelag"]),
        ("prematch", "\U0001f52e", "Пре-матч аномалии",
         "За часы до матча ищет странные коэффициенты (модель против линии — возможные договорняки) и находит value.",
         d["prematch"]),
        ("totals", "\U0001f3b2", "Тоталы раундов",
         "Наш калиброванный расчёт тотала раундов карты против линии winline. Ставим только при реальном крае.",
         d["roi"]),
    ]
    tot_n = sum(s[4]["n"] for s in strats)
    tot_pl = round(sum(s[4]["pl"] for s in strats), 2)
    tot_w = sum(s[4]["wins"] for s in strats)
    tot_wr = round(tot_w / tot_n * 100) if tot_n else 0
    tot_roi = round(tot_pl / tot_n * 100, 1) if tot_n else 0
    pend = sum(s[4]["pending"] for s in strats)
    nlive = len(d["live"])
    nval = len(d["value"])

    def sign(x):
        return f"+{x}" if x >= 0 else f"{x}"
    roicls = "pos" if tot_roi >= 0 else "neg"

    P = ['''<!doctype html><html lang=ru><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<meta http-equiv=refresh content=30><title>CS2 Analyst</title><style>
:root{--bg:#0b0e14;--surf:#141a24;--surf2:#1b2331;--line:#232d3d;--txt:#e8edf4;--mut:#8593a6;
--grn:#3ddc84;--red:#ff6b6b;--blu:#5ab0ff;--amb:#ffc857;--vio:#b18cff}
*{box-sizing:border-box}
body{margin:0;background:radial-gradient(1200px 600px at 50% -10%,#131b2b 0,var(--bg) 60%);
color:var(--txt);font:15px/1.5 -apple-system,Segoe UI,Roboto,Helvetica,sans-serif;-webkit-font-smoothing:antialiased}
.wrap{max-width:920px;margin:0 auto;padding:18px 16px 60px}
.top{display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:6px}
.brand{font-size:20px;font-weight:800;letter-spacing:-.3px}
.brand .dot{color:var(--grn)}
.clock{font-size:12px;color:var(--mut);background:var(--surf);border:1px solid var(--line);
border-radius:20px;padding:4px 12px;display:flex;align-items:center;gap:6px}
.clock .live{width:7px;height:7px;border-radius:50%;background:var(--grn);box-shadow:0 0 8px var(--grn);animation:pulse 2s infinite}
@keyframes pulse{50%{opacity:.35}}
.tagline{color:var(--mut);font-size:13px;margin:0 0 18px}
.hero{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:24px}
.stat{background:linear-gradient(180deg,var(--surf2),var(--surf));border:1px solid var(--line);
border-radius:14px;padding:14px 14px 12px}
.stat .k{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.6px}
.stat .v{font-size:26px;font-weight:800;margin-top:3px;letter-spacing:-.5px}
.stat .s{font-size:12px;color:var(--mut);margin-top:1px}
.sect{font-size:12px;font-weight:700;color:var(--mut);text-transform:uppercase;letter-spacing:1px;
margin:26px 0 12px;display:flex;align-items:center;gap:8px}
.sect::after{content:"";flex:1;height:1px;background:var(--line)}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}
.strat{background:var(--surf);border:1px solid var(--line);border-radius:16px;padding:16px;
position:relative;overflow:hidden;transition:.2s}
.strat::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--accent,var(--blu))}
.strat h3{margin:0 0 2px;font-size:15px;display:flex;align-items:center;gap:8px}
.strat .desc{color:var(--mut);font-size:12.5px;line-height:1.45;min-height:54px;margin:6px 0 12px}
.strat .big{display:flex;align-items:baseline;gap:10px}
.strat .wr{font-size:30px;font-weight:800;letter-spacing:-1px}
.strat .roi{font-size:15px;font-weight:700}
.strat .meta{display:flex;gap:14px;margin-top:10px;padding-top:10px;border-top:1px solid var(--line);font-size:12px;color:var(--mut)}
.strat .meta b{color:var(--txt)}
.pos{color:var(--grn)}.neg{color:var(--red)}.blu{color:var(--blu)}.amb{color:var(--amb)}
.card{background:var(--surf);border:1px solid var(--line);border-radius:14px;padding:14px;margin:10px 0}
.mrow{display:flex;justify-content:space-between;align-items:center;gap:10px}
.team{font-weight:700;font-size:15px}
.badge{font-size:11px;font-weight:700;padding:3px 9px;border-radius:20px;white-space:nowrap}
.b-live{background:rgba(255,107,107,.15);color:var(--red)}
.b-ok{background:rgba(61,220,132,.14);color:var(--grn)}
.b-warn{background:rgba(255,200,87,.15);color:var(--amb)}
.b-val{background:rgba(61,220,132,.14);color:var(--grn);border:1px solid rgba(61,220,132,.35)}
.mut{color:var(--mut);font-size:13px}
.probbar{display:flex;height:26px;border-radius:8px;overflow:hidden;margin:10px 0 4px;font-size:12px;font-weight:700}
.probbar .a{background:linear-gradient(90deg,#2b6cff,#5ab0ff);display:flex;align-items:center;padding:0 8px;color:#fff}
.probbar .b{background:#26303f;display:flex;align-items:center;justify-content:flex-end;padding:0 8px;color:var(--mut);flex:1}
.val{border-color:rgba(61,220,132,.35)}
.pick{display:flex;justify-content:space-between;align-items:center;background:rgba(61,220,132,.06);
border:1px dashed rgba(61,220,132,.3);border-radius:10px;padding:8px 12px;margin-top:8px}
.pick b{font-size:15px}
.calib{display:flex;flex-direction:column;gap:4px;margin-top:8px}
.calib .lab{display:flex;justify-content:space-between;font-size:12px}
.bar{position:relative;height:10px;background:#26303f;border-radius:6px;overflow:hidden}
.bar .fill{position:absolute;left:0;top:0;bottom:0;background:linear-gradient(90deg,#2b6cff,#5ab0ff);border-radius:6px}
.bar .mark{position:absolute;top:-3px;width:2px;height:16px;background:var(--amb)}
.empty{color:var(--mut);font-style:italic;padding:14px;text-align:center;background:var(--surf);border:1px dashed var(--line);border-radius:12px}
.foot{color:var(--mut);font-size:11.5px;text-align:center;margin-top:30px;line-height:1.6}
@media(max-width:720px){.hero{grid-template-columns:repeat(2,1fr)}.grid{grid-template-columns:1fr}.strat .desc{min-height:0}}
</style></head><body><div class=wrap>''']

    P.append(f'''<div class=top><div class=brand>\U0001f3af CS2 <span class=dot>Analyst</span></div>
<div class=clock><span class=live></span>{ts} UTC</div></div>
<p class=tagline>Автоматические ставки на CS2 — модель + рыночные неэффективности. Обновление каждые 30 сек.</p>''')

    # ---- HERO ----
    P.append(f'''<div class=hero>
<div class=stat><div class=k>Всего ставок</div><div class=v>{tot_n}</div><div class=s>+{pend} ждут исхода</div></div>
<div class=stat><div class=k>Винрейт</div><div class="v pos">{tot_wr}%</div><div class=s>{tot_w} из {tot_n} зашло</div></div>
<div class=stat><div class=k>ROI</div><div class="v {roicls}">{sign(tot_roi)}%</div><div class=s>{sign(tot_pl)} ед. прибыли</div></div>
<div class=stat><div class=k>Сейчас</div><div class="v blu">{nlive}</div><div class=s>{nval} value-пик{"ов" if nval!=1 else ""}</div></div>
</div>''')

    # ---- STRATEGIES ----
    P.append('<div class=sect>\U0001f4b0 Стратегии</div><div class=grid>')
    accents = {"livelag": "var(--amb)", "prematch": "var(--vio)", "totals": "var(--grn)"}
    for key, ic, name, desc, s in strats:
        rc = "pos" if s["roi"] >= 0 else "neg"
        P.append(f'''<div class=strat style="--accent:{accents[key]}">
<h3>{ic} {name}</h3><div class=desc>{desc}</div>
<div class=big><span class="wr {('pos' if s['winrate']>=55 else '')}">{s['winrate']}%</span>
<span class="roi {rc}">ROI {sign(s['roi'])}%</span></div>
<div class=meta><span><b>{s['n']}</b> ставок</span><span><b>{sign(s['pl'])}</b> ед.</span><span>{s['pending']} ждут</span></div>
</div>''')
    P.append('</div>')

    # ---- LIVE NOW ----
    P.append('<div class=sect>\U0001f534 Идут сейчас</div>')
    if not d["live"]:
        P.append('<div class=empty>Нет live-матчей прямо сейчас</div>')
    for s in d["live"]:
        ps = s.get("p_series") or {}
        names = list(ps.keys())
        pa = int(round((ps.get(names[0], .5)) * 100)) if names else 50
        na = e(names[0]) if names else e(s.get("team1", "?"))
        nb = e(names[1]) if len(names) > 1 else e(s.get("team2", "?"))
        rt = s.get("round_total")
        rtline = ""
        if rt:
            rtline = f'<div class=pick><span class=mut>\U0001f3b2 тотал {e(rt["map"])} ~{rt["exp"]:.0f}</span><b class=blu>{e(rt["side"])} 21.5 @ {rt["fair"]}</b></div>'
        cm = f' · карта {e(s["cur_map"])}' if s.get("cur_map") else ""
        P.append(f'''<div class=card>
<div class=mrow><span class=team>{e(s.get("team1","?"))} vs {e(s.get("team2","?"))}</span>
<span class="badge b-live">LIVE · Bo{s.get("bo","?")} {e(s.get("current",""))}{cm}</span></div>
<div class=probbar><span class=a style="flex:{max(pa,12)}">{na} {pa}%</span><span class=b>{nb} {100-pa}%</span></div>
{rtline}</div>''')

    # ---- VALUE PICKS ----
    P.append('<div class=sect>\U0001f3af Ставить сейчас</div>')
    if not d["value"]:
        P.append('<div class=empty>Сейчас нет value-пиков по тоталам (ждём выгодную линию)</div>')
    for v in d["value"]:
        P.append(f'''<div class="card val">
<div class=mrow><span class=team>{e(v["t1"])} vs {e(v["t2"])}</span><span class="badge b-val">край +{v["edge"]}%</span></div>
<div class=pick><b class=pos>{e(v["side"])} {v["line"]} @ {v["odd"]}</b><span class=mut>наша вероятность {v["p"]}%</span></div>
</div>''')

    # ---- CALIBRATION ----
    P.append('<div class=sect>\U0001f4ca Точность модели <span class=mut style="text-transform:none;letter-spacing:0;font-weight:400">— совпадает ли заявленная вероятность с реальностью</span></div>')
    tr = d.get("track") or {}
    if not tr:
        P.append('<div class=empty>Данные калибровки копятся по live-матчам</div>')
    else:
        P.append('<div class=card>')
        for mk in ("series", "map", "total"):
            t = tr.get(mk)
            if not t:
                continue
            ok = abs(t["gap"]) <= 8
            badge = ('<span class="badge b-ok">✓ калибр</span>' if ok else
                     (f'<span class="badge b-warn">завышает {abs(t["gap"])}pp</span>' if t["gap"] < 0
                      else f'<span class="badge b-warn">занижает {t["gap"]}pp</span>'))
            wr = t["winrate"]
            P.append(f'''<div class=calib>
<div class=lab><span><b>{e(t["label"])}</b> · {t["n"]} ставок</span>{badge}</div>
<div class=bar><div class=fill style="width:{wr}%"></div><div class=mark style="left:{t["avg_p"]}%"></div></div>
<div class=lab><span class=mut>реально {wr}% зашло</span><span class=mut>▾ модель ждёт {t["avg_p"]}%</span></div>
</div>''')
            if mk != "total":
                P.append('<div style="height:14px"></div>')
        P.append('</div>')

    P.append(f'''<div class=foot>⚙️ Стратегии считаются автоматически по факту исходов (форвард-тест) · параметры самонастраиваются ежедневно<br>
странные матчи и value-пики приходят в Telegram · <span class=pos>зелёное = прибыль</span>, жёлтая метка = модель over/under-уверена</div>
</div></body></html>''')
    return "\n".join(P)
'''

path = "/opt/cs2-analyst/web.py"
s = open(path, encoding="utf-8").read()
import os, shutil
if not os.path.exists(path + ".bak_ui"):
    shutil.copy(path, path + ".bak_ui")
# replace the whole render() function (from 'def render' to the next top-level 'def ')
m = re.search(r"\ndef render\(d: dict\) -> str:\n", s)
assert m, "render not found"
start = m.start() + 1
nxt = re.search(r"\ndef [a-zA-Z_]", s[m.end():])
end = m.end() + nxt.start() if nxt else len(s)
s = s[:start] + NEW_RENDER + s[end:]
open(path, "w", encoding="utf-8").write(s)
print("web.py render() replaced, new length", len(s))
