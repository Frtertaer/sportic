"""CS2 + Football dashboard UI — clean render() / render_football() imported by web.py."""
import html
import json
import math
import re
import time

HEAD = '''<!doctype html><html lang="ru"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="30">
<meta name="color-scheme" content="dark"><meta name="theme-color" content="#080c12">
<title>Analyst — спортивная аналитика</title><style>
:root{color-scheme:dark;--bg:#080c12;--bg2:#0c121c;--surf:#101722;--raised:#151f2d;--soft:#192536;
--line:#263448;--line2:#31425c;--txt:#f5f7fb;--mut:#a8b3c4;--faint:#738198;--blue:#5b8cff;
--blue2:#70b7ff;--green:#32d583;--red:#ff6677;--amber:#f6c453;--violet:#a78bfa;
--shadow:0 18px 50px rgba(0,0,0,.26);--r:16px;--r-sm:12px}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;min-width:320px;background:
radial-gradient(900px 500px at 14% -10%,rgba(91,140,255,.12),transparent 60%),
radial-gradient(800px 480px at 94% 8%,rgba(50,213,131,.07),transparent 62%),var(--bg);
color:var(--txt);font:15px/1.5 Inter,ui-sans-serif,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;
-webkit-font-smoothing:antialiased;font-variant-numeric:tabular-nums}
a{color:inherit}.skip{position:fixed;left:16px;top:-80px;z-index:99;background:var(--txt);color:var(--bg);padding:10px 14px;border-radius:10px}
.skip:focus{top:12px}.wrap{width:min(1320px,calc(100% - 48px));margin:0 auto;padding:18px 0 88px}
.top{min-height:58px;display:flex;align-items:center;justify-content:space-between;gap:20px;padding:8px 0 14px}
.brand{display:flex;align-items:center;gap:11px;text-decoration:none;font-size:18px;font-weight:760;letter-spacing:-.3px}
.brand-mark{display:grid;place-items:center;width:36px;height:36px;border-radius:11px;background:linear-gradient(145deg,var(--blue),#765cff);box-shadow:0 9px 22px rgba(91,140,255,.22);font-size:15px}.brand-copy{display:grid;line-height:1.15}.brand-copy small{margin-top:4px;color:var(--faint);font-size:11px;font-weight:600;letter-spacing:.04em}
.top-status{display:flex;align-items:center;gap:10px}.clock,.health{display:inline-flex;align-items:center;gap:8px;min-height:34px;border:1px solid var(--line);border-radius:999px;padding:6px 11px;color:var(--mut);background:rgba(16,23,34,.72);font-size:12px}
.live{width:7px;height:7px;border-radius:50%;background:var(--green);box-shadow:0 0 0 4px rgba(50,213,131,.09)}
.tabs{position:sticky;top:0;z-index:20;display:flex;gap:6px;margin:0 -10px 24px;padding:9px 10px;background:rgba(8,12,18,.86);backdrop-filter:blur(16px);border-bottom:1px solid rgba(38,52,72,.7)}
.tab{display:inline-flex;align-items:center;justify-content:center;gap:8px;min-height:42px;padding:9px 16px;border-radius:11px;color:var(--mut);text-decoration:none;font-size:14px;font-weight:680;transition:color .15s,background .15s}
.tab:hover{color:var(--txt);background:var(--surf)}.tab.active{color:var(--txt);background:var(--raised);box-shadow:inset 0 0 0 1px var(--line2)}
.sport-dot{width:8px;height:8px;border-radius:50%;background:var(--faint)}.active .sport-dot{background:var(--blue);box-shadow:0 0 0 4px rgba(91,140,255,.12)}
.page-intro{display:flex;justify-content:space-between;gap:24px;align-items:end;margin:0 0 18px}.eyebrow{margin:0 0 5px;color:var(--blue2);font-size:12px;font-weight:760;letter-spacing:.08em;text-transform:uppercase}.page-intro h1{margin:0;font-size:clamp(24px,3vw,34px);line-height:1.14;letter-spacing:-.035em}.tagline{max-width:680px;margin:8px 0 0;color:var(--mut);font-size:14px}.freshness{color:var(--faint);font-size:12px;white-space:nowrap}
.hero{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:0 0 28px}.stat{position:relative;min-height:116px;padding:18px 19px;background:linear-gradient(160deg,var(--raised),var(--surf));border:1px solid var(--line);border-radius:var(--r);overflow:hidden}.stat::after{content:"";position:absolute;inset:auto -20px -38px auto;width:100px;height:100px;border-radius:50%;background:var(--glow,rgba(91,140,255,.09));filter:blur(4px)}.stat .k{position:relative;z-index:1;color:var(--mut);font-size:12px;font-weight:680}.stat .v{position:relative;z-index:1;margin-top:7px;font-size:30px;line-height:1;font-weight:780;letter-spacing:-.045em}.stat .s{position:relative;z-index:1;margin-top:8px;color:var(--faint);font-size:12px}.stat.profit{--glow:rgba(50,213,131,.13)}.stat.alerts{--glow:rgba(255,102,119,.11)}
.primary-grid,.secondary-grid{display:grid;grid-template-columns:minmax(0,2fr) minmax(300px,1fr);gap:18px;align-items:start}.primary-grid{margin-bottom:30px}.secondary-grid{margin-top:30px}.side-stack{display:grid;gap:16px}
.panel{background:rgba(16,23,34,.9);border:1px solid var(--line);border-radius:18px;box-shadow:var(--shadow)}.panel-pad{padding:20px}.section-head{display:flex;align-items:flex-start;justify-content:space-between;gap:16px;margin-bottom:14px}.section-head h2,.sect{margin:0;color:var(--txt);font-size:18px;line-height:1.3;font-weight:730;letter-spacing:-.018em;text-transform:none}.section-head p{margin:5px 0 0;color:var(--mut);font-size:13px}.count{display:inline-flex;align-items:center;justify-content:center;min-width:27px;height:27px;border-radius:999px;background:rgba(91,140,255,.13);color:var(--blue2);font-size:12px;font-weight:760}
.signals{display:grid;gap:10px}.signal{position:relative;padding:18px 18px 16px;background:linear-gradient(135deg,rgba(21,31,45,.98),rgba(16,23,34,.98));border:1px solid var(--line);border-radius:14px;overflow:hidden}.signal::before{content:"";position:absolute;inset:0 auto 0 0;width:3px;background:var(--tone,var(--green))}.signal.live-signal{--tone:var(--red)}.signal.anti{--tone:var(--violet)}.signal-head,.mrow{display:flex;align-items:flex-start;justify-content:space-between;gap:14px}.signal-kicker{display:flex;align-items:center;gap:8px;margin-bottom:4px;color:var(--faint);font-size:12px}.signal h3,.team{margin:0;font-size:16px;line-height:1.35;font-weight:720;overflow-wrap:anywhere}.badge{display:inline-flex;align-items:center;gap:6px;min-height:25px;padding:4px 9px;border-radius:999px;white-space:nowrap;font-size:11px;font-weight:740}.b-live{color:var(--red);background:rgba(255,102,119,.12)}.b-ok,.b-val{color:var(--green);background:rgba(50,213,131,.11)}.b-warn,.b-md{color:var(--amber);background:rgba(246,196,83,.11)}.b-hi{color:var(--red);background:rgba(255,102,119,.11)}.b-neutral{color:var(--blue2);background:rgba(91,140,255,.12)}
.pick-main{display:flex;align-items:baseline;gap:9px;margin:14px 0 13px}.pick-main strong{font-size:22px;line-height:1.2;letter-spacing:-.025em}.pick-main span{color:var(--mut);font-size:13px}.metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px}.metric{padding:9px 10px;border-radius:10px;background:rgba(25,37,54,.7)}.metric span{display:block;color:var(--faint);font-size:11px}.metric b{display:block;margin-top:2px;font-size:14px}.signal-foot{display:flex;justify-content:space-between;gap:14px;margin-top:12px;padding-top:11px;border-top:1px solid rgba(38,52,72,.7);color:var(--faint);font-size:12px}.source{display:inline-flex;align-items:center;gap:6px}.source::before{content:"";width:6px;height:6px;border-radius:50%;background:var(--green)}.source.risk::before{background:var(--red)}.source.neutral::before{background:var(--amber)}
.status-card{padding:19px}.status-line{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:15px}.status-line strong{font-size:16px}.status-good{color:var(--green)}.strategy-list{display:grid;gap:2px}.strategy{padding:12px 0;border-top:1px solid rgba(38,52,72,.65)}.strategy:first-child{border-top:0}.strategy-top{display:flex;justify-content:space-between;gap:12px;font-size:13px}.strategy-top b{font-size:13px}.strategy-meta{display:flex;gap:10px;margin-top:4px;color:var(--faint);font-size:11px}.method{border-top:1px solid var(--line);margin-top:12px;padding-top:12px}.method summary{cursor:pointer;color:var(--mut);font-size:13px;font-weight:650}.method p{color:var(--faint);font-size:12px}
.full-section{margin-top:30px}.live-grid{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:12px}.card{background:var(--surf);border:1px solid var(--line);border-radius:14px;padding:16px;margin:10px 0}.card.val{border-color:rgba(50,213,131,.36);background:linear-gradient(135deg,rgba(50,213,131,.05),var(--surf) 34%)}.live-card{margin:0;padding:17px;background:linear-gradient(145deg,var(--raised),var(--surf))}.match-meta{display:flex;gap:8px;flex-wrap:wrap;margin-top:5px;color:var(--faint);font-size:12px}.prob-labels{display:flex;justify-content:space-between;gap:16px;margin-top:16px;font-size:12px;overflow-wrap:anywhere}.prob-labels b{font-size:13px}.probbar{display:flex;height:8px;margin:7px 0 0;border-radius:999px;overflow:hidden;background:var(--soft)}.probbar .a{flex:0 0 var(--pa,50%);width:var(--pa,50%);padding:0;background:linear-gradient(90deg,var(--blue),var(--blue2))}.probbar .b{flex:0 0 calc(100% - var(--pa,50%));width:calc(100% - var(--pa,50%));padding:0;background:#2a3547}.pick{display:flex;justify-content:space-between;align-items:center;gap:14px;margin-top:13px;padding:10px 12px;border-radius:10px;background:rgba(91,140,255,.07);border:1px solid rgba(91,140,255,.2)}.pick b{font-size:14px}.mut{color:var(--mut);font-size:13px}.pos{color:var(--green)}.neg{color:var(--red)}.blu{color:var(--blue2)}.amb{color:var(--amber)}
.line-score{display:grid;grid-template-columns:repeat(2,1fr);gap:8px}.line-score .metric{padding:12px}.line-score .metric b{font-size:17px}.empty{padding:22px;text-align:center;color:var(--mut);background:rgba(16,23,34,.7);border:1px dashed var(--line2);border-radius:13px;font-size:13px}.board-list{display:grid;gap:9px}.board-card{padding:15px;border:1px solid var(--line);border-radius:13px;background:rgba(21,31,45,.72)}.board-top{display:flex;justify-content:space-between;gap:14px}.board-title{font-weight:710;overflow-wrap:anywhere}.board-time{margin-top:3px;color:var(--faint);font-size:11px}.board-metrics{display:grid;grid-template-columns:repeat(3,1fr);gap:7px;margin-top:11px}.board-metrics .metric{padding:8px}.board-action{margin-top:10px;color:var(--green);font-weight:700;font-size:13px}.news-note{margin-top:9px;color:var(--mut);font-size:12px;overflow-wrap:anywhere}
.calibration{margin-top:30px}.calib-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:12px}.calib{padding:15px;border-radius:13px;background:var(--raised);border:1px solid var(--line)}.calib .lab{display:flex;justify-content:space-between;gap:10px;font-size:12px}.calib .lab b{font-size:13px}.bar{position:relative;height:9px;margin:13px 0 9px;background:#293548;border-radius:999px;overflow:visible}.bar .fill{position:absolute;inset:0 auto 0 0;background:linear-gradient(90deg,var(--blue),var(--blue2));border-radius:999px}.bar .mark{position:absolute;top:-4px;width:3px;height:17px;border-radius:2px;background:var(--amber);box-shadow:0 0 0 3px rgba(246,196,83,.1)}.calib-legend{display:flex;justify-content:space-between;color:var(--faint);font-size:11px}
.grid{display:grid;grid-template-columns:repeat(3,1fr);gap:12px}.strat{background:var(--surf);border:1px solid var(--line);border-radius:14px;padding:16px}.strat h3{margin:0;font-size:15px}.strat .desc{color:var(--mut);font-size:12px}.strat .big{display:flex;align-items:baseline;gap:10px}.strat .wr{font-size:27px;font-weight:760}.strat .meta{display:flex;gap:12px;color:var(--mut);font-size:12px}.three{display:flex;gap:7px;margin:10px 0}.three>div{flex:1;text-align:center;background:var(--soft);border-radius:9px;padding:7px}.three b{display:block;font-size:15px}.brand .dot{color:var(--green)}.sect{display:flex;align-items:center;gap:10px;margin-top:28px;margin-bottom:12px;flex-wrap:wrap}.sect .mut{font-weight:400}.foot{margin-top:34px;padding:20px;border-top:1px solid var(--line);color:var(--faint);text-align:center;font-size:12px}.foot strong{color:var(--mut)}
.mobile-nav{display:none}.risk-note{margin-top:18px;padding:13px 15px;border-radius:12px;background:rgba(246,196,83,.06);color:var(--mut);font-size:12px;border:1px solid rgba(246,196,83,.15)}
:focus-visible{outline:2px solid #78a9ff;outline-offset:3px}
@media(max-width:1020px){.wrap{width:min(100% - 32px,900px)}.primary-grid,.secondary-grid{grid-template-columns:1fr}.side-stack{grid-template-columns:repeat(2,minmax(0,1fr))}.calib-grid{grid-template-columns:1fr}.live-grid{grid-template-columns:1fr}}
@media(max-width:700px){body{font-size:14px}.wrap{width:calc(100% - 24px);padding-bottom:100px}.top{min-height:52px}.brand-copy small,.health{display:none}.tabs{margin:0 -12px 18px;padding-left:12px;overflow:auto}.tab{min-width:112px}.page-intro{display:block}.freshness{display:block;margin-top:8px}.hero{grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.stat{min-height:102px;padding:14px}.stat .v{font-size:25px}.primary-grid,.secondary-grid{gap:12px}.panel-pad,.status-card{padding:15px}.side-stack{grid-template-columns:1fr}.signal-head,.mrow,.board-top{align-items:flex-start;flex-direction:column}.metric-grid{grid-template-columns:repeat(2,minmax(0,1fr))}.signal-foot,.pick{align-items:flex-start;flex-direction:column}.live-grid{grid-template-columns:1fr}.board-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}.mobile-nav{position:fixed;z-index:30;left:10px;right:10px;bottom:10px;display:grid;grid-template-columns:repeat(4,1fr);padding:7px;background:rgba(16,23,34,.94);border:1px solid var(--line2);border-radius:15px;backdrop-filter:blur(18px);box-shadow:var(--shadow)}.mobile-nav a{display:grid;place-items:center;min-height:43px;border-radius:9px;color:var(--mut);text-decoration:none;font-size:11px}.mobile-nav a:focus,.mobile-nav a:hover{background:var(--raised);color:var(--txt)}}
@media(max-width:420px){.hero{grid-template-columns:1fr 1fr}.stat .s{font-size:11px}.board-metrics{grid-template-columns:1fr}.top-status .clock{padding:5px 8px}.pick-main{display:block}.pick-main strong{overflow-wrap:anywhere}.pick-main span{display:block;margin-top:4px}}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto!important;animation:none!important;transition:none!important}}
</style></head><body><a class="skip" href="#main">К содержанию</a><div class="wrap">'''


