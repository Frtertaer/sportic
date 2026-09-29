"""Local CS2 editorial-feed prototype. Production v2 remains untouched."""

from __future__ import annotations

import html
import re
import time
from datetime import date, timedelta

import web_ui_v2 as base


CS2_HEAD_V3 = '''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<meta name="color-scheme" content="dark"><meta name="theme-color" content="#0d1014">
<title>CS2 Analyst — матчи и сигналы</title><style>
:root{color-scheme:dark;--bg:#0d1014;--head:#11151a;--surface:#15191e;--row:#181d23;--row-hover:#1b2128;
--line:#2a3037;--line-soft:#22282f;--text:#e6e9ed;--muted:#9aa4ae;--faint:#7b8793;
--blue:#5a8db3;--blue-soft:#213240;--green:#63a87d;--red:#cf5a5f;--amber:#c7a45e;--radius:4px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-width:320px;background:var(--bg);color:var(--text);
font:13px/1.4 Arial,"Segoe UI",sans-serif;-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
a{color:inherit}.skip{position:fixed;left:12px;top:-60px;z-index:100;background:#fff;color:#111;padding:8px 10px;border-radius:2px}
.skip:focus{top:10px}.site-head{height:54px;background:var(--head);border-bottom:1px solid var(--line)}
.head-inner,.page{width:min(1180px,calc(100% - 32px));margin:0 auto}.head-inner{height:100%;display:flex;align-items:center;gap:28px}
.wordmark{display:flex;align-items:center;gap:9px;font-size:13px;font-weight:800;letter-spacing:.055em;text-decoration:none;white-space:nowrap}
.wordmark-mark{width:7px;height:22px;background:var(--blue)}.main-nav{display:flex;height:100%;align-items:stretch;gap:2px}
.main-nav a{display:flex;align-items:center;padding:0 12px;border-bottom:2px solid transparent;color:var(--muted);text-decoration:none;font-weight:600}
.main-nav a:hover,.main-nav a.active{color:var(--text);border-bottom-color:var(--blue)}
.head-fresh{margin-left:auto;color:var(--muted);font-size:12px;white-space:nowrap}.fresh-dot{display:inline-block;width:6px;height:6px;margin-right:6px;border-radius:50%;background:var(--green)}.fresh-dot.stale{background:var(--red)}
.page{padding:18px 0 48px}.page-title{display:flex;align-items:end;justify-content:space-between;gap:20px;margin-bottom:12px}
.page-title h1{margin:0;font-size:21px;line-height:1.2;letter-spacing:-.015em}.page-title p{margin:3px 0 0;color:var(--muted);font-size:12px}.utc{color:var(--faint);font-size:11px}
.summary{display:flex;align-items:stretch;min-height:48px;margin-bottom:16px;border:1px solid var(--line);background:var(--surface)}
.summary-item{display:flex;flex:1;align-items:baseline;gap:7px;min-width:0;padding:13px 18px;border-left:1px solid var(--line)}.summary-item:first-child{border-left:0}
.summary-item b{font-size:16px}.summary-item span{color:var(--muted);font-size:11px}.summary-item .positive{color:var(--green)}
.layout{display:grid;grid-template-columns:minmax(0,1fr) 286px;gap:16px;align-items:start}.main-feed{min-width:0}.rail{display:grid;gap:12px}
.section{margin-bottom:18px}.section-bar{display:flex;align-items:center;justify-content:space-between;min-height:34px;padding:0 10px;background:#12171c;border:1px solid var(--line);border-bottom:0}
.section-bar h2{margin:0;font-size:12px;letter-spacing:.045em;text-transform:uppercase}.section-bar span{color:var(--faint);font-size:11px}
.column-head{display:grid;align-items:center;min-height:28px;padding:0 10px;background:var(--surface);border:1px solid var(--line);border-bottom:0;color:var(--faint);font-size:10px;font-weight:700;letter-spacing:.045em;text-transform:uppercase}
.signal-cols{grid-template-columns:62px minmax(145px,1.25fr) minmax(150px,1.4fr) 60px 60px 68px 52px;column-gap:10px}
.signal-list,.match-list,.board-list{border:1px solid var(--line);background:var(--surface)}
.signal-row{display:grid;grid-template-columns:62px minmax(145px,1.25fr) minmax(150px,1.4fr) 60px 60px 68px 52px;column-gap:10px;align-items:center;min-height:66px;padding:8px 10px;border-left:2px solid var(--tone,var(--blue));border-bottom:1px solid var(--line-soft)}
.signal-row:last-child,.match-row:last-child,.board-row:last-child{border-bottom:0}
.tone-live{--tone:var(--red)}.tone-value{--tone:var(--green)}.tone-watch{--tone:var(--blue)}
.type{font-size:10px;font-weight:800;letter-spacing:.04em;text-transform:uppercase}.type.live{color:var(--red)}.type.value{color:var(--green)}.type.watch{color:var(--blue)}
.match-name{min-width:0}.match-name b{display:block;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-size:13px}.sub{display:block;margin-top:3px;color:var(--faint);font-size:10.5px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.recommendation{min-width:0;font-weight:700;overflow-wrap:anywhere}.recommendation small{display:block;margin-top:3px;color:var(--faint);font-size:10px;font-weight:400}
.num{text-align:right;white-space:nowrap}.num strong{font-size:12px}.mobile-prefix{display:none}.edge-pos{color:var(--green)}.edge-neg{color:var(--red)}.unknown{color:var(--faint)}
.empty{padding:22px 12px;border:1px solid var(--line);color:var(--muted);text-align:center;background:var(--surface)}
.match-cols{grid-template-columns:54px minmax(170px,1fr) 90px minmax(100px,.7fr) 118px;column-gap:12px}
.match-row{display:grid;grid-template-columns:54px minmax(170px,1fr) 90px minmax(100px,.7fr) 118px;column-gap:12px;align-items:center;min-height:60px;padding:7px 10px;border-bottom:1px solid var(--line-soft)}
.live-state{color:var(--red);font-size:10px;font-weight:800;letter-spacing:.04em}.team-stack{display:grid;gap:3px}.team-line{display:flex;justify-content:space-between;gap:8px;min-width:0}.team-line span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.team-line b{min-width:16px;text-align:right}.series-score{font-weight:700}
.match-meta{color:var(--muted);font-size:11px}.prob{display:flex;align-items:center;justify-content:space-between;gap:8px}.prob b{font-size:12px}.prob-line{grid-column:5;height:3px;background:#303740}.prob-line span{display:block;height:100%;background:var(--blue);width:var(--prob,50%)}
.board-cols{grid-template-columns:68px minmax(145px,1.25fr) minmax(126px,1fr) 54px 54px 65px 80px;column-gap:9px}
.board-row{display:grid;grid-template-columns:68px minmax(145px,1.25fr) minmax(126px,1fr) 54px 54px 65px 80px;column-gap:9px;align-items:center;min-height:61px;padding:7px 10px;border-bottom:1px solid var(--line-soft)}
.time-cell b{display:block;font-size:11px}.time-cell span{color:var(--faint);font-size:10px}.news{font-size:10px;font-weight:800;letter-spacing:.025em;text-align:right}.news.ok{color:var(--green)}.news.risk{color:var(--red)}.news.unknown{color:var(--amber)}
.date-band{padding:6px 10px;border-bottom:1px solid var(--line);background:#12171c;color:var(--muted);font-size:10px;font-weight:700;letter-spacing:.035em;text-transform:uppercase}
.rail-box{border:1px solid var(--line);background:var(--surface)}.rail-title{padding:9px 11px;border-bottom:1px solid var(--line);font-size:10px;font-weight:800;letter-spacing:.05em;text-transform:uppercase}
.rail-body{padding:10px 11px}.status-list{display:grid;grid-template-columns:1fr auto;gap:7px 12px}.status-list span{color:var(--muted)}.status-list b{text-align:right}
.strategy{padding:9px 0;border-bottom:1px solid var(--line-soft)}.strategy:last-child{border-bottom:0}.strategy-line{display:flex;justify-content:space-between;gap:8px}.strategy-line b{font-size:11px}.strategy-meta{margin-top:3px;color:var(--faint);font-size:10px}
.cal-row{padding:8px 0;border-bottom:1px solid var(--line-soft)}.cal-row:last-child{border-bottom:0}.cal-top,.cal-bottom{display:flex;justify-content:space-between;gap:8px}.cal-top b{font-size:11px}.cal-bottom{margin-top:3px;color:var(--faint);font-size:10px}
.cal-track{position:relative;height:3px;margin-top:6px;background:#303740}.cal-track span{display:block;height:100%;width:var(--actual,0%);background:var(--blue)}.cal-track i{position:absolute;top:-2px;left:var(--expected,0%);width:1px;height:7px;background:var(--amber)}
.method summary{cursor:pointer;font-weight:700}.method p{margin:8px 0 0;color:var(--muted);font-size:11px}.footnote{padding-top:10px;border-top:1px solid var(--line);color:var(--faint);font-size:10px}
:focus-visible{outline:2px solid #79a7c7;outline-offset:2px}
@media(max-width:980px){.layout{grid-template-columns:1fr}.rail{grid-template-columns:repeat(3,minmax(0,1fr))}.rail .method{grid-column:1/-1}.head-inner,.page{width:min(100% - 24px,820px)}}
@media(max-width:720px){.site-head{height:auto}.head-inner{min-height:52px;gap:14px;flex-wrap:wrap;padding:8px 0}.main-nav{order:3;width:100%;height:34px;justify-content:space-between;border-top:1px solid var(--line)}.main-nav a{padding:0 5px;font-size:11px}.head-fresh{font-size:10px}.page{padding-top:12px}.page-title{align-items:start}.page-title h1{font-size:18px}.page-title p,.utc{display:none}.summary{display:grid;grid-template-columns:repeat(2,1fr)}.summary-item{padding:10px 12px;border-top:1px solid var(--line)}.summary-item:nth-child(odd){border-left:0}.summary-item:nth-child(-n+2){border-top:0}
.column-head{display:none}.signal-row{grid-template-columns:50px minmax(0,1fr) 62px;gap:5px 8px;min-height:72px;padding:8px}.signal-row .match-name{grid-column:2}.signal-row .recommendation{grid-column:1/3;grid-row:2}.signal-row .c-model,.signal-row .c-market{display:none}.signal-row .c-edge{grid-column:3;grid-row:1;text-align:right}.signal-row .c-odd{grid-column:3;grid-row:2;text-align:right}.mobile-prefix{display:inline;color:var(--faint);font-size:10px;font-weight:400}
.match-row{grid-template-columns:46px minmax(0,1fr) 74px;gap:5px 8px;min-height:64px;padding:7px 8px}.match-row .team-stack{grid-column:2}.match-row .series-cell{display:none}.match-row .map-cell{grid-column:1/3;grid-row:2}.match-row .prob-cell{grid-column:3;grid-row:1}.prob-line{grid-column:3;grid-row:2}
.board-row{grid-template-columns:54px minmax(0,1fr) 62px;gap:5px 8px;min-height:67px;padding:7px 8px}.board-row .match-name{grid-column:2}.board-row .board-pick{grid-column:1/3;grid-row:2}.board-row .board-model,.board-row .board-market{display:none}.board-row .board-edge{grid-column:3;grid-row:1}.board-row .news{grid-column:3;grid-row:2}.match-name b{white-space:normal;display:-webkit-box;-webkit-box-orient:vertical;-webkit-line-clamp:2}
.rail{grid-template-columns:1fr}.rail .method{grid-column:auto}}
@media(max-width:400px){.head-inner,.page{width:calc(100% - 16px)}.summary-item{display:block}.summary-item span{display:block;margin-top:1px}.signal-row{grid-template-columns:46px minmax(0,1fr) 56px}.board-row{grid-template-columns:50px minmax(0,1fr) 58px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important}}
</style></head><body><a class=skip href="#main">К содержанию</a>'''


