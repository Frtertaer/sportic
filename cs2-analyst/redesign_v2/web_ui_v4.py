"""Local Atlantic Broadcast color exploration built on the approved v3 structure."""

from __future__ import annotations

import web_ui_v3 as v3


V4_OVERRIDES = r'''
/* v4 — Atlantic Broadcast. Flat, chromatic surfaces; semantic accents only. */
:root{
  --bg:#0b1724;--head:#10263b;--surface:#152b40;--row:#1b354c;--row-hover:#21405a;
  --line:#31566f;--line-soft:#27465b;--text:#edf3f5;--muted:#afc1cc;--faint:#8da4b3;
  --blue:#69a9cf;--blue-soft:#203e56;--brand:#e8864a;--actual-color:#69a9cf;--model:#63cfdb;--market:#b5aacb;
  --green:#67c18f;--red:#f07a76;--amber:#dfb65e
}
body{background:var(--bg);color:var(--text)}
.site-head{background:var(--head);border-bottom-color:#39627d}
.wordmark-mark{background:var(--brand)}
.main-nav a:hover,.main-nav a.active{color:#fff;border-bottom-color:var(--brand)}
.main-nav a:hover{background:#173149}
.fresh-dot{background:var(--green)}.fresh-dot.stale{background:var(--red)}
.page-title h1{color:#f7fbfc}.page-title p{color:#a9c0ce}.utc{color:#8eb2c8}
.summary{background:#142a40;border-color:var(--line)}
.summary-item{border-left-color:var(--line);border-top:3px solid transparent}
.summary-item:nth-child(1){background:#17334b;border-top-color:var(--blue)}
.summary-item:nth-child(2){background:#163748;border-top-color:var(--model)}
.summary-item:nth-child(3){background:#173b3b;border-top-color:var(--green)}
.summary-item:nth-child(4){background:#382f38;border-top-color:var(--brand)}
.summary-item:nth-child(1) b{color:#a9d7f1}.summary-item:nth-child(2) b{color:var(--model)}
.summary-item:nth-child(4) b{color:#f2b27f}.summary-item span{color:#b6c7d1}
.section-bar{background:#20415a;border-color:var(--line);border-left:4px solid var(--blue)}
#signals .section-bar{border-left-color:var(--brand)}
#matches .section-bar{border-left-color:var(--red)}
#upcoming .section-bar{border-left-color:var(--blue)}
.section-bar h2{color:#f0f7f9}.section-bar span{color:#9dbaca}
.column-head{background:#112a3e;border-color:var(--line);color:#91adbd}
.signal-cols span:nth-child(4),.board-cols span:nth-child(4){color:var(--model)}
.signal-cols span:nth-child(5),.board-cols span:nth-child(5){color:var(--market)}
.signal-cols span:nth-child(6),.board-cols span:nth-child(6){color:#8edcaf}
.signal-list,.match-list,.board-list{background:var(--surface);border-color:var(--line)}
.signal-row,.match-row,.board-row{background:var(--row);border-bottom-color:var(--line-soft)}
.signal-row:nth-child(even),.match-row:nth-child(even),.board-row:nth-child(even){background:#183148}
.signal-row.tone-live{background:#303744;--tone:var(--brand)}
.signal-row.tone-value{background:#193b42;--tone:var(--green)}
.signal-row.tone-watch{background:#21394f;--tone:#789fbe}
.type.live{color:#f0a06b}.type.value{color:#81d8a7}.type.watch{color:#94bad5}
.c-model strong,.board-model strong{color:var(--model)}
.c-market strong,.board-market strong{color:var(--market)}
.edge-pos{color:#81d8a7}.edge-neg{color:#ff918e}.unknown{color:#91aaba}
.recommendation small,.sub{color:#94adbb}
.match-row{border-left:3px solid var(--red);background:#1c344b}
.live-state{color:#ff918e}.match-meta{color:#a9c0ce}
.prob-line{background:#2b4a60}.prob-line span{background:var(--model)}
.date-band{background:#28516d;color:#ffd0a7;border-bottom-color:#3c6883}
.board-row{background:#1a344b}.board-row:nth-child(even){background:#1d3951}
.news.ok{color:#83d7a8}.news.risk{color:#ff918e}.news.unknown{color:#efc66d}
.rail-box{background:#152e44;border-color:var(--line)}
.rail-title{background:#20425b;border-bottom-color:#3b6077;color:#dcebf0}
.rail-box:nth-child(1) .rail-title{border-left:4px solid var(--brand)}
.rail-box:nth-child(2) .rail-title{border-left:4px solid var(--blue)}
.rail-box:nth-child(3) .rail-title{border-left:4px solid var(--model)}
.rail-box:nth-child(4) .rail-title{border-left:4px solid var(--faint)}
.strategy,.cal-row{border-bottom-color:var(--line-soft)}
.strategy-meta,.cal-bottom{color:#91a9b7}
.cal-track{background:#2b4b61}.cal-track span{background:var(--actual-color)}.cal-track i{background:var(--model)}
.method p{color:#aac0cb}.footnote{border-top-color:var(--line);color:#89a2b1}
.mobile-prefix{color:#99b0bd}
@media(max-width:720px){
  .main-nav{background:#0f2235;border-top-color:#31566f}
  .summary-item{border-left-color:#31566f;border-top-width:2px}
  .match-row .prob-cell{min-width:0}
  .match-row .prob{gap:2px}
  .match-row .prob span{display:none}
  .match-row .prob b{font-size:11px}
}
'''


