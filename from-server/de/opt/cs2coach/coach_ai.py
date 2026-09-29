#!/usr/bin/env python3
"""AI coaching layer for CS2 Coach.

The parser produces numbers; a player cannot act on numbers. This turns them into the two or three
things worth fixing, in priority order, with a concrete adjustment for each — which is what a coach
actually delivers.

Hard rule in the prompt: the model may only reason about the numbers it is given. Invented stats
would destroy trust in a paid product faster than saying nothing.

Usage:
  coach_ai.py <demo.dem> --player "<name or steamid>" [--ru] [--json]
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/opt/cs2-analyst")
sys.path.insert(0, "/opt/cs2coach")

import analyst  # noqa: E402  OmniRoute gateway (cookie-based, zero API cost)
from demo_parser import parse  # noqa: E402

MODELS = ["claude-code", "gemini-cli", "grok", "gpt-cursor"]   # fallback chain; workers can be flaky

SYSTEM_EN = """You are a CS2 coach for a Faceit Level 7-10 player. You are given REAL parsed stats
from one of their matches, plus the lobby averages for context.

Rules:
- Use ONLY the numbers provided. Never invent a stat, a position, or a round number.
- Give the 2-3 highest-impact problems, most costly first. Ignore everything else.
- For each: state the problem in one line, why it loses rounds, and ONE concrete adjustment or drill.
- Speak like a coach to a player: direct, specific, no filler, no motivational fluff.
- If a number is actually good, say so briefly — do not manufacture problems.
- Max 220 words total."""

SYSTEM_RU = """Ты тренер по CS2, разбираешь матч игрока уровня Faceit 7-10. Тебе даны РЕАЛЬНЫЕ
метрики из его матча и средние по лобби для сравнения.

Правила:
- Опирайся ТОЛЬКО на приведённые цифры. Не выдумывай статистику, позиции и номера раундов.
- Дай 2-3 самые дорогие проблемы, начиная с главной. Остальное не упоминай.
- По каждой: в чём проблема (одна строка), почему из-за неё теряются раунды, и ОДНО конкретное
  действие или упражнение.
- Говори как тренер игроку: прямо, конкретно, без воды и мотивационных лозунгов.
- Если показатель хороший — отметь коротко, не выдумывай проблему на пустом месте.
- Максимум 220 слов."""


def pick_player(data: dict, who: str) -> dict | None:
    who = (who or "").strip().lower()
    if not who:
        return data["players"][0] if data["players"] else None
    for p in data["players"]:
        if p["steamid"] == who or who in p["name"].lower():
            return p
    return None


def build_brief(data: dict, p: dict) -> str:
    b = data["match_baselines"]
    ow = f"{p['opening_winrate']}%" if p["opening_winrate"] is not None else "n/a"
    weapons = ", ".join(f"{w} x{c}" for w, c in p["top_weapons"]) or "n/a"
    died_to = ", ".join(f"{w} x{c}" for w, c in p["died_to"]) or "n/a"
    return f"""MATCH: {data['map']}, {data['rounds']} rounds.
PLAYER: {p['name']} (team {p['team']})

His numbers:
- K/D/A: {p['kills']}/{p['deaths']}/{p['assists']}  (K/D {p['kd']})
- ADR: {p['adr']}   |  lobby average ADR: {b['avg_adr']}
- Headshot %: {p['hs_pct']}
- Opening duels: {p['opening_kills']} won / {p['opening_deaths']} lost  (win rate {ow})
- Deaths that were NOT traded by a teammate: {p['untraded_deaths']} of {p['deaths']} ({p['untraded_pct']}%)
  lobby average untraded: {b['avg_untraded']}%
- Trade kills he made for teammates: {p['trade_kills']}
- Flash assists: {p['flash_assists']}  |  flash assists in the whole match: {b['flash_total']}
- Wallbang kills: {p['wallbang_kills']}
- His most used weapons: {weapons}
- He was killed most by: {died_to}

Note on context: a low match-wide trade rate means this was an uncoordinated lobby; judge him
against the lobby averages above, not against pro benchmarks."""


def coach(demo_path: str, who: str, ru: bool = False) -> dict:
    data = parse(demo_path)
    p = pick_player(data, who)
    if not p:
        names = ", ".join(x["name"] for x in data["players"])
        raise SystemExit(f"игрок '{who}' не найден. В матче: {names}")

    brief = build_brief(data, p)
    system = SYSTEM_RU if ru else SYSTEM_EN

    report, used = None, None
    for m in MODELS:
        try:
            r = analyst.call_omni(m, brief, system=system, max_tokens=700)
            if r and len(r.strip()) > 80:
                report, used = r.strip(), m
                break
        except Exception as e:  # noqa: BLE001
            print(f"  [{m} недоступен: {str(e).splitlines()[0][:60]}]", file=sys.stderr)
    if not report:
        raise SystemExit("ни одна модель не ответила — проверь OmniRoute")

    return {"map": data["map"], "rounds": data["rounds"], "player": p["name"],
            "stats": p, "baselines": data["match_baselines"], "model": used, "report": report}


def main():
    argv = sys.argv[1:]
    if not argv:
        print(__doc__)
        return 1
    demo = argv[0]
    who = ""
    if "--player" in argv:
        i = argv.index("--player")
        who = argv[i + 1] if i + 1 < len(argv) else ""

    res = coach(demo, who, ru="--ru" in argv)

    if "--json" in argv:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    s = res["stats"]
    print(f"\n{'='*66}")
    print(f"РАЗБОР МАТЧА · {res['player']} · {res['map']} · {res['rounds']} раундов")
    print(f"{'='*66}")
    print(f"K/D {s['kd']}   ADR {s['adr']}   HS {s['hs_pct']}%   "
          f"входные {s['opening_kills']}-{s['opening_deaths']}   неразменянных смертей {s['untraded_pct']}%")
    print(f"{'-'*66}\n")
    print(res["report"])
    print(f"\n{'-'*66}\nмодель: {res['model']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