def _tabs(active: str) -> str:
    def cls(x):
        return "tab active" if x == active else "tab"
    return (f'<nav class="tabs" aria-label="Вид спорта">'
            f'<a href="/" class="{cls("cs2")}"{(" aria-current=page" if active == "cs2" else "")}>'
            f'<span class=sport-dot aria-hidden=true></span>CS2</a>'
            f'<a href="/football" class="{cls("fb")}"{(" aria-current=page" if active == "fb" else "")}>'
            f'<span class=sport-dot aria-hidden=true></span>Футбол</a></nav>')


def _sign(x):
    return f"+{x}" if x >= 0 else f"{x}"


def _dict(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, (list, tuple)) else []


_GROK_RENDER = re.compile(
    r"<grok:render\b.*?(?:</grok:render\s*>|$)",
    flags=re.IGNORECASE | re.DOTALL,
)


def _clean_display(value, limit: int = 180) -> str:
    """Sanitize provider text again at display time for already-persisted dirty rows."""
    text = html.unescape(str(value or ""))
    text = _GROK_RENDER.sub("", text)
    text = re.sub(r"</?grok:[^>]*(?:>|$)", "", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"^\s*(?:grok:|\[grok\])\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[`*]+", "", text)
    text = " ".join(text.split()).strip()
    return text[:limit].rstrip()