def _e(value) -> str:
    return html.escape(str(value if value is not None else ""))


_TEAM_ALIASES = {
    "pain": "paiN",
    "pain gaming": "paiN Gaming",
    "sharks": "Sharks",
    "voca": "Voca",
    "lynn vision gaming": "Lynn Vision Gaming",
    "lynn vision": "Lynn Vision",
}
_KEEP_UPPER = {"M80", "TYLOO", "NRG", "BIG", "ENCE", "FURIA", "OG", "9Z"}


def _team_name(value) -> str:
    raw = " ".join(str(value or "?").split())
    if raw.isdigit():
        return f"Команда #{raw}"
    alias = _TEAM_ALIASES.get(raw.casefold())
    if alias:
        return alias
    if raw.isupper() and raw not in _KEEP_UPPER:
        return raw.title()
    return raw


def _match_key(t1, t2) -> tuple[str, str]:
    clean = lambda value: re.sub(r"[^a-z0-9а-яё]", "", str(value or "").casefold())
    return tuple(sorted((clean(t1), clean(t2))))


def _freshness(raw_ts) -> tuple[str, str, int | None]:
    generated = base._timestamp(raw_ts)
    if generated is None:
        return "время неизвестно", "—", None
    age = max(0, int(time.time() - generated))
    fresh = f"обновлено {age} сек назад" if age < 60 else f"обновлено {max(1, age // 60)} мин назад"
    return fresh, time.strftime("%H:%M", time.gmtime(generated)), age


