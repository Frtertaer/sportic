"""Live-search match intelligence via OmniRoute.

Replaces the old X.com provider. Grok is the only OmniRoute worker with real
live X/Twitter + web access, so it fetches the most recent *qualitative*
team-news for the fixture (injuries, suspensions, lineup leaks, manager
comments, morale, venue weather). A reliable worker (claude-code) then distills
that prose into structured ``XSocialSignal`` items.

Design guardrails:
  * QUALITATIVE ONLY — never numbers/odds/xG. Those are the scrapers' job and
    are the model's hard ground truth; live search is an overlay laid on top.
  * ANTI-FABRICATION — the distiller may only keep facts explicitly present in
    the live dump. No invented injuries, dates, or sources. Empty is fine.
  * GRACEFUL — any failure returns ``scrape_success=False`` with a message; the
    pipeline already treats this source as optional.
"""
from __future__ import annotations

import logging
import re
from datetime import date, timezone, datetime

import config
from models import Player, PlayerStatus, XSocialData, XSocialSignal

logger = logging.getLogger(__name__)

# grok = the only worker with live web/X access (verified). claude-code = honest,
# reliable structured extraction (it self-reports "NO LIVE ACCESS", so it is used
# only to distill, never to search).
_SEARCH_WORKER = "grok"
_DISTILL_WORKER = "claude-code"

_VALID_TYPES = {
    "injury_update", "lineup_leak", "transfer_rumor",
    "morale", "tactical", "weather", "suspension",
}
_VALID_CONF = {"HIGH", "MEDIUM", "LOW"}

_CITATION_RE = re.compile(r"<grok:render.*?</grok:render>", re.DOTALL)

_SEARCH_PROMPT = """It is {today}. Use your LIVE web and X/Twitter access to find the most recent
REAL team news for the upcoming football match: {team1} vs {team2}.

For EACH team report only what you can actually find right now:
- confirmed or doubtful injuries / fitness updates
- suspensions
- expected lineup, rotation or notable absences
- relevant manager press-conference comments
- locker-room morale / off-pitch context
- weather at the venue if mentioned

For every item give: which team, the category, a one-line fact, the approximate
date, and the outlet/source. If you find NOTHING current for a team, say so
explicitly for that team. Do NOT guess, infer, or invent anything. Facts only."""

_DISTILL_SYSTEM = (
    "You convert a live-search news dump into strict JSON. You MUST NOT invent "
    "anything: include only items explicitly present in the input. If the input "
    "reports no news for a team, add no signals for that team. Output ONLY a JSON "
    "object — no prose, no code fences."
)

_DISTILL_PROMPT = """Convert the live-search results into EXACTLY this JSON schema:
{{
  "signals": [
    {{"type": "injury_update|lineup_leak|transfer_rumor|morale|tactical|weather|suspension",
      "content": "<team>: <one-line fact>",
      "confidence": "HIGH|MEDIUM|LOW",
      "source_hint": "<outlet/journalist or null>",
      "recency": "<approx date or 'today' or null>"}}
  ],
  "absences": [
    {{"team": "team1|team2",
      "name": "<player name>",
      "position": "GK|DEF|MID|FWD or empty",
      "status": "injured|suspended|doubtful"}}
  ],
  "sentiment_team1": "positive|neutral|negative|null",
  "sentiment_team2": "positive|neutral|negative|null",
  "key_narrative": "<=2 sentence dominant storyline across both teams, or null"
}}

Rules:
- confidence: HIGH = stated by a named outlet/official; MEDIUM = reported/expected; LOW = rumor.
- absences: list ONLY players the input says will miss the match or are genuinely doubtful.
  status "injured"/"suspended" = will NOT play; "doubtful" = fitness test / 50-50 / managed minutes.
  Do NOT add a player unless the input names them. No invented names. Empty list is fine.
- Keep only facts that are actually in the input. Strip any citation markup. Never fabricate.
- team1 = {team1}; team2 = {team2}.

LIVE-SEARCH RESULTS:
{dump}"""