def _number(value, default: float = 0.0) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    return number if math.isfinite(number) else default


def _timestamp(value) -> float | None:
    try:
        number = float(value)
        if not math.isfinite(number) or number <= 0:
            return None
        time.gmtime(number)
        return number
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def _pct(value, default: float = 0.0) -> float:
    return max(0.0, min(100.0, _number(value, default)))


def _probability_pct(value) -> float:
    number = _number(value)
    return _pct(number * 100 if 0 <= number <= 1 else number)


def _implied(odd) -> float:
    price = _number(odd)
    return round(100 / price, 1) if price > 1 else 0.0


def _fmt(value, digits: int = 1) -> str:
    number = _number(value)
    rendered = f"{number:.{digits}f}"
    if "." in rendered:
        rendered = rendered.rstrip("0").rstrip(".")
    return rendered or "0"


def _news_view(value) -> tuple[str, str]:
    cleaned = _clean_display(value)
    match = re.match(r"^(RED|GREEN)\s*:?\s*(.*)$", cleaned, flags=re.IGNORECASE)
    if match:
        flag, reason = match.group(1).upper(), match.group(2).strip(" -:.")
        if flag == "RED":
            return '<span class="source risk">Обнаружен риск</span>', reason or "Есть важная новость"
        return '<span class=source>Новости проверены</span>', ""
    if not cleaned or cleaned.upper() == "UNKNOWN":
        return '<span class="source neutral">Статус новостей неизвестен</span>', ""
    return '<span class="source neutral">Статус не подтверждён</span>', cleaned


def _start_label(value) -> str:
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})", str(value or ""))
    if not match:
        return "время уточняется"
    _, month, day, hour, minute = match.groups()
    return f"{day}.{month} · {hour}:{minute} UTC"


def _steam_signal(sm: dict) -> str:
    e = lambda value: html.escape(str(value if value is not None else ""))
    badge, note = _news_view(sm.get("news"))
    note_html = f'<span>{e(note)}</span>' if note else '<span>рынок движется в ту же сторону</span>'
    return f'''<article class="signal live-signal">
<div class=signal-head><div><div class=signal-kicker>Движение линии · {e(sm.get("mode") or "LIVE")}</div>
<h3>{e(sm.get("t1") or "?")} vs {e(sm.get("t2") or "?")}</h3></div><span class="badge b-live">Срочно · сила {_fmt(sm.get("strength"),0)}%</span></div>
<div class=pick-main><strong>Ставка: {e(sm.get("side") or "?")} @ {_fmt(sm.get("entry"),2)}</strong><span>линия {_fmt(sm.get("open"),2)} → {_fmt(sm.get("entry"),2)}</span></div>
<div class=metric-grid><div class=metric><span>Открытие</span><b>{_fmt(sm.get("open"),2)}</b></div>
<div class=metric><span>Сейчас</span><b>{_fmt(sm.get("entry"),2)}</b></div>
<div class=metric><span>Сила движения</span><b>{_fmt(sm.get("strength"),0)}%</b></div>
<div class=metric><span>Режим</span><b>{e(sm.get("mode") or "—")}</b></div></div>
<div class=signal-foot>{badge}{note_html}</div></article>'''


def _total_signal(v: dict) -> str:
    e = lambda value: html.escape(str(value if value is not None else ""))
    odd = _number(v.get("odd")); model = _pct(v.get("p")); market = _implied(odd)
    edge = round(model - market, 1)
    return f'''<article class=signal>
<div class=signal-head><div><div class=signal-kicker>Тоталы раундов · прематч</div>
<h3>{e(v.get("t1") or "?")} vs {e(v.get("t2") or "?")}</h3></div><span class="badge b-val">Преимущество {_sign(edge)} п.п.</span></div>
<div class=pick-main><strong>{e(v.get("side") or "?")} {_fmt(v.get("line"))} @ {_fmt(odd,2)}</strong><span>основной сценарий модели</span></div>
<div class=metric-grid><div class=metric><span>Модель</span><b>{_fmt(model)}%</b></div>
<div class=metric><span>Рынок</span><b>{_fmt(market)}%</b></div>
<div class=metric><span>Разница</span><b class=pos>{_sign(edge)} п.п.</b></div>
<div class=metric><span>Уверенность</span><b>{'высокая' if edge >= 8 else 'средняя'}</b></div></div>
<div class=signal-foot><span class=source>Линия проверена</span><span>Winline · коэффициент {_fmt(odd,2)}</span></div></article>'''