def _edge_label(value) -> str:
    number = base._number(value)
    return f"{number:+.1f}"


def _signal_row(kind: str, tone: str, match: str, meta: str, pick: str, pick_meta: str,
                model: str, market: str, edge: str, odd: str) -> str:
    return f'''<article class="signal-row tone-{tone}">
<div class="type {tone}">{_e(kind)}</div>
<div class=match-name><b>{_e(match)}</b><span class=sub>{_e(meta)}</span></div>
<div class=recommendation>{_e(pick)}<small>{_e(pick_meta)}</small></div>
<div class="num c-model"><strong>{_e(model)}</strong></div>
<div class="num c-market"><strong>{_e(market)}</strong></div>
<div class="num c-edge"><strong class="{'edge-pos' if edge.startswith('+') else 'edge-neg' if edge.startswith('-') else 'unknown'}"><span class=mobile-prefix>Δ </span>{_e(edge)}</strong></div>
<div class="num c-odd"><strong><span class=mobile-prefix>@</span>{_e(odd)}</strong></div></article>'''


def _signal_rows(d: dict) -> list[str]:
    rows: list[str] = []
    board_lookup = {
        _match_key(item.get("t1"), item.get("t2")): item
        for item in base._rows(d.get("prematch_board"))
    }
    steam = base._dict(d.get("steam"))
    for item in base._rows(steam.get("live")):
        entry = base._number(item.get("entry")); opened = base._number(item.get("open"))
        rows.append(_signal_row(
            "ЛИНИЯ", "live", f'{_team_name(item.get("t1"))} — {_team_name(item.get("t2"))}',
            "движение коэффициента · live", _team_name(item.get("side")),
            f'линия {base._fmt(opened, 2)} → {base._fmt(entry, 2)}',
            f'{base._fmt(item.get("strength"), 0)}% сила', "—", "—", base._fmt(entry, 2),
        ))
    for item in base._rows(d.get("value")):
        odd = base._number(item.get("odd")); model = base._pct(item.get("p")); market = base._implied(odd)
        edge = round(model - market, 1)
        rows.append(_signal_row(
            "ТОТАЛ", "value", f'{_team_name(item.get("t1"))} — {_team_name(item.get("t2"))}',
            "тотал раундов · прематч", f'{item.get("side") or "?"} {base._fmt(item.get("line"))}',
            "основная линия", f'{base._fmt(model)}%', f'{base._fmt(market)}%',
            f'{edge:+.1f}', base._fmt(odd, 2),
        ))
    for item in base._rows(d.get("prematch_picks")):
        anti = item.get("kind") == "anti"; odd = base._number(item.get("odd"))
        has_p = item.get("p") not in (None, "")
        if has_p:
            model = base._pct(item.get("p")); market = base._implied(odd); edge = round(model - market, 1)
            model_label, market_label = f'{base._fmt(model)}%', f'{base._fmt(market)}%'
        else:
            board_row = board_lookup.get(_match_key(item.get("t1"), item.get("t2")))
            if board_row and board_row.get("pm_fav") is not None and board_row.get("pf_fav") is not None:
                pf = base._pct(base._number(board_row.get("pf_fav")) * 100)
                pm = base._pct(base._number(board_row.get("pm_fav")) * 100)
                favorite = str(board_row.get("fav") or ""); side = str(item.get("side") or "")
                if side and favorite and side.casefold() != favorite.casefold():
                    model, market = 100 - pm, 100 - pf
                else:
                    model, market = pm, pf
                edge = round(model - market, 1)
                model_label, market_label = f'{base._fmt(model)}%', f'{base._fmt(market)}%'
            else:
                edge = round(base._number(item.get("edge")), 1); model_label = market_label = "—"
        pick = f'{_team_name(item.get("side"))} +1.5 карты' if anti else f'{_team_name(item.get("side"))} победа'
        rows.append(_signal_row(
            "+1.5" if anti else "VALUE", "watch" if anti else "value",
            f'{_team_name(item.get("t1"))} — {_team_name(item.get("t2"))}',
            "защита от 0:2 · BO3+" if anti else "прематч · победитель", pick,
            "минимум одна карта" if anti else "сравнение с нормализованной линией",
            model_label, market_label, f'{edge:+.1f}', base._fmt(odd, 2),
        ))
    return rows


