#!/usr/bin/env python3
"""CS2 AI analyst on top of the math model (veto_sim) + grok live-search.

Flow: math prediction (per-map Glicko + veto Monte-Carlo) → grok LIVE-SEARCH
(roster/stand-ins/form the ratings cannot see) → claude-code Analyst (reasoned
verdict: does the live intel change the read? is the model over-confident given
thin data? final lean + which maps decide it). Zero cost (OmniRoute cookie gateway).

The roster/stand-in layer is the point: the 4-AI debate flagged stale rosters as the
#1 silent killer of CS2 models — a team's rating is meaningless across a lineup change.

  python cs2_analyst.py match <slug>
  python cs2_analyst.py predict <t1_id> <t2_id> <bo>
"""
from __future__ import annotations

import http.cookiejar
import json
import re
import ssl
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import bo3_client as bo3  # noqa: E402
import veto_sim  # noqa: E402

DATE = datetime.now(timezone.utc).date().isoformat()


def _omni_creds() -> tuple[str, str, str]:
    env = {}
    try:
        for line in open("/opt/sportsbot/.env"):
            line = line.strip()
            if line.startswith("OMNIROUTE_") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except OSError:
        pass
    return (env.get("OMNIROUTE_URL", "https://127.0.0.1"),
            env.get("OMNIROUTE_USER", "admin"), env.get("OMNIROUTE_PASS", ""))


OMNI_URL, OMNI_USER, OMNI_PASS = _omni_creds()


def call_omni(model: str, prompt: str, system: str = "", max_tokens: int = 1100) -> str:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    cj = http.cookiejar.CookieJar()
    op = urllib.request.build_opener(
        urllib.request.HTTPSHandler(context=ctx),
        urllib.request.HTTPCookieProcessor(cj),
    )
    base = OMNI_URL.rstrip("/")
    op.open(urllib.request.Request(
        base + "/api/auth/login",
        data=json.dumps({"username": OMNI_USER, "password": OMNI_PASS}).encode(),
        headers={"Content-Type": "application/json"}), timeout=30)
    full = f"{system}\n\n{prompt}" if system else prompt
    r = op.open(urllib.request.Request(
        base + "/api/managed-agents/chat-completion",
        data=json.dumps({
            "model": model, "messages": [{"role": "user", "content": full}],
            "stream": False, "tools": [], "tool_choice": "none", "max_tokens": max_tokens,
        }).encode(),
        headers={"Content-Type": "application/json"}), timeout=230)
    payload = json.load(r)
    return payload["choices"][0]["message"]["content"].strip()


_GROK_RENDER_BLOCK = re.compile(
    r"<grok:render\b.*?(?:</grok:render\s*>|$)",
    flags=re.IGNORECASE | re.DOTALL,
)
_GROK_TAG = re.compile(r"</?grok:[^>]*(?:>|$)", flags=re.IGNORECASE | re.DOTALL)


def _strip_grok(t: str) -> str:
    """Remove provider-only render/citation markup before text reaches logs or HTML."""
    text = _GROK_RENDER_BLOCK.sub("", t or "")
    text = _GROK_TAG.sub("", text)
    text = re.sub(r"^\s*(?:grok:|\[grok\])\s*", "", text, flags=re.IGNORECASE)
    text = re.sub(r"[`*]+", "", text)
    text = re.sub(r"[\u200b-\u200d\ufeff]", "", text)
    return " ".join(text.split()).strip()


def _safe_call(model: str, prompt: str, system: str = "", max_tokens: int = 600) -> tuple[bool, str]:
    try:
        return True, _strip_grok(call_omni(model, prompt, system=system, max_tokens=max_tokens))
    except Exception as exc:  # noqa: BLE001
        return False, f"({model} unavailable: {type(exc).__name__})"


# ── Dual live-search: grok (X/web) + gemini-cli (Google grounding) ───────────
def live_search(t1: str, t2: str) -> str:
    q = (f"It is {DATE}. Using your LIVE web/X access, give the CURRENT state of the "
         f"Counter-Strike 2 teams '{t1}' and '{t2}' before their match. For EACH team: "
         f"confirmed roster and ANY stand-ins / recent player changes (critical), last 2-3 "
         f"results + form, and relevant news (visa, bootcamp, lineup, motivation). Facts only, "
         f"cite outlets. If nothing recent for a team, say so. Do not invent.")
    parts = []
    for src in ("grok", "gemini-cli"):
        ok, txt = _safe_call(src, q, max_tokens=700)
        if ok and "NO LIVE ACCESS" not in txt.upper() and len(txt) > 40:
            parts.append(f"[{src}]\n{txt}")
        else:
            parts.append(f"[{src}] {txt if not ok else 'no usable result'}")
    return "\n\n".join(parts)


_COUNCIL_SYS = "You are a sharp, honest CS2 betting analyst. Be concise. No hype."