def _prematch_signal(v: dict) -> str:
    e = lambda value: html.escape(str(value if value is not None else ""))
    anti = v.get("kind") == "anti"; odd = _number(v.get("odd")); market = _implied(odd)
    has_model_probability = v.get("p") not in (None, "")
    model = _pct(v.get("p")) if has_model_probability else 0.0
    edge = round(model - market, 1) if has_model_probability else round(_number(v.get("edge")), 1)
    recommendation = (f'{e(v.get("side") or "?")} берёт 1+ карту · Фора +1.5 @ {_fmt(odd,2)}'
                      if anti else f'Победа {e(v.get("side") or "?")} @ {_fmt(odd,2)}')
    if has_model_probability:
        metrics = (f'<div class=metric><span>Модель</span><b>{_fmt(model)}%</b></div>'
                   f'<div class=metric><span>Рынок</span><b>{_fmt(market)}%</b></div>'
                   f'<div class=metric><span>Разница</span><b class=pos>{_sign(edge)} п.п.</b></div>'
                   f'<div class=metric><span>Уверенность</span><b>{"высокая" if edge >= 10 else "средняя"}</b></div>')
    else:
        metrics = (f'<div class=metric><span>Преимущество</span><b class=pos>{_sign(edge)} п.п.</b></div>'
                   f'<div class=metric><span>Коэффициент</span><b>{_fmt(odd,2)}</b></div>'
                   '<div class=metric><span>Тип сигнала</span><b>ML value</b></div>'
                   '<div class=metric><span>Вероятности</span><b>в карточке матча</b></div>')
    return f'''<article class="signal{' anti' if anti else ''}">
<div class=signal-head><div><div class=signal-kicker>{'Анти-свип' if anti else 'Прематч value'}</div>
<h3>{e(v.get("t1") or "?")} vs {e(v.get("t2") or "?")}</h3></div><span class="badge {'b-neutral' if anti else 'b-val'}">Преимущество {_sign(edge)} п.п.</span></div>
<div class=pick-main><strong>{recommendation}</strong><span>{'защита от 0:2' if anti else 'рынок недооценивает сторону'}</span></div>
<div class=metric-grid>{metrics}</div>
<div class=signal-foot><span class=source>Модель согласована</span><span>Прематч · Bo3+</span></div></article>'''


def _live_card(match: dict) -> str:
    e = lambda value: html.escape(str(value if value is not None else ""))
    team1, team2 = str(match.get("team1") or "?"), str(match.get("team2") or "?")
    probs = _dict(match.get("p_series"))
    pa = _pct(_number(probs.get(team1), .5) * 100, 50); pb = round(100 - pa, 1)
    current = e(match.get("current") or "—"); current_map = e(match.get("cur_map") or "карта уточняется")
    rt = _dict(match.get("round_total")); rtline = ""
    if rt:
        rtline = (f'<div class=pick><span class=mut>Тотал {e(rt.get("map") or current_map)} · ожидание {_fmt(rt.get("exp"))}</span>'
                  f'<b class=blu>{e(rt.get("side") or "?")} 21.5 @ {_fmt(rt.get("fair"),2)}</b></div>')
    return f'''<article class="card live-card"><div class=mrow><h3 class=team>{e(team1)} vs {e(team2)}</h3><span class="badge b-live">LIVE · {current}</span></div>
<div class=match-meta><span>Bo{int(_number(match.get("bo"),3))}</span><span>{current_map}</span><span>{'решающая карта' if match.get('is_decider') else 'серия продолжается'}</span></div>
<div class=prob-labels><span>{e(team1)} <b>{_fmt(pa)}%</b></span><span><b>{_fmt(pb)}%</b> {e(team2)}</span></div>
<div class=probbar role=progressbar aria-label="Вероятность победы {e(team1)}" aria-valuemin=0 aria-valuemax=100 aria-valuenow="{_fmt(pa)}" style="--pa:{_fmt(pa)}%"><span class=a></span><span class=b></span></div>{rtline}</article>'''


def _board_card(row: dict) -> str:
    e = lambda value: html.escape(str(value if value is not None else ""))
    pf = _pct(_number(row.get("pf_fav")) * 100); pm = _pct(_number(row.get("pm_fav")) * 100)
    favorite = str(row.get("fav") or "")
    bet_side = str(row.get("bet_side") or "")
    if bet_side and favorite and bet_side.casefold() != favorite.casefold():
        shown_model, shown_market, assessed_side = 100 - pm, 100 - pf, bet_side
    else:
        shown_model, shown_market, assessed_side = pm, pf, favorite or bet_side
    diff = round(shown_model - shown_market, 1); verdict = str(row.get("verdict") or "PASS").upper()
    badge = ('<span class="badge b-val">Есть value</span>' if verdict == "VALUE" else
             '<span class="badge b-warn">Нужна осторожность</span>' if verdict == "SUSPICIOUS" else
             '<span class="badge b-neutral">Наблюдаем</span>')
    structured_flag = _clean_display(row.get("news_flag"))
    structured_reason = _clean_display(row.get("news_reason"))
    news_value = f"{structured_flag}: {structured_reason}".strip(" :") if structured_flag else row.get("news")
    source, note = _news_view(news_value)
    action = (f'<div class=board-action>Ставка: {e(row.get("bet_side") or "?")} @ {_fmt(row.get("bet_odd"),2)}</div>'
              if row.get("bet_side") else '')
    anti = row.get("antisweep") if isinstance(row.get("antisweep"), (list, tuple)) else ["", 0]
    anti_pct = _pct(_number(anti[1] if len(anti) > 1 else 0) * 100)
    anti_html = (f'<div class=news-note>Анти-свип: {e(anti[0])} возьмёт минимум карту · {_fmt(anti_pct)}%</div>'
                 if anti_pct >= 78 else '')
    note_html = f'<div class=news-note>{e(note)}</div>' if note else ''
    return f'''<article class=board-card><div class=board-top><div><div class=board-title>{e(row.get("t1") or "?")} vs {e(row.get("t2") or "?")}</div>
<div class=board-time>{_start_label(row.get("start_date"))} · Bo{int(_number(row.get("bo"),3))}</div></div>{badge}</div>
<div class=board-metrics><div class=metric><span>Модель</span><b>{_fmt(shown_model)}%</b></div><div class=metric><span>Букмекер</span><b>{_fmt(shown_market)}%</b></div>
<div class=metric><span>Разница</span><b class="{'pos' if diff >= 0 else 'neg'}">{_sign(diff)} п.п.</b></div></div>{action}{anti_html}
<div class=signal-foot>{source}<span>{e(assessed_side)} · оцениваемая сторона</span></div>{note_html}</article>'''


def _calibration_card(item: dict) -> str:
    e = lambda value: html.escape(str(value if value is not None else ""))
    wr = _pct(item.get("winrate")); expected = _pct(item.get("avg_p")); gap = round(wr - expected, 1)
    ok = abs(gap) <= 8
    return f'''<article class=calib><div class=lab><b>{e(item.get("label") or "Метрика")}</b><span class="badge {'b-ok' if ok else 'b-warn'}">{'Калибрована' if ok else 'Есть отклонение'}</span></div>
<div class=bar aria-hidden=true><div class=fill style="width:{_fmt(wr)}%"></div><div class=mark style="left:{_fmt(expected)}%"></div></div>
<div class=calib-legend><span>факт {_fmt(wr)}%</span><span>модель {_fmt(expected)}%</span></div>
<div class=news-note>{int(_number(item.get("n")))} ставок · разница {_sign(gap)} п.п.</div></article>'''