def _live_row(match: dict) -> str:
    raw_team1, raw_team2 = str(match.get("team1") or "?"), str(match.get("team2") or "?")
    team1, team2 = _team_name(raw_team1), _team_name(raw_team2)
    probs = base._dict(match.get("p_series")); pa = base._pct(base._number(probs.get(raw_team1), .5) * 100, 50)
    pb = round(100 - pa, 1); current = str(match.get("current") or "—")
    bo = max(1, int(base._number(match.get("bo"), 3))); cur_map = str(match.get("cur_map") or "карта уточняется")
    score_match = re.search(r"(\d+)\s*[-:]\s*(\d+)", current)
    score1, score2 = score_match.groups() if score_match else ("", "")
    return f'''<article class=match-row><div class=live-state>LIVE</div>
<div class=team-stack><div class=team-line><span>{_e(team1)}</span><b>{_e(score1)}</b></div><div class=team-line><span>{_e(team2)}</span><b>{_e(score2)}</b></div></div>
<div class="match-meta series-cell"><b class=series-score>BO{bo}</b><span class=sub>{'решающая карта' if match.get('is_decider') else 'серия'}</span></div>
<div class="match-meta map-cell">{_e(cur_map)}</div>
<div class="prob-cell"><div class=prob><b>{base._fmt(pa)}%</b><span>—</span><b>{base._fmt(pb)}%</b></div></div>
<div class=prob-line style="--prob:{base._fmt(pa)}%"><span></span></div></article>'''