_COUNCIL_PROMPT = """MODEL (per-map Glicko-2 + veto Monte-Carlo, 20k sims):
{pred}

LIVE INTEL (grok + gemini):
{live}

Give YOUR independent read in <=110 words: your lean, confidence (low/med/high), the
1-2 KEY factors (esp. any ROSTER CHANGE / STAND-IN that could invalidate the ratings,
and whether the model looks over-confident on thin/{r1}-{r2} map data), and BET or PASS."""

_SYNTH_PROMPT = """A 4-AI council analysed a CS2 match. Math model + live intel below, then each
analyst's take. Synthesize the CONSENSUS in <=170 words: where they AGREE vs DISAGREE, the
final lean + confidence, the 1-2 decisive maps, and a clear BET or PASS. Be honest — if the
council leans PASS (stale roster / stand-in / thin data / overstated edge), say PASS.

MODEL:
{pred}

LIVE INTEL:
{live}

COUNCIL TAKES:
{takes}

End with EXACTLY one machine-readable line, nothing after it:
DECISION: PASS
   or
DECISION: BET | <exact team name> | <ml or map-handicap or total-maps> | conf=<low|med|high>"""

COUNCIL = ["claude-code", "gpt-cursor", "grok", "gemini-cli"]


def council_verdict(pred: dict, live: str) -> tuple[dict, str]:
    p = json.dumps(pred, ensure_ascii=False, indent=2)
    base = _COUNCIL_PROMPT.format(pred=p, live=live, r1=pred.get("rated1"), r2=pred.get("rated2"))
    takes: dict[str, str] = {}
    for w in COUNCIL:
        ok, txt = _safe_call(w, base, system=_COUNCIL_SYS, max_tokens=450)
        takes[w] = txt
    takes_block = "\n\n".join(f"=== {w} ===\n{t}" for w, t in takes.items())
    # synthesis by the most reliable worker
    _, synth = _safe_call("claude-code",
                          _SYNTH_PROMPT.format(pred=p, live=live, takes=takes_block),
                          system=_COUNCIL_SYS, max_tokens=700)
    return takes, synth


def parse_decision(synth: str) -> dict:
    """Extract the machine-readable DECISION line from the synthesis."""
    m = re.search(r"DECISION:\s*(PASS|BET)([^\n]*)", synth, re.IGNORECASE)
    if not m:
        return {"action": "PASS", "side": None, "market": None, "conf": None}
    if m.group(1).upper() == "PASS":
        return {"action": "PASS", "side": None, "market": None, "conf": None}
    parts = [p.strip() for p in m.group(2).split("|")]
    parts = [p for p in parts if p]
    side = parts[0] if parts else None
    market = parts[1] if len(parts) > 1 else "ml"
    conf = None
    cm = re.search(r"conf\s*=\s*(low|med|high)", m.group(2), re.IGNORECASE)
    if cm:
        conf = cm.group(1).lower()
    return {"action": "BET", "side": side, "market": market, "conf": conf}


def analyze(t1: int, t2: int, bo: int) -> dict:
    """Full pipeline → structured result (used by the monitor)."""
    pred = veto_sim.predict(t1, t2, bo)
    live = live_search(pred["team1"], pred["team2"])
    takes, synth = council_verdict(pred, live)
    return {"pred": pred, "live": live, "takes": takes, "synth": synth,
            "decision": parse_decision(synth)}


def report(t1: int, t2: int, bo: int) -> None:
    pred = veto_sim.predict(t1, t2, bo)
    print("=" * 64)
    print(f"  {pred['team1']}  vs  {pred['team2']}   (Bo{pred['bo']})")
    print(f"  MODEL: {pred['team1']} {pred['p1_win']*100:.0f}% ({pred['fair_odds_t1']})  |  "
          f"{pred['team2']} {pred['p2_win']*100:.0f}% ({pred['fair_odds_t2']})")
    print(f"  rated maps: {pred['rated1']}/7 vs {pred['rated2']}/7 | score dist: {pred['score_dist']}")
    print(f"  {pred['team1']} map winrate: {pred['map_t1_winrate']}")
    print("-" * 64)
    live = live_search(pred["team1"], pred["team2"])
    print("LIVE INTEL (grok + gemini):\n" + live)
    print("-" * 64)
    takes, synth = council_verdict(pred, live)
    for w, t in takes.items():
        print(f"\n[{w}]\n{t}")
    print("-" * 64)
    print("COUNCIL SYNTHESIS:\n" + synth)
    print("=" * 64)


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "match":
        m = bo3.get_match(sys.argv[2])
        report(m["team1_id"], m["team2_id"], m.get("bo_type") or 3)
    elif cmd == "predict":
        report(int(sys.argv[2]), int(sys.argv[3]), int(sys.argv[4]))
    else:
        print(__doc__)


if __name__ == "__main__":
    main()