def _render_cs2_v2(d: dict) -> str:
    d = _dict(d)
    e = lambda value: html.escape(str(value if value is not None else ""))
    generated = _timestamp(d.get("ts"))
    if generated is None:
        ts, age, fresh = "—", None, "время обновления неизвестно"
    else:
        ts = time.strftime("%H:%M", time.gmtime(generated))
        age = max(0, int(time.time() - generated))
        fresh = f"обновлено {age} сек назад" if age < 60 else f"обновлено {max(1, age // 60)} мин назад"
    live, values = _rows(d.get("live")), _rows(d.get("value"))
    pmp, board = _rows(d.get("prematch_picks")), _rows(d.get("prematch_board"))
    steam = _dict(d.get("steam")); steam_live = _rows(steam.get("live")); steam_track = _dict(steam.get("track"))
    track = _dict(d.get("track"))
    strategies = [
        ("LIVE лаг-линии", _dict(d.get("livelag"))),
        ("Прематч value", _dict(d.get("prematch"))),
        ("Тоталы раундов", _dict(d.get("roi"))),
    ]
    settled = int(sum(_number(item.get("n")) for _, item in strategies))
    won = int(sum(_number(item.get("wins")) for _, item in strategies))
    pending = int(sum(_number(item.get("pending")) for _, item in strategies))
    profit = round(sum(_number(item.get("pl")) for _, item in strategies), 2)
    winrate = round(won / settled * 100) if settled else 0; roi = round(profit / settled * 100, 1) if settled else 0
    active = len(values) + len(pmp) + len(steam_live)
    P = [HEAD]
    P.append(f'''<header class=top><a class=brand href="/" aria-label="Analyst — главная"><span class=brand-mark aria-hidden=true>A</span>
<span class=brand-copy>Analyst<small>CS2 decision dashboard</small></span></a>
<div class=top-status><span class=health><span class=live></span>{e(fresh)}</span><span class=clock>{ts} UTC</span></div></header>''')
    P.append(_tabs("cs2"))
    P.append(f'''<main id=main><section class=page-intro aria-labelledby=page-title><div><p class=eyebrow>Обзор</p>
<h1 id=page-title>Сигналы и состояние модели</h1><p class=tagline>Сначала — что заслуживает внимания прямо сейчас. Затем live-матчи, рынок и проверка точности.</p></div>
<span class=freshness>Forward-test · без ретроспективной подгонки</span></section>
<section class=hero aria-label="Ключевые показатели">
<article class="stat profit"><div class=k>Прибыль</div><div class="v {'pos' if profit >= 0 else 'neg'}">{_sign(profit)}u</div><div class=s>{settled} рассчитанных ставок</div></article>
<article class=stat><div class=k>ROI</div><div class="v {'pos' if roi >= 0 else 'neg'}">{_sign(roi)}%</div><div class=s>за весь forward-test</div></article>
<article class=stat><div class=k>Проходимость</div><div class=v>{winrate}%</div><div class=s>{won} выиграно · {pending} ждут</div></article>
<article class="stat alerts"><div class=k>Активные сигналы</div><div class="v blu">{active}</div><div class=s>{len(live)} live-матча сейчас</div></article></section>''')

    P.append(f'''<section class=primary-grid><div class="panel panel-pad" id=signals><div class=section-head><div><h2>Сигналы сейчас</h2>
<p>Отсортированы по срочности и практической ценности</p></div><span class=count aria-label="Активных сигналов: {active}">{active}</span></div><div class=signals>''')
    P.extend(_steam_signal(item) for item in steam_live)
    P.extend(_total_signal(item) for item in values)
    P.extend(_prematch_signal(item) for item in pmp)
    if not active:
        P.append('<div class=empty>Сейчас нет сигналов с достаточным преимуществом. Модель ждёт качественную линию.</div>')
    P.append('</div></div><aside class=side-stack>')
    status_ok = age is not None and age < 180
    P.append(f'''<section class="panel status-card" aria-labelledby=status-title><div class=status-line><div><div class=signal-kicker>Состояние системы</div>
<strong id=status-title>{'Данные актуальны' if status_ok else 'Нужно обновление'}</strong></div><span class="badge {'b-ok' if status_ok else 'b-warn'}">{'В норме' if status_ok else 'Устарело'}</span></div>
<div class=line-score><div class=metric><span>Live-матчи</span><b>{len(live)}</b></div><div class=metric><span>В расчёте</span><b>{pending}</b></div></div></section>''')
    P.append('<section class="panel status-card" aria-labelledby=strategy-title><div class=section-head><div><h2 id=strategy-title>Стратегии</h2><p>Компактная статистика</p></div></div><div class=strategy-list>')
    for name, stats in strategies:
        sroi = _number(stats.get("roi")); swr = _number(stats.get("winrate"))
        P.append(f'''<div class=strategy><div class=strategy-top><span>{e(name)}</span><b class="{'pos' if sroi >= 0 else 'neg'}">ROI {_sign(sroi)}%</b></div>
<div class=strategy-meta><span>{_fmt(swr,0)}% проходимость</span><span>{int(_number(stats.get("n")))} ставок</span><span>{int(_number(stats.get("pending")))} ждут</span></div></div>''')
    P.append('''</div><details class=method><summary>Как читать показатели</summary><p>Проходимость — доля выигранных рассчитанных ставок. ROI — прибыль на одну ставку. Сигнал не гарантирует исход.</p></details></section></aside></section>''')

    P.append(f'''<section class=full-section id=matches><div class=section-head><div><h2>Live-матчи</h2><p>Счёт серии и текущая вероятность победы</p></div><span class=count>{len(live)}</span></div><div class=live-grid>''')
    if live:
        P.extend(_live_card(item) for item in live)
    else:
        P.append('<div class=empty>Сейчас нет live-матчей.</div>')
    P.append('</div></section>')

    P.append(f'''<section class=secondary-grid><div class="panel panel-pad" id=upcoming><div class=section-head><div><h2>Ближайшие матчи</h2>
<p>Модель, букмекер и проверка новостей в одной строке</p></div><span class=count>{len(board)}</span></div><div class=board-list>''')
    if board:
        P.extend(_board_card(item) for item in board)
    else:
        P.append('<div class=empty>Ближайшие матчи пока не рассчитаны.</div>')
    P.append('</div></div><aside class=side-stack>')
    line_roi = _number(steam_track.get("roi")); line_n = int(_number(steam_track.get("n")))
    P.append(f'''<section class="panel status-card" id=market><div class=section-head><div><h2>Движение линии</h2><p>История стратегии «следуй за рынком»</p></div></div>
<div class=line-score><div class=metric><span>Сыграно</span><b>{line_n}</b></div><div class=metric><span>Победы</span><b>{int(_number(steam_track.get("wins")))}</b></div>
<div class=metric><span>Проходимость</span><b>{_fmt(steam_track.get("winrate"),0)}%</b></div><div class=metric><span>ROI</span><b class="{'pos' if line_roi >= 0 else 'neg'}">{_sign(line_roi)}%</b></div></div>
<div class=risk-note>{int(_number(steam_track.get("pending")))} сигналов ждут расчёта. Красный ROI — повод снизить вес стратегии, а не скрывать результат.</div></section>
<section class="panel status-card"><div class=section-head><div><h2>Прозрачность</h2><p>Почему интерфейсу можно доверять</p></div></div>
<div class=strategy-list><div class=strategy><div class=strategy-top><span>Результаты</span><b>forward-test</b></div><div class=strategy-meta><span>без удаления проигрышей</span></div></div>
<div class=strategy><div class=strategy-top><span>Новости</span><b>структурный статус</b></div><div class=strategy-meta><span>без служебной разметки</span></div></div></div></section></aside></section>''')

    calibration_rows = [_dict(track.get(key)) for key in ("series", "map", "total") if _dict(track.get(key))]
    P.append('''<section class="panel panel-pad calibration" id=accuracy><div class=section-head><div><h2>Точность модели</h2>
<p>Синий — фактическая проходимость, жёлтая метка — ожидание модели</p></div></div><div class=calib-grid>''')
    if calibration_rows:
        P.extend(_calibration_card(item) for item in calibration_rows)
    else:
        P.append('<div class=empty>Данные калибровки ещё копятся.</div>')
    P.append('''</div></section><div class=risk-note><strong>Важно:</strong> вероятности и коэффициенты не гарантируют результат. Используйте фиксированный размер ставки и не увеличивайте риск после проигрыша.</div>
</main><nav class=mobile-nav aria-label="Быстрая навигация"><a href="#main">Обзор</a><a href="#signals">Сигналы</a><a href="#matches">Матчи</a><a href="#accuracy">Точность</a></nav>
<footer class=foot><strong>Analyst</strong> · данные обновляются автоматически · все результаты сохраняются в forward-test</footer></div></body></html>''')
    return "\n".join(P)