def _news_status(row: dict) -> tuple[str, str, str]:
    flag = base._clean_display(row.get("news_flag"))
    reason = base._clean_display(row.get("news_reason"))
    raw = f"{flag}: {reason}".strip(" :") if flag else base._clean_display(row.get("news"))
    match = re.match(r"^(RED|GREEN)\s*:?\s*(.*)$", raw, flags=re.IGNORECASE)
    if match and match.group(1).upper() == "RED":
        reason = match.group(2).strip(" -:.")
        return "РИСК", "risk", reason if re.search(r"[а-яё]", reason, re.IGNORECASE) else "есть фактор риска"
    if match and match.group(1).upper() == "GREEN":
        return "ПРОВЕРЕНО", "ok", ""
    return "НЕТ ДАННЫХ", "unknown", "" if raw.upper() == "UNKNOWN" else raw


_MONTHS = ("", "января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря")


def _board_date(value, today: date) -> tuple[str, str]:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    if not match:
        return "9999-99-99", "Дата уточняется"
    year, month, day = map(int, match.groups())
    try:
        current = date(year, month, day)
    except ValueError:
        return "9999-99-99", "Дата уточняется"
    prefix = "Сегодня · " if current == today else "Завтра · " if current == today + timedelta(days=1) else ""
    return current.isoformat(), f"{prefix}{day} {_MONTHS[month]} · UTC"