CS2_HEAD_V4 = (
    v3.CS2_HEAD_V3
    .replace('content="#0d1014"', 'content="#0b1724"', 1)
    .replace("</style>", V4_OVERRIDES + "\n</style>", 1)
)


FOOTBALL_V4_OVERRIDES = r'''
:root{
  --bg:#0b1724;--bg2:#10263b;--surf:#152b40;--raised:#1b354c;--soft:#203e56;
  --line:#31566f;--line2:#3b6782;--txt:#edf3f5;--mut:#afc1cc;--faint:#8da4b3;
  --blue:#69a9cf;--blue2:#63cfdb;--green:#67c18f;--red:#f07a76;--amber:#dfb65e;--violet:#b5aacb;
  --shadow:none;--r:6px;--r-sm:4px
}
body{background:var(--bg)}
.wrap{width:min(1180px,calc(100% - 32px))}
.brand-mark{background:var(--brand,#e8864a);box-shadow:none;border-radius:4px}
.tabs{background:#10263b;backdrop-filter:none;border-bottom-color:var(--line)}
.tab{border-radius:4px}.tab:hover{background:#173149}.tab.active{background:#1b354c;box-shadow:inset 0 0 0 1px var(--line2)}
.active .sport-dot{background:#e8864a;box-shadow:none}
.stat{background:#17334b;border-color:var(--line);border-radius:6px}.stat::after{display:none}
.stat:nth-child(2){background:#163748}.stat:nth-child(3){background:#173b3b}.stat:nth-child(4){background:#382f38}
.card{background:#1b354c;border-color:var(--line);border-radius:5px;box-shadow:none}
.card.val{background:#193b42;border-color:#3d735f}
.sect{color:#edf3f5}.mut{color:#afc1cc}
.calib{background:#183148;border-color:var(--line);border-radius:4px}
.bar{background:#2b4b61}.bar .fill{background:#69a9cf}.bar .mark{background:#63cfdb;box-shadow:none}
.three>div{background:#203e56;border-radius:4px}
.empty{background:#152b40;border-color:var(--line2);border-radius:4px}
.badge{border-radius:3px}.b-ok,.b-val{color:#81d8a7;background:#193b42}.b-live,.b-hi{color:#ff918e;background:#3b2b36}
.b-warn,.b-md{color:#efc66d;background:#3c3829}.b-neutral{color:#a9d7f1;background:#203e56}
.foot{border-top-color:var(--line)}
'''


FOOTBALL_HEAD_V4 = (
    v3.base.HEAD
    .replace('content="#080c12"', 'content="#0b1724"', 1)
    .replace("</style>", FOOTBALL_V4_OVERRIDES + "\n</style>", 1)
)


def render(data: dict) -> str:
    return v3.render(data).replace(v3.CS2_HEAD_V3, CS2_HEAD_V4, 1)


def render_football(data: dict) -> str:
    return v3.render_football(data).replace(v3.base.HEAD, FOOTBALL_HEAD_V4, 1)


render_v3 = v3.render