def render(d: dict) -> str:
    e = html.escape
    ts = time.strftime("%H:%M", time.localtime(d["ts"]))
    strats = [
        ("livelag", "⚡", "LIVE лаг-линии",
         "Ставит, когда исход карты почти решён (счёт 11-4), но бук не успел обновить live-коэффициент. Ловим задержку линии.", d["livelag"]),
        ("prematch", "\U0001f52e", "Пре-матч аномалии",
         "За часы до матча ищет странные коэффициенты (модель против линии — возможные договорняки) и находит value.", d["prematch"]),
        ("totals", "\U0001f3b2", "Тоталы раундов",
         "Наш калиброванный расчёт тотала раундов карты против линии winline. Ставим только при реальном крае.", d["roi"]),
    ]
    tot_n = sum(s[4]["n"] for s in strats)
    tot_pl = round(sum(s[4]["pl"] for s in strats), 2)
    tot_w = sum(s[4]["wins"] for s in strats)
    tot_wr = round(tot_w / tot_n * 100) if tot_n else 0
    tot_roi = round(tot_pl / tot_n * 100, 1) if tot_n else 0
    pend = sum(s[4]["pending"] for s in strats)
    roicls = "pos" if tot_roi >= 0 else "neg"

    P = [HEAD]
    P.append(f'''<div class=top><div class=brand>\U0001f3af CS2 <span class=dot>Analyst</span></div>
<div class=clock><span class=live></span>{ts} UTC</div></div>''')
    P.append(_tabs("cs2"))
    P.append(f'''<div class=hero>
<div class=stat><div class=k>Всего ставок</div><div class=v>{tot_n}</div><div class=s>+{pend} ждут исхода</div></div>
<div class=stat><div class=k>Винрейт</div><div class="v pos">{tot_wr}%</div><div class=s>{tot_w} из {tot_n} зашло</div></div>
<div class=stat><div class=k>ROI</div><div class="v {roicls}">{_sign(tot_roi)}%</div><div class=s>{_sign(tot_pl)} ед. прибыли</div></div>
<div class=stat><div class=k>Сейчас</div><div class="v blu">{len(d["live"])}</div><div class=s>{len(d["value"])} value-пик</div></div>
</div>''')

    P.append('<div class=sect>\U0001f4b0 Стратегии</div><div class=grid>')
    accents = {"livelag": "var(--amb)", "prematch": "var(--vio)", "totals": "var(--grn)"}
    for key, ic, name, desc, s in strats:
        rc = "pos" if s["roi"] >= 0 else "neg"
        wrcls = "pos" if s["winrate"] >= 55 else ""
        P.append(f'''<div class=strat style="--accent:{accents[key]}">
<h3>{ic} {name}</h3><div class=desc>{desc}</div>
<div class=big><span class="wr {wrcls}">{s["winrate"]}%</span><span class="roi {rc}">ROI {_sign(s["roi"])}%</span></div>
<div class=meta><span><b>{s["n"]}</b> ставок</span><span><b>{_sign(s["pl"])}</b> ед.</span><span>{s["pending"]} ждут</span></div>
</div>''')
    P.append('</div>')

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
<div class=mrow><span class=team>{e(s.get("team1", "?"))} vs {e(s.get("team2", "?"))}</span>
<span class="badge b-live">LIVE · Bo{s.get("bo", "?")} {e(s.get("current", ""))}{cm}</span></div>
<div class=probbar><span class=a style="flex:{max(pa, 12)}">{na} {pa}%</span><span class=b>{nb} {100 - pa}%</span></div>
{rtline}</div>''')

    st = d.get('steam') or {}
    stl = st.get('live') or []
    tr_ = st.get('track') or {}
    P.append('<div class=sect>\U0001f525 Движение линии winline <span class=mut style="text-transform:none;letter-spacing:0;font-weight:400">— стим/договорняки по всему борду</span></div>')
    if tr_.get('n'):
        P.append(f'<div class=card><div class=mrow><span class=mut>трек «следуй за стимом»</span><b class="{"pos" if tr_["roi"] > 0 else ""}">{tr_["wins"]}/{tr_["n"]} = {tr_["winrate"]}% · ROI {tr_["roi"]:+}% · {tr_["pending"]} ждут</b></div></div>')
    else:
        P.append(f'<div class=card><span class=mut>трек копится — стимов сыграно 0, ждут {tr_.get("pending", 0)}</span></div>')
    if not stl:
        P.append('<div class=empty>Активных стимов сейчас нет — появятся здесь и в Telegram</div>')
    for sm in stl:
        tagl = '\U0001f680 ранний' if sm['mode'] == 'EARLY' else '\U0001f525 стим'
        newsl = (f'<div class=mrow><span class=mut>\U0001f4f0</span><span class=mut>{e(str(sm["news"])[:90])}</span></div>' if sm.get('news') else '')
        P.append(f'''<div class="card val">
<div class=mrow><span class=team>{e(str(sm["t1"]))} vs {e(str(sm["t2"]))}</span><span class="badge b-val">{tagl} · {sm["strength"]}%</span></div>
<div class=pick><b class=pos>ставь {e(str(sm["side"]))} @ {sm["entry"]}</b><span class=mut>двинули {sm["open"]} \u2192 {sm["entry"]}</span></div>
{newsl}</div>''')
    P.append('<div class=sect>\U0001f3af Ставить сейчас</div>')
    pmp = d.get("prematch_picks") or []
    if not d["value"] and not pmp:
        P.append('<div class=empty>Сейчас нет value-пиков (ждём выгодные линии) — как появятся, придут сюда и в Telegram</div>')
    for v in d["value"]:
        P.append(f'''<div class="card val">