class XSocialScraper:
    """Fetch live qualitative match intelligence through OmniRoute (grok + distill)."""

    def __init__(self, team1: str, team2: str) -> None:
        self.team1 = team1
        self.team2 = team2

    async def _close(self) -> None:
        pass

    async def scrape(
        self,
        team1: str | None = None,
        team2: str | None = None,
        *,
        league: str | None = None,
    ) -> XSocialData:
        del league
        t1 = team1 or self.team1
        t2 = team2 or self.team2

        if not getattr(config, "USE_OMNIROUTE", False) or not config.OMNIROUTE_PASS:
            return XSocialData(
                team1_name=t1, team2_name=t2, scrape_success=False,
                error_message="Live search requires OmniRoute (set USE_OMNIROUTE + OMNIROUTE_PASS).",
            )

        from agents.model_utils import _call_omniroute

        # ── 1) Live fetch via grok ───────────────────────────────────────────
        today = datetime.now(timezone.utc).date().isoformat()
        try:
            dump = await _call_omniroute(
                model=_SEARCH_WORKER,
                max_tokens=900,
                system="",
                user_prompt=_SEARCH_PROMPT.format(today=today, team1=t1, team2=t2),
            )
        except Exception as exc:  # noqa: BLE001 — optional source, degrade gracefully
            logger.warning("Live-search fetch (%s) failed: %s", _SEARCH_WORKER, exc)
            return XSocialData(
                team1_name=t1, team2_name=t2, scrape_success=False,
                error_message=f"Live search fetch failed: {exc}",
            )

        dump = _CITATION_RE.sub("", dump or "").strip()
        if not dump or "NO LIVE ACCESS" in dump.upper():
            return XSocialData(
                team1_name=t1, team2_name=t2, scrape_success=False,
                error_message="Live search returned no usable content.",
            )

        # ── 2) Distill to structured signals via a reliable worker ───────────
        try:
            raw = await _call_omniroute(
                model=_DISTILL_WORKER,
                max_tokens=1200,
                system=_DISTILL_SYSTEM,
                user_prompt=_DISTILL_PROMPT.format(team1=t1, team2=t2, dump=dump[:6000]),
            )
            from analyzer import _loads_agent_json
            parsed = _loads_agent_json(raw, context="live-search distill")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Live-search distill (%s) failed: %s", _DISTILL_WORKER, exc)
            return XSocialData(
                team1_name=t1, team2_name=t2, scrape_success=False,
                error_message=f"Live search distill failed: {exc}",
            )

        signals = self._build_signals(parsed.get("signals"))
        absences1, absences2 = self._build_absences(parsed.get("absences"))
        return XSocialData(
            team1_name=t1,
            team2_name=t2,
            signals=signals,
            sentiment_team1=_norm_sentiment(parsed.get("sentiment_team1")),
            sentiment_team2=_norm_sentiment(parsed.get("sentiment_team2")),
            key_narrative=_clean_opt(parsed.get("key_narrative")),
            team1_absences=absences1,
            team2_absences=absences2,
            scrape_success=True,
        )

    @staticmethod
    def _build_signals(raw_signals: object) -> list[XSocialSignal]:
        signals: list[XSocialSignal] = []
        if not isinstance(raw_signals, list):
            return signals
        for item in raw_signals[:12]:
            if not isinstance(item, dict):
                continue
            content = _clean_opt(item.get("content"))
            if not content:
                continue
            stype = str(item.get("type") or "").strip().lower()
            if stype not in _VALID_TYPES:
                stype = "tactical"
            conf = str(item.get("confidence") or "MEDIUM").strip().upper()
            if conf not in _VALID_CONF:
                conf = "MEDIUM"
            signals.append(
                XSocialSignal(
                    type=stype,
                    content=content[:300],
                    confidence=conf,
                    source_hint=_clean_opt(item.get("source_hint")),
                    recency=_clean_opt(item.get("recency")),
                )
            )
        return signals

    @staticmethod
    def _build_absences(raw_absences: object) -> tuple[list[Player], list[Player]]:
        """Split distilled absences into (team1, team2) Player lists."""
        team1: list[Player] = []
        team2: list[Player] = []
        if not isinstance(raw_absences, list):
            return team1, team2
        for item in raw_absences[:16]:
            if not isinstance(item, dict):
                continue
            name = _clean_opt(item.get("name"))
            if not name:
                continue
            status = _map_status(item.get("status"))
            if status is None:
                continue
            player = Player(
                name=name[:80],
                position=str(item.get("position") or "").strip()[:8],
                status=status,
                injury_detail="live-search",
            )
            target = str(item.get("team") or "").strip().lower()
            (team2 if target in {"team2", "2", "away"} else team1).append(player)
        return team1, team2


def _map_status(value: object) -> PlayerStatus | None:
    v = str(value or "").strip().lower()
    if v in {"injured", "injury", "out", "ruled out"}:
        return PlayerStatus.INJURED
    if v in {"suspended", "suspension", "banned"}:
        return PlayerStatus.SUSPENDED
    if v in {"doubtful", "doubt", "questionable", "fitness", "50-50", "managed"}:
        return PlayerStatus.DOUBTFUL
    return None


def _norm_sentiment(value: object) -> str | None:
    v = str(value or "").strip().lower()
    return v if v in {"positive", "neutral", "negative"} else None


def _clean_opt(value: object) -> str | None:
    if value is None:
        return None
    v = str(value).strip()
    if not v or v.lower() in {"null", "none", "n/a"}:
        return None
    return v
