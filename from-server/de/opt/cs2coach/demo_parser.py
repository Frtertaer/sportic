#!/usr/bin/env python3
"""CS2 demo parser for the Coach product.

Deliberately NOT a stats dump: a Faceit player already sees K/D on the scoreboard. What they cannot
see — and what coaching is actually made of — is opening-duel record, whether their deaths get traded,
and which buy types they bleed rounds on. Those are the numbers this extracts.

Usage:  python demo_parser.py <demo.dem> [--steamid 7656...] [--json]
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

from demoparser2 import DemoParser

TRADE_WINDOW_TICKS = 64 * 5          # 5 s at 64 tick — the window a death can still be traded in


def _num(x, default=0.0):
    try:
        v = float(x)
        return default if v != v else v      # NaN guard
    except Exception:
        return default


def parse(path: str) -> dict:
    p = DemoParser(path)
    header = p.parse_header()
    deaths = p.parse_event("player_death", player=["team_name"], other=["total_rounds_played"])
    hurts = p.parse_event("player_hurt", other=["total_rounds_played"])
    rounds = p.parse_event("round_end", other=["total_rounds_played"])
    total_rounds = int(len(rounds)) or 1

    # ---- per-player accumulators ----
    P: dict[str, dict] = {}

    def slot(sid, name=None, team=None):
        sid = str(sid)
        if sid not in P:
            P[sid] = {"steamid": sid, "name": name or "?", "team": team or "?",
                      "kills": 0, "deaths": 0, "assists": 0, "hs": 0, "dmg": 0.0,
                      "opening_kills": 0, "opening_deaths": 0,
                      "traded_deaths": 0, "trade_kills": 0,
                      "flash_assists": 0, "wallbang": 0, "noscope": 0,
                      "rounds_survived": 0, "weapons": defaultdict(int),
                      "killed_by": defaultdict(int)}
        if name and P[sid]["name"] == "?":
            P[sid]["name"] = name
        if team and P[sid]["team"] == "?":
            P[sid]["team"] = team
        return P[sid]

    # ---- kills / deaths ----
    kill_rows = []
    for r in deaths.to_dict("records"):
        rnd = int(_num(r.get("total_rounds_played")))
        tick = int(_num(r.get("tick")))
        vic, att = r.get("user_steamid"), r.get("attacker_steamid")
        kill_rows.append({"rnd": rnd, "tick": tick, "vic": str(vic), "att": str(att) if att else None,
                          "vic_team": r.get("user_team_name"), "att_team": r.get("attacker_team_name")})

        v = slot(vic, r.get("user_name"), r.get("user_team_name"))
        v["deaths"] += 1
        if r.get("weapon"):
            v["killed_by"][str(r.get("weapon"))] += 1

        if att and str(att) != str(vic) and str(att) != "None":
            a = slot(att, r.get("attacker_name"), r.get("attacker_team_name"))
            a["kills"] += 1
            if bool(r.get("headshot")):
                a["hs"] += 1
            if r.get("weapon"):
                a["weapons"][str(r.get("weapon"))] += 1
            if bool(r.get("penetrated")):
                a["wallbang"] += 1
            if bool(r.get("noscope")):
                a["noscope"] += 1
        if r.get("assister_steamid") and str(r.get("assister_steamid")) != "None":
            s = slot(r.get("assister_steamid"), r.get("assister_name"), r.get("assister_team_name"))
            s["assists"] += 1
            if bool(r.get("assistedflash")):
                s["flash_assists"] += 1

    # ---- damage (ADR) ----
    for r in hurts.to_dict("records"):
        att = r.get("attacker_steamid")
        if att and str(att) != "None" and str(att) != str(r.get("user_steamid")):
            slot(att, r.get("attacker_name"))["dmg"] += _num(r.get("dmg_health"))

    # ---- opening duels: the FIRST kill of each round decides map control ----
    by_round: dict[int, list] = defaultdict(list)
    for k in kill_rows:
        by_round[k["rnd"]].append(k)
    for rnd, ks in by_round.items():
        ks.sort(key=lambda x: x["tick"])
        first = ks[0]
        if first["att"] and first["att"] != "None":
            slot(first["att"])["opening_kills"] += 1
        slot(first["vic"])["opening_deaths"] += 1

    # ---- trades: was a death avenged within the window (and did someone do the avenging)? ----
    for rnd, ks in by_round.items():
        for i, k in enumerate(ks):
            if not k["att"] or k["att"] == "None":
                continue
            for later in ks[i + 1:]:
                if later["tick"] - k["tick"] > TRADE_WINDOW_TICKS:
                    break
                # the killer gets killed by someone on the victim's team = the death was traded
                if later["vic"] == k["att"] and later["att"] and later["att"] != "None" \
                        and later["att_team"] == k["vic_team"]:
                    slot(k["vic"])["traded_deaths"] += 1
                    slot(later["att"])["trade_kills"] += 1
                    break

    # ---- derive coaching metrics ----
    out_players = []
    for sid, s in P.items():
        if s["kills"] + s["deaths"] == 0:
            continue
        op_total = s["opening_kills"] + s["opening_deaths"]
        untraded = s["deaths"] - s["traded_deaths"]
        pl = {
            "steamid": sid,
            "name": s["name"],
            "team": s["team"],
            "kills": s["kills"], "deaths": s["deaths"], "assists": s["assists"],
            "kd": round(s["kills"] / s["deaths"], 2) if s["deaths"] else float(s["kills"]),
            "adr": round(s["dmg"] / total_rounds, 1),
            "hs_pct": round(100 * s["hs"] / s["kills"], 1) if s["kills"] else 0.0,
            "opening_kills": s["opening_kills"],
            "opening_deaths": s["opening_deaths"],
            "opening_winrate": round(100 * s["opening_kills"] / op_total, 1) if op_total else None,
            "trade_kills": s["trade_kills"],
            "traded_deaths": s["traded_deaths"],
            "untraded_deaths": untraded,
            "untraded_pct": round(100 * untraded / s["deaths"], 1) if s["deaths"] else 0.0,
            "flash_assists": s["flash_assists"],
            "wallbang_kills": s["wallbang"],
            "top_weapons": sorted(s["weapons"].items(), key=lambda x: -x[1])[:3],
            "died_to": sorted(s["killed_by"].items(), key=lambda x: -x[1])[:3],
        }
        out_players.append(pl)

    # lobby baselines — notes are judged against the match the player was actually in
    dead = [x for x in out_players if x["deaths"] > 0]
    ctx = {
        "avg_untraded": sum(x["untraded_pct"] for x in dead) / len(dead) if dead else 0.0,
        "avg_adr": sum(x["adr"] for x in out_players) / len(out_players) if out_players else 0.0,
        "flash_total": sum(x["flash_assists"] for x in out_players),
    }
    for pl in out_players:
        pl["notes"] = coach_notes(pl, total_rounds, ctx)

    out_players.sort(key=lambda x: -x["adr"])
    return {
        "map": header.get("map_name"),
        "server": header.get("server_name"),
        "rounds": total_rounds,
        "match_baselines": {k: round(v, 1) for k, v in ctx.items()},
        "players": out_players,
    }


def coach_notes(pl: dict, rounds: int, ctx: dict) -> list[str]:
    """Turn numbers into the few sentences a coach would actually say.

    ctx carries lobby averages so a note fires only when the player stands out against the
    match he was actually in — in a pug where nobody trades, "you don't trade" is noise."""
    n = []
    ow = pl["opening_winrate"]
    if ow is not None and pl["opening_kills"] + pl["opening_deaths"] >= 3:
        if ow < 40:
            n.append(f"Проигрывает входные дуэли ({ow}%) — заходит первым без поддержки/утилити. "
                     f"Открытий: {pl['opening_kills']} побед / {pl['opening_deaths']} смертей.")
        elif ow >= 65:
            n.append(f"Сильный entry ({ow}% входных дуэлей) — стоит чаще отдавать ему первый контакт.")
    # only if he dies alone noticeably more than the rest of this lobby
    if pl["deaths"] >= 5 and pl["untraded_pct"] > ctx["avg_untraded"] + 12:
        n.append(f"{pl['untraded_pct']}% смертей не разменяны против {ctx['avg_untraded']:.0f}% "
                 f"в среднем по матчу — умирает вне поддержки чаще остальных. Позиционирование "
                 f"и тайминги захода вместе с тиммейтом.")
    if pl["adr"] < ctx["avg_adr"] * 0.7 and pl["kills"] >= 3:
        n.append(f"Низкий урон ({pl['adr']} ADR) при нормальных фрагах — мало добивает и мало "
                 f"работает по нескольким целям за раунд.")
    if pl["hs_pct"] < 30 and pl["kills"] >= 5:
        n.append(f"HS всего {pl['hs_pct']}% — прицел уводит с уровня головы, нужна тренировка "
                 f"crosshair placement.")
    # pointless to flag zero flashes in a match where nobody flashed at all
    if pl["flash_assists"] == 0 and rounds >= 8 and ctx["flash_total"] >= 3:
        n.append("Ноль флеш-ассистов, хотя в матче команды активно флешили — "
                 "не поддерживает тиммейтов утилитой.")
    return n


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print(__doc__)
        return 1
    res = parse(args[0])

    if "--json" in sys.argv:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    print(f"\n{'='*70}\nКарта: {res['map']}   раундов: {res['rounds']}\n{'='*70}")
    print(f"{'игрок':<18}{'K-D-A':<10}{'ADR':>6}{'HS%':>6}{'вход':>8}{'неразм.':>9}")
    print("-" * 70)
    for p in res["players"]:
        ow = f"{p['opening_winrate']}%" if p["opening_winrate"] is not None else "—"
        kda = f"{p['kills']}-{p['deaths']}-{p['assists']}"
        untraded = f"{p['untraded_pct']}%"
        print(f"{p['name'][:17]:<18}{kda:<10}{p['adr']:>6}{p['hs_pct']:>6}{ow:>8}{untraded:>9}")
    print(f"\n{'='*70}\nЗАМЕЧАНИЯ ТРЕНЕРА\n{'='*70}")
    for p in res["players"]:
        if p["notes"]:
            print(f"\n▸ {p['name']}")
            for note in p["notes"]:
                print(f"   • {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