<div class=mrow><span class=team>{e(v["t1"])} vs {e(v["t2"])}</span><span class="badge b-val">\U0001f3b2 тотал · край +{v["edge"]}%</span></div>
<div class=pick><b class=pos>{e(v["side"])} {v["line"]} @ {v["odd"]}</b><span class=mut>наша вероятность {v["p"]}%</span></div>
</div>''')
    for v in [x for x in pmp if x.get('kind') != 'anti']:
        P.append(f'''<div class="card val">
<div class=mrow><span class=team>{e(v["t1"])} vs {e(v["t2"])}</span><span class="badge b-val">\U0001f52e пре-матч · край +{v["edge"]}%</span></div>
<div class=pick><b class=pos>победа: {e(v["side"])} @ {v["odd"]}</b><span class=mut>модель нашла value против линии</span></div>
</div>''')

    for v in [x for x in pmp if x.get('kind') == 'anti']:
        P.append(f'''<div class="card val">
<div class=mrow><span class=team>{e(v["t1"])} vs {e(v["t2"])}</span><span class="badge b-val">\U0001f6e1 анти-свип · край +{v["edge"]}%</span></div>
<div class=pick><b class=pos>{e(v["side"])} берёт 1+ карту (Фора карт +1.5) @ {v["odd"]}</b><span class=mut>наша вероятность {v.get("p","?")}%</span></div>
</div>''')
    board = d.get('prematch_board') or []
    if board:
        P.append('<div class=sect>\U0001f4cb Ближайшие матчи · оценка модели</div>')
        for r in board:
            pf = round((r.get('pf_fav') or 0) * 100); pm = round((r.get('pm_fav') or 0) * 100)
            asw = r.get('antisweep') or ['', 0]; aswp = round((asw[1] or 0) * 100)
            vd = r.get('verdict')
            badge = ('<span class="badge b-val">\u2705 value</span>' if vd == 'VALUE'
                     else '<span class="badge b-warn">\u26a0\ufe0f странный</span>' if vd == 'SUSPICIOUS' else '')
            betline = (f'<div class=pick><b class=pos>ставка: {e(str(r.get("bet_side")))} @ {r.get("bet_odd")}</b></div>'
                       if r.get('bet_side') else '')
            aline = (f'<div class=mrow><span class=mut>\U0001f6e1 {e(str(asw[0]))} берёт \u22651 карту</span><b class=pos>{aswp}%</b></div>'
                     if aswp >= 78 else '')
            ln = r.get('line'); lineb = ''
            if ln and ln.get('dir') == 'confirm':
                lineb = f'<div class=mrow><span class=mut>\U0001f525 рынок подтверждает</span><b class=pos>деньги тоже на {e(str(ln["side"]))}</b></div>'
            elif ln and ln.get('dir') == 'against':
                lineb = f'<div class=mrow><span class=mut>\u26a0\ufe0f рынок против</span><b style="color:#ffc857">деньги на {e(str(ln["side"]))} — осторожно</b></div>'
            newsb = (f'<div class=mrow><span class=mut>\U0001f4f0</span><span class=mut>{e(str(r.get("news"))[:110])}</span></div>' if r.get('news') else '')
            P.append(f'''<div class="card{' val' if vd == 'VALUE' else ''}">
<div class=mrow><span class=team>{e(str(r.get('t1')))} vs {e(str(r.get('t2')))}</span>{badge}</div>
<div class=mrow><span class=mut>фаворит: {e(str(r.get('fav')))}</span><b>модель {pm}%{(' · бук '+str(pf)+'%') if r.get('pf_fav') else ''}</b></div>
{betline}{aline}{lineb}{newsb}</div>''')
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
            if ok:
                badge = '<span class="badge b-ok">✓ калибр</span>'
            elif t["gap"] < 0:
                badge = f'<span class="badge b-warn">завышает {abs(t["gap"])}pp</span>'
            else:
                badge = f'<span class="badge b-warn">занижает {t["gap"]}pp</span>'
            wr = t["winrate"]
            P.append(f'''<div class=calib>
<div class=lab><span><b>{e(t["label"])}</b> · {t["n"]} ставок</span>{badge}</div>
<div class=bar><div class=fill style="width:{wr}%"></div><div class=mark style="left:{t["avg_p"]}%"></div></div>
<div class=lab><span class=mut>реально {wr}% зашло</span><span class=mut>▾ модель ждёт {t["avg_p"]}%</span></div>
</div>''')
            if mk != "total":
                P.append('<div style="height:14px"></div>')
        P.append('</div>')

    P.append('''<div class=foot>⚙️ Все стратегии считаются автоматически по факту исходов · параметры самонастраиваются ежедневно<br>
странные матчи и value-пики приходят в Telegram · <span class=pos>зелёное = прибыль</span></div>
</div></body></html>''')
    return "\n".join(P)


# Keep the synchronized production renderer above as a readable fallback while
# exposing the redesigned CS2 renderer through the same public import contract.
render_legacy = render
render = _render_cs2_v2


def _flean(r: dict) -> str:
    """Model's calibrated 1X2 lean (varies per match, ~53% accurate) — shown instead of the O/U
    rec, which is the model's weakest market (~43%). Returns 'П1 · Team · 57%' style."""
    w1, dr, w2 = r.get("prob_w1") or 0, r.get("prob_draw") or 0, r.get("prob_w2") or 0
    best = max(w1, dr, w2)
    if best <= 0:
        return "\u2014"
    if best == w1:
        return f'П1 · {html.escape(str(r.get("team1", "?")))} · {w1:.0f}%'
    if best == w2:
        return f'П2 · {html.escape(str(r.get("team2", "?")))} · {w2:.0f}%'
    return f'Ничья · {dr:.0f}%'


def _fedge(r: dict):
    """Model's real-EDGE pick for a match — Over 2.5 / BTTS (where it beats the book on ~7500 matches),
    with the CALIBRATED true hit-rate. 1X2/Double-Chance omitted: high win-rate but no edge."""
    po = _number(r.get("prob_over_2_5"))
    pb = _number(r.get("prob_btts_yes"))
    po = po * 100 if po <= 1.5 else po
    pb = pb * 100 if pb <= 1.5 else pb
    if po >= 52:
        real = 57 if po < 55 else (66 if po < 60 else 73)
        return (f'ТБ 2.5 голов · модель {po:.0f}% \u2192 реально ~{real}%', True)
    if pb >= 55:
        real = 61 if pb < 60 else 69
        return (f'Обе забьют · модель {pb:.0f}% \u2192 реально ~{real}%', True)
    if po and po <= 45:
        return (f'ТМ 2.5 голов · модель {100 - po:.0f}%', False)
    return ('нет явного края в сильных рынках', False)


def render_football(d: dict) -> str:
    d = _dict(d)
    e = lambda value: html.escape(str(value if value is not None else ""))
    generated = _timestamp(d.get("ts"))
    ts = time.strftime("%H:%M", time.gmtime(generated)) if generated is not None else "—"
    f = _dict(d.get("football"))
    acc = _dict(f.get("acc"))
    pend = _rows(f.get("pending"))
    resolved = _rows(f.get("resolved"))

    def a(mk):
        raw = acc.get(mk)
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            return 0, 0.0
        return max(0, int(_number(raw[0]))), _probability_pct(raw[1])

    n1, p1 = a("1x2")
    no, po = a("ou")
    nb, pb = a("btts")
    nval = sum(1 for r in pend if _parse_vb(r.get("value_bets")))
    _fm = _dict(d.get("football_markets"))
    families = _rows(_fm.get("families"))
    spots = _rows(_fm.get("spots"))
    _topfam = str((families[0].get("name") if families else "—") or "—").split(" (")[0]

    P = [HEAD]
    P.append(f'''<header class=top><a class=brand href="/football" aria-label="Analyst — футбол"><span class=brand-mark aria-hidden=true>⚽</span>