def _board_row(row: dict) -> str:
    favorite = str(row.get("fav") or ""); bet_side = str(row.get("bet_side") or "")
    pf = base._pct(base._number(row.get("pf_fav")) * 100); pm = base._pct(base._number(row.get("pm_fav")) * 100)
    if bet_side and favorite and bet_side.casefold() != favorite.casefold():
        model, market, side = 100 - pm, 100 - pf, bet_side
    else:
        model, market, side = pm, pf, favorite or bet_side or "—"
    edge = round(model - market, 1); news, news_cls, reason = _news_status(row)
    start = base._start_label(row.get("start_date")); day, _, clock = start.partition(" · ")
    odd = base._number(row.get("bet_odd")); pick = f'{side} @ {base._fmt(odd, 2)}' if bet_side else "без ставки"
    verdict = str(row.get("verdict") or "PASS").upper()
    verdict_label = {"VALUE": "есть value", "SUSPICIOUS": "аномалия", "PASS": "без сигнала"}.get(verdict, "без сигнала")
    return f'''<article class=board-row><div class=time-cell><b>{_e(clock or "—")}</b><span>{_e(day)}</span></div>
<div class=match-name><b>{_e(_team_name(row.get("t1")))} — {_e(_team_name(row.get("t2")))}</b><span class=sub>BO{max(1, int(base._number(row.get("bo"), 3)))} · {_e(verdict_label)}</span></div>
<div class="recommendation board-pick">{_e(_team_name(side) if bet_side else pick)}{f' @ {base._fmt(odd, 2)}' if bet_side else ''}<small>{_e(reason)}</small></div>
<div class="num board-model"><strong>{base._fmt(model)}%</strong></div><div class="num board-market"><strong>{base._fmt(market)}%</strong></div>
<div class="num board-edge"><strong class="{'edge-pos' if edge >= 0 else 'edge-neg'}"><span class=mobile-prefix>Δ </span>{edge:+.1f}</strong></div>
<div class="news {news_cls}">{news}</div></article>'''


def _strategy_rows(d: dict) -> str:
    items = (("Live-линия", base._dict(d.get("livelag"))), ("Прематч", base._dict(d.get("prematch"))),
             ("Тоталы", base._dict(d.get("roi"))))
    out = []
    for name, item in items:
        roi = base._number(item.get("roi")); cls = "edge-pos" if roi >= 0 else "edge-neg"
        out.append(f'''<div class=strategy><div class=strategy-line><span>{_e(name)}</span><b class={cls}>{roi:+.1f}% ROI</b></div>
<div class=strategy-meta>{int(base._number(item.get("n")))} ставок · {base._fmt(item.get("winrate"),0)}% WR · {int(base._number(item.get("pending")))} ждут</div></div>''')
    return "".join(out)


def _calibration_rows(track: dict) -> str:
    out = []
    for key in ("series", "map", "total"):
        item = base._dict(track.get(key))
        if not item:
            continue
        actual = base._pct(item.get("winrate")); expected = base._pct(item.get("avg_p"))
        out.append(f'''<div class=cal-row><div class=cal-top><b>{_e(item.get("label") or key)}</b><span>{base._fmt(actual)} / {base._fmt(expected)}%</span></div>
<div class=cal-track style="--actual:{base._fmt(actual)}%;--expected:{base._fmt(expected)}%"><span></span><i></i></div>
<div class=cal-bottom><span>{int(base._number(item.get("n")))} ставок</span><span>факт / модель</span></div></div>''')
    return "".join(out) or '<div class=unknown>Данные копятся</div>'