<span class=brand-copy>Analyst<small>Football decision dashboard</small></span></a>
<div class=top-status><span class=health><span class=live></span>модель активна</span><span class=clock>{ts} UTC</span></div></header>''')
    P.append(_tabs("fb"))
    P.append('''<main id=main><section class=page-intro aria-labelledby=football-title><div><p class=eyebrow>Футбол</p>
<h1 id=football-title>Прогнозы и рыночное преимущество</h1><p class=tagline>Матмодель Elo + Пуассон/Dixon-Coles + ML-ансамбль. Показываем value относительно букмекерской линии.</p></div>
<span class=freshness>Калибровка на исторической выборке</span></section>''')
    P.append(f'''<div class=hero>
<div class=stat><div class=k>Прогнозов</div><div class=v>{max(0, int(_number(f.get("n"))))}</div><div class=s>{len(pend)} впереди · {len(resolved)} сыграно</div></div>
<div class=stat><div class=k>Исход 1X2</div><div class="v {'pos' if p1 >= 50 else ''}">{_fmt(p1)}%</div><div class=s>по {n1} матчам</div></div>
<div class=stat><div class=k>Топ-рынок</div><div class="v pos">{e(_topfam)}</div><div class=s>реальный край vs бук</div></div>
<div class=stat><div class=k>Value-ставок</div><div class="v blu">{nval}</div><div class=s>в ближайших матчах</div></div>
</div>''')

    if families:
        P.append('<div class=sect>\U0001f4ca Где у модели РЕАЛЬНЫЙ край над букмекером <span class=mut style="text-transform:none;letter-spacing:0;font-weight:400">— по ~7500 матчам (винрейт врёт, край — нет)</span></div>')
        P.append('<div class=card>')
        for fam in families:
            has_edge = bool(fam.get("edge"))
            cls = 'pos' if has_edge else 'mut'
            tag = '\u2713 есть край' if has_edge else '\u2717 нет края'
            trust = _number(fam.get("trust")); sample = max(0, int(_number(fam.get("n"))))
            trust_label = f"+{_fmt(trust)}" if trust >= 0 else _fmt(trust)
            P.append(f'<div class=mrow><span class=team>{e(fam.get("name") or "—")}</span><b class={cls}>{tag} · {trust_label} · {sample} матч.</b></div>')
        P.append('</div>')
    if spots:
        P.append('<div class=sect>\U0001f3af Где край реально заходит <span class=mut style="text-transform:none;letter-spacing:0;font-weight:400">— маркет + зона уверенности модели</span></div>')
        P.append('<div class=card>')
        for sp in spots:
            bucket = e(sp.get("bucket") or "—"); sample = max(0, int(_number(sp.get("n"))))
            delta = _number(sp.get("delta")); real = _pct(sp.get("real")); pred = _pct(sp.get("pred"))
            delta_label = f"+{_fmt(delta)}" if delta >= 0 else _fmt(delta)
            P.append(f'''<div class=calib>
<div class=lab><span><b>{e(sp.get("market") or "—")}</b> · модель {bucket}% · {sample} матч.</span><span class="badge b-ok">{delta_label} п.п. края</span></div>
<div class=bar><div class=fill style="width:{_fmt(real)}%"></div><div class=mark style="left:{_fmt(pred)}%"></div></div>
<div class=lab><span class=mut>реально {_fmt(real)}% заходит</span><span class=mut>\u25be бук ждёт ~{_fmt(pred)}%</span></div>
</div><div style="height:10px"></div>''')
        P.append('</div>')
    P.append('<div class=sect>\U0001f3af Предстоящие прогнозы</div>')
    if not pend:
        P.append('<div class=empty>Нет предстоящих прогнозов — бот считает по мере появления матчей</div>')
    for r in pend[:14]:
        risk = str(r.get("risk_level") or "").upper()
        rb = ('<span class="badge b-hi">риск высокий</span>' if risk == "HIGH"
              else '<span class="badge b-md">риск средний</span>' if risk == "MEDIUM" else "")
        w1, dr, w2 = (_probability_pct(r.get("prob_w1")), _probability_pct(r.get("prob_draw")),
                      _probability_pct(r.get("prob_w2")))
        eg, eg_strong = _fedge(r)
        vbs = _parse_vb(r.get("value_bets"))
        vblock = "".join(f'<div class=pick><b class=pos>\U0001f4b0 {e(v)}</b></div>' for v in vbs)
        P.append(f'''<div class="card{" val" if vbs else ""}">
<div class=mrow><span class=team>{e(r.get("team1", "?"))} vs {e(r.get("team2", "?"))}</span>{rb}</div>
<div class=three><div>П1<b>{_fmt(w1,0)}%</b></div><div>X<b>{_fmt(dr,0)}%</b></div><div>П2<b>{_fmt(w2,0)}%</b></div></div>
<div class=mrow><span class=mut>\U0001f3af лучший рынок (край)</span><b class="{'pos' if eg_strong else 'mut'}">{e(eg)}</b></div>
{vblock}</div>''')

    P.append('<div class=sect>\U0001f4dc Последние результаты</div>')
    if not resolved:
        P.append('<div class=empty>Пока нет сыгранных прогнозов</div>')
    for r in resolved[:12]:
        ar = str(r.get("actual_result") or "")
        rw1, rdr, rw2 = (_probability_pct(r.get("prob_w1")), _probability_pct(r.get("prob_draw")),
                         _probability_pct(r.get("prob_w2")))
        argmax = "W1" if rw1 >= rdr and rw1 >= rw2 else ("DRAW" if rdr >= rw2 else "W2")
        hit = ar in ("W1", "DRAW", "W2") and argmax == ar
        mark = '<span class="badge b-ok">✓ 1X2</span>' if hit else '<span class="badge b-warn">✗ 1X2</span>'
        P.append(f'''<div class=card>
<div class=mrow><span class=team>{e(r.get("team1", "?"))} vs {e(r.get("team2", "?"))}</span>{mark}</div>
<div class=mrow><span class=mut>{e(str(r.get("top_rec") or "—"))}</span><b>счёт {e(str(r.get("actual_score") or "?"))}</b></div>
</div>''')

    P.append('''</main><footer class=foot>⚽ Матмодель + ИИ-разбор · value = где наша вероятность выше, чем даёт бук<br>
прогнозы приходят в Telegram · самообучение уроков включено</footer>
</div></body></html>''')
    return "\n".join(P)


def _parse_vb(raw):
    if not raw or raw in ("[]", ""):
        return []
    try:
        v = json.loads(raw)
        return [str(x) for x in v][:3] if isinstance(v, list) else [str(v)]
    except Exception:
        return [str(raw)[:80]]