def render(d: dict) -> str:
    d = base._dict(d); fresh, utc, age = _freshness(d.get("ts"))
    live = base._rows(d.get("live")); board = sorted(base._rows(d.get("prematch_board")), key=lambda item: str(item.get("start_date") or "9999")); signals = _signal_rows(d)
    generated = base._timestamp(d.get("ts")) or time.time()
    now_utc = time.gmtime(generated); today = date(now_utc.tm_year, now_utc.tm_mon, now_utc.tm_mday)
    strategies = [base._dict(d.get(key)) for key in ("livelag", "prematch", "roi")]
    settled = int(sum(base._number(item.get("n")) for item in strategies)); won = int(sum(base._number(item.get("wins")) for item in strategies))
    pending = int(sum(base._number(item.get("pending")) for item in strategies)); profit = round(sum(base._number(item.get("pl")) for item in strategies), 2)
    wr = round(won / settled * 100) if settled else 0; roi = round(profit / settled * 100, 1) if settled else 0
    status = "данные актуальны" if age is not None and age < 180 else "нужно обновление"
    P = [CS2_HEAD_V3]
    P.append(f'''<header class=site-head><div class=head-inner><a class=wordmark href="/"><span class=wordmark-mark></span>CS2 ANALYST</a>
<nav class=main-nav aria-label="Разделы"><a class=active href="#main">Обзор</a><a href="#signals">Сигналы</a><a href="#matches">Матчи</a><a href="#accuracy">Статистика</a><a href="/football">Футбол</a></nav>
<div class=head-fresh><span class="fresh-dot{' stale' if age is None or age >= 180 else ''}"></span>{_e(fresh)}</div></div></header><main class=page id=main>
<div class=page-title><div><h1>Матчи и сигналы</h1><p>Текущие рекомендации, live-состояние и проверка модели</p></div><span class=utc>{_e(utc)} UTC</span></div>
<section class=summary aria-label="Сводка"><div class=summary-item><b>{settled}</b><span>ставок</span></div><div class=summary-item><b>{wr}%</b><span>WR · forward-test</span></div>
<div class=summary-item><b class="{'positive' if roi >= 0 else 'edge-neg'}">{roi:+.1f}%</b><span>ROI · forward-test</span></div><div class=summary-item><b>{len(signals)}</b><span>активных</span></div></section>
<div class=layout><div class=main-feed>''')
    P.append(f'''<section class=section id=signals><div class=section-bar><h2>Рекомендации</h2><span>{len(signals)}</span></div>
<div class="column-head signal-cols"><span>Тип</span><span>Матч</span><span>Рекомендация</span><span class=num>Модель</span><span class=num>Рынок</span><span class=num>Δ п.п.</span><span class=num>Кэф</span></div>''')
    if signals:
        P.append('<div class=signal-list>' + "".join(signals) + '</div>')
    else:
        P.append('<div class=empty>Сигналов с достаточным преимуществом нет.</div>')
    P.append(f'''</section><section class=section id=matches><div class=section-bar><h2>Live-матчи</h2><span>{len(live)}</span></div>
<div class="column-head match-cols"><span>Статус</span><span>Команды</span><span>Формат</span><span>Карта</span><span>Вероятность</span></div>''')
    if live:
        P.append('<div class=match-list>' + "".join(_live_row(item) for item in live) + '</div>')
    else:
        P.append('<div class=empty>Сейчас нет live-матчей.</div>')
    P.append(f'''</section><section class=section id=upcoming><div class=section-bar><h2>Ближайшие матчи</h2><span>{len(board)}</span></div>
<div class="column-head board-cols"><span>Время</span><span>Матч</span><span>Выбор</span><span class=num>Модель</span><span class=num>Бук</span><span class=num>Δ п.п.</span><span class=num>Новости</span></div>''')
    if board:
        grouped = []
        current_group = None
        for item in board:
            group_key, group_label = _board_date(item.get("start_date"), today)
            if group_key != current_group:
                grouped.append(f'<div class=date-band>{_e(group_label)}</div>')
                current_group = group_key
            grouped.append(_board_row(item))
        P.append('<div class=board-list>' + "".join(grouped) + '</div>')
    else:
        P.append('<div class=empty>Ближайшие матчи пока не рассчитаны.</div>')
    P.append('</section></div><aside class=rail>')
    P.append(f'''<section class=rail-box><div class=rail-title>Состояние</div><div class=rail-body><div class=status-list>
<span>Обновление</span><b class="{'edge-pos' if age is not None and age < 180 else 'edge-neg'}">{_e(status)}</b><span>Live</span><b>{len(live)}</b><span>Сигналы</span><b>{len(signals)}</b><span>Прибыль</span><b class="{'edge-pos' if profit >= 0 else 'edge-neg'}">{profit:+.2f}u</b>
</div></div></section><section class=rail-box><div class=rail-title>Стратегии</div><div class=rail-body>{_strategy_rows(d)}</div></section>''')
    P.append(f'''<section class=rail-box id=accuracy><div class=rail-title>Калибровка</div><div class=rail-body>{_calibration_rows(base._dict(d.get("track")))}</div></section>
<details class="rail-box method"><summary class=rail-title>Методика</summary><div class=rail-body><p>Forward-test без удаления проигрышей. Δ — разница вероятности модели и нормализованной оценки рынка. Сигнал не гарантирует исход.</p></div></details></aside></div>
<footer class=footnote>Данные обновляются автоматически каждые 30 секунд · используйте фиксированный размер ставки</footer></main></body></html>''')
    return "\n".join(P)


render_football = base.render_football
render_v2 = base.render
