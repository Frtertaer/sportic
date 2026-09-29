"""
Telegram message formatter.

Uses HTML parse mode and escapes every dynamic value before interpolation.
"""
from __future__ import annotations

import html
import re
from typing import Optional

from models import PredictionReport
from scrapers.registry import SCRAPER_REGISTRY, get_loading_source_lines

_CONFIDENCE_EMOJI = {"HIGH": "🟢", "MEDIUM": "🟡", "LOW": "🔴"}
_RISK_EMOJI = {"HIGH": "🔴", "MEDIUM": "🟡", "LOW": "🟢"}
_BAR_WIDTH = 10
_TELEGRAM_TEXT_LIMIT = 4090
_HTML_TAG_RE = re.compile(r"(<[^>]+>|[^<]+)")
_HTML_SIMPLE_TAG_RE = re.compile(r"^<(/?)(b|i|code)>$")
_SPLIT_SEPARATOR = "─" * 32


def _esc(value: object) -> str:
    return html.escape(str(value), quote=False)


def _bar(pct: float) -> str:
    filled = round(max(0.0, min(100.0, pct)) / 100 * _BAR_WIDTH)
    return f"<code>{'#' * filled}{'-' * (_BAR_WIDTH - filled)}</code> <b>{pct:.0f}%</b>"


def _badge(level: str) -> str:
    safe_level = _esc(level)
    return f"{_CONFIDENCE_EMOJI.get(level, '◻')} <b>{safe_level}</b>"


def _truncate_telegram(text: str) -> str:
    if len(text) <= _TELEGRAM_TEXT_LIMIT:
        return text

    parts = _HTML_TAG_RE.findall(text)
    output: list[str] = []
    open_tags: list[str] = []
    current_len = 0
    truncated = False

    def closing_tags() -> str:
        return "".join(f"</{tag}>" for tag in reversed(open_tags))

    def reserve_len() -> int:
        return len("...") + len(closing_tags())

    def trim_text_fragment(fragment: str, limit: int) -> str:
        if limit <= 0:
            return ""
        trimmed = fragment[:limit]
        entity_start = trimmed.rfind("&")
        if entity_start != -1 and ";" not in trimmed[entity_start:]:
            trimmed = trimmed[:entity_start]
        return trimmed

    for part in parts:
        tag_match = _HTML_SIMPLE_TAG_RE.fullmatch(part)
        if tag_match:
            is_closing = tag_match.group(1) == "/"
            tag_name = tag_match.group(2)
            next_open_tags = list(open_tags)
            if is_closing:
                if next_open_tags and next_open_tags[-1] == tag_name:
                    next_open_tags.pop()
            else:
                next_open_tags.append(tag_name)
            suffix_len = len("...") + len("".join(f"</{tag}>" for tag in reversed(next_open_tags)))
            if current_len + len(part) + suffix_len > _TELEGRAM_TEXT_LIMIT:
                truncated = True
                break
            output.append(part)
            current_len += len(part)
            open_tags = next_open_tags
            continue

        allowed = _TELEGRAM_TEXT_LIMIT - current_len - reserve_len()
        if allowed <= 0:
            truncated = True
            break
        if len(part) <= allowed:
            output.append(part)
            current_len += len(part)
            continue

        trimmed_part = trim_text_fragment(part, allowed)
        if trimmed_part:
            output.append(trimmed_part)
            current_len += len(trimmed_part)
        truncated = True
        break

    result = "".join(output)
    if truncated:
        result += "..."
    result += closing_tags()
    return result[:_TELEGRAM_TEXT_LIMIT]


def split_telegram_message(text: str) -> list[str]:
    """Split a long HTML message into ≤4090-char parts at section separators.

    Tries to split at ``─`` separator lines first. If a section itself exceeds
    the limit it falls back to ``_truncate_telegram`` on that chunk.
    Returns a list of 1–N parts, each safe to send as a separate message.
    """
    if len(text) <= _TELEGRAM_TEXT_LIMIT:
        return [text]

    lines = text.split("\n")
    parts: list[str] = []
    current_lines: list[str] = []
    current_len = 0

    def flush() -> None:
        nonlocal current_len
        if not current_lines:
            return
        chunk = "\n".join(current_lines)
        if len(chunk) > _TELEGRAM_TEXT_LIMIT:
            chunk = _truncate_telegram(chunk)
        parts.append(chunk)
        current_lines.clear()
        current_len = 0

    for line in lines:
        line_cost = len(line) + 1  # +1 for \n
        is_separator = line.strip() == _SPLIT_SEPARATOR

        if is_separator and current_len + line_cost > _TELEGRAM_TEXT_LIMIT:
            flush()
            continue

        if current_len + line_cost > _TELEGRAM_TEXT_LIMIT:
            flush()

        current_lines.append(line)
        current_len += line_cost

    flush()
    return parts or [text[:_TELEGRAM_TEXT_LIMIT]]


def _append_segment_block_with_sample_gate(
    target_lines: list[str],
    title: str,
    segments: list[dict],
    *,
    limit: int,
) -> None:
    if not segments:
        return
    target_lines.append("")
    target_lines.append(f"<b>{title}</b>")
    for segment in segments[:limit]:
        label = _esc(segment.get("segment", "Unknown"))
        tracked = int(segment.get("tracked_bets", 0) or 0)
        profit = float(segment.get("profit_units", 0.0) or 0.0)
        roi = segment.get("roi_pct")
        sample_gate = str(segment.get("sample_gate") or "").strip()
        roi_text = f" ({float(roi):+.1f}% ROI)" if roi is not None else ""
        gate_text = f" [{_esc(sample_gate)}]" if sample_gate else ""
        target_lines.append(
            f"- {label}{gate_text}: <b>{profit:+.2f}u</b>{roi_text}, {tracked} bets"
        )


def _parse_score(score: str) -> Optional[tuple[int, int]]:
    try:
        left, right = score.replace(" ", "").split("-", 1)
        return int(left), int(right)
    except (AttributeError, TypeError, ValueError):
        return None


def _evaluate_top_rec(
    top_rec: str,
    team1: str,
    team2: str,
    actual_result: str,
    score: str,
) -> Optional[str]:
    rec_lower = top_rec.lower()
    score_tuple = _parse_score(score)

    if score_tuple is not None:
        home_goals, away_goals = score_tuple
        total_goals = home_goals + away_goals
        both_scored = home_goals > 0 and away_goals > 0
        team1_won = home_goals > away_goals
        team2_won = away_goals > home_goals
        is_draw = home_goals == away_goals

        if "over 1.5" in rec_lower or "o1.5" in rec_lower:
            return "correct" if total_goals > 1 else "wrong"
        if "under 1.5" in rec_lower or "u1.5" in rec_lower:
            return "correct" if total_goals < 2 else "wrong"
        if "over 3.5" in rec_lower or "o3.5" in rec_lower:
            return "correct" if total_goals > 3 else "wrong"
        if "under 3.5" in rec_lower or "u3.5" in rec_lower:
            return "correct" if total_goals < 4 else "wrong"

        if any(token in rec_lower for token in ("over 2.5", "o2.5", "тб 2.5")):
            return "correct" if total_goals > 2 else "wrong"
        if any(token in rec_lower for token in ("under 2.5", "u2.5", "тм 2.5")):
            return "correct" if total_goals < 3 else "wrong"
        if "btts" in rec_lower or "обе забьют" in rec_lower:
            if any(token in rec_lower for token in ("yes", "да")):
                return "correct" if both_scored else "wrong"
            if any(token in rec_lower for token in ("no", "нет")):
                return "correct" if not both_scored else "wrong"

        if "draw no bet" in rec_lower or "dnb" in rec_lower:
            if team1.lower() in rec_lower:
                if is_draw:
                    return "push"
                return "correct" if team1_won else "wrong"
            if team2.lower() in rec_lower:
                if is_draw:
                    return "push"
                return "correct" if team2_won else "wrong"
        if "1x" in rec_lower or f"{team1.lower()} or draw" in rec_lower:
            return "correct" if team1_won or is_draw else "wrong"
        if "x2" in rec_lower or f"draw or {team2.lower()}" in rec_lower:
            return "correct" if team2_won or is_draw else "wrong"
        if "12" in rec_lower or "no draw" in rec_lower:
            return "correct" if not is_draw else "wrong"

        for team_name, is_team1 in ((team1, True), (team2, False)):
            if team_name.lower() not in rec_lower:
                continue
            match = re.search(
                rf"{re.escape(team_name.lower())}\s*([+-]?\d+(?:\.\d+)?)",
                rec_lower,
            )
            if not match:
                continue
            line = float(match.group(1))
            goal_diff = (home_goals - away_goals) if is_team1 else (away_goals - home_goals)
            adjusted = goal_diff + line
            if adjusted > 0:
                return "correct"
            if adjusted < 0:
                return "wrong"
            return "push"

    if actual_result == "W1" and team1.lower() in rec_lower:
        return "correct"
    if actual_result == "W2" and team2.lower() in rec_lower:
        return "correct"
    if actual_result == "DRAW" and ("draw" in rec_lower or "ничья" in rec_lower):
        return "correct"
    if actual_result in {"W1", "W2", "DRAW"} and any(
        token in rec_lower for token in ("win", "побед", "draw", "ничья")
    ):
        return "wrong"
    return None


def format_loading(team1: str, team2: str) -> str:
    lines = [
        f"⚽ <b>{_esc(team1)} vs {_esc(team2)}</b>",
        "",
        "🔄 <b>Собираю данные...</b>",
        "",
        "📡 Источники:",
        *get_loading_source_lines(),
        "",
        "<i>Займет ~15-35 секунд...</i>",
    ]
    return "\n".join(lines)


def format_analysing(
    team1: str,
    team2: str,
    raw_completeness: int,
    *,
    applicable_completeness: Optional[int] = None,
    unsupported_count: int = 0,
    resolved_team1: Optional[str] = None,
    resolved_team2: Optional[str] = None,
) -> str:
    header = f"⚽ <b>{_esc(team1)} vs {_esc(team2)}</b>\n"
    if resolved_team1 and resolved_team2:
        if resolved_team1 != team1 or resolved_team2 != team2:
            header += f"<i>Найдено: {_esc(resolved_team1)} vs {_esc(resolved_team2)}</i>\n"
    header += "\n"
    if unsupported_count > 0 and applicable_completeness is not None:
        return (
            header
            + "🤖 <b>Анализирую данные...</b>\n\n"
            + f"Собрано: <b>{applicable_completeness}% применимых источников</b>\n"
            + f"Провайдер не покрывает: <b>{unsupported_count}</b> источников\n"
            + f"<i>Raw completeness: {raw_completeness}% от полного registry.</i>\n\n"
            + "<i>AI обрабатывает информацию...</i>"
        )
    return (
        header
        + f"🤖 <b>Анализирую данные ({raw_completeness}% данных собрано)...</b>\n\n"
        + "<i>AI обрабатывает информацию...</i>"
    )


def format_stats(
    stats: Optional[dict],
    n_total: int,
    bankroll_stats: Optional[dict] = None,
    per_league_stats: Optional[list[dict]] = None,
    calibration_curve: Optional[dict] = None,
    odds_movement: Optional[dict] = None,
) -> str:
    """Format /stats reply with historical accuracy metrics."""
    def _append_segment_block_sample(target_lines: list[str], title: str, segments: list[dict], limit: int = 4) -> None:
        if not segments:
            return
        target_lines.append("")
        target_lines.append(f"<b>{title}</b>")
        for segment in segments[:limit]:
            label = _esc(segment.get("segment", "Unknown"))
            tracked = int(segment.get("tracked_bets", 0) or 0)
            profit = float(segment.get("profit_units", 0.0) or 0.0)
            roi = segment.get("roi_pct")
            sample_gate = str(segment.get("sample_gate") or "").strip()
            roi_text = f" ({float(roi):+.1f}% ROI)" if roi is not None else ""
            gate_text = f" [{_esc(sample_gate)}]" if sample_gate else ""
            target_lines.append(f"РІР‚Сћ {label}{gate_text}: <b>{profit:+.2f}u</b>{roi_text}, {tracked} bets")
    sep = "─" * 32
    lines = ["📊 <b>СТАТИСТИКА САМООБУЧЕНИЯ</b>", sep, ""]

    lines.append(f"Всего прогнозов в базе: <b>{n_total}</b>")
    if not stats or stats.get("n_with_result", 0) == 0:
        lines.append("С известным результатом: <b>0</b>")
        lines.append("")
        lines.append(
            "<i>Введите реальные счёты командой:</i>\n"
            "<code>/result Arsenal 2-1 Chelsea</code>\n"
            "чтобы бот начал учиться на своих ошибках."
        )
        return "\n".join(lines)

    n = stats["n_with_result"]
    acc = stats.get("acc_1x2", 0.0)
    brier = stats.get("brier_1x2")

    lines.append(f"С известным результатом: <b>{n}</b>")
    lines.append("")
    lines.append(f"🎯 Точность (1X2): <b>{acc:.1f}%</b>")
    lines.append("<i>(угадан победитель/ничья)</i>")
    lines.append("")

    if brier is not None:
        lines.append(f"📉 Brier score 1X2: <b>{brier:.4f}</b>")
        lines.append("<i>(0.0 = идеально, 0.333 = случайно)</i>")
        lines.append("")
        if brier < 0.18:
            quality = "🟢 Отличная калибровка"
        elif brier < 0.23:
            quality = "🟡 Хорошая калибровка"
        elif brier < 0.27:
            quality = "🟠 Удовлетворительная"
        else:
            quality = "🔴 Переоценка вероятностей"
        lines.append(f"Качество: <b>{quality}</b>")

    if bankroll_stats:
        lines.append("")
        lines.append("💼 <b>Paper bankroll</b>")
        lines.append(
            f"Банк: <b>{bankroll_stats['starting_bankroll_units']:.2f}u → "
            f"{bankroll_stats['current_bankroll_units']:.2f}u</b>"
        )
        lines.append(
            f"Прибыль: <b>{bankroll_stats['profit_units']:+.2f}u</b> | "
            f"ROI: <b>{bankroll_stats['roi_pct']:+.2f}%</b>"
        )
        lines.append(
            f"Ставок: <b>{int(bankroll_stats['tracked_bets'])}</b> | "
            f"W {int(bankroll_stats['wins'])} / L {int(bankroll_stats['losses'])} / P {int(bankroll_stats['pushes'])}"
        )
        if bankroll_stats.get("hit_rate") is not None:
            lines.append(
                f"Hit rate: <b>{float(bankroll_stats['hit_rate']):.1f}%</b> | "
                f"средняя ставка <b>{float(bankroll_stats['avg_stake_units']):.2f}u</b>"
            )
        lines.append(
            f"Макс. просадка: <b>{float(bankroll_stats['max_drawdown_units']):.2f}u</b>"
        )
        # Recent form
        recent_form = bankroll_stats.get("recent_form_20")
        if recent_form:
            lines.append("")
            lines.append(f"<b>Recent form (last {int(recent_form.get('window_size', 20) or 20)} bets)</b>")
            lines.append(
                f"Прибыль: <b>{float(recent_form.get('profit_units', 0.0) or 0.0):+.2f}u</b> | "
                f"ROI: <b>{float(recent_form.get('roi_pct', 0.0) or 0.0):+.2f}%</b>"
            )
            lines.append(
                f"W {int(recent_form.get('wins', 0) or 0)} / "
                f"L {int(recent_form.get('losses', 0) or 0)} / "
                f"P {int(recent_form.get('pushes', 0) or 0)}"
            )
            if recent_form.get("streak_label"):
                lines.append(f"Current streak: <b>{_esc(recent_form.get('streak_label'))}</b>")

        # Segment breakdowns (best-by-market, best-by-competition, etc.)
        _append_segment_block_with_sample_gate(lines, "Best by market", bankroll_stats.get("by_market") or [], limit=4)
        _append_segment_block_with_sample_gate(lines, "Best by competition + policy", bankroll_stats.get("by_competition_policy") or [], limit=4)
        _append_segment_block_with_sample_gate(lines, "Best by risk", bankroll_stats.get("by_risk") or [], limit=4)
        _append_segment_block_with_sample_gate(lines, "Best by bankroll mode", bankroll_stats.get("by_bankroll_mode") or [], limit=4)
        _append_segment_block_with_sample_gate(lines, "Best by recommendation mode", bankroll_stats.get("by_recommendation_mode") or [], limit=4)
        _append_segment_block_with_sample_gate(lines, "Best by policy flag", bankroll_stats.get("by_policy_flag") or [], limit=4)

    # Per-league calibration breakdown
    if per_league_stats:
        lines.append("")
        lines.append("<b>По лигам (1X2):</b>")
        for lg in per_league_stats[:8]:
            acc = lg["accuracy_pct"]
            icon = "🟢" if acc >= 55 else ("🟡" if acc >= 40 else "🔴")
            lines.append(
                f"  {icon} {_esc(lg['league'])}: "
                f"<b>{acc:.1f}%</b> ({lg['correct']}/{lg['n']})"
            )

    # Calibration curve (ECE)
    if calibration_curve:
        lines.append("")
        lines.append("📐 <b>Калибровочная кривая (ECE)</b>")
        ece_val = calibration_curve["ece"]
        if ece_val < 3.0:
            ece_icon = "🟢"
        elif ece_val < 7.0:
            ece_icon = "🟡"
        elif ece_val < 12.0:
            ece_icon = "🟠"
        else:
            ece_icon = "🔴"
        lines.append(f"ECE: <b>{ece_icon} {ece_val:.1f}%</b> (n={calibration_curve['n_total']})")
        lines.append("<i>(0% = идеальная калибровка)</i>")
        lines.append("")
        lines.append("<code>Уверен. | Точность | Разрыв")
        for b in calibration_curve["bins"]:
            if b["n"] == 0:
                continue
            lo = int(b["range_lo"] * 100)
            hi = int(b["range_hi"] * 100)
            conf = b["avg_confidence"]
            acc = b["accuracy"]
            gap = b["gap"]
            bar_len = max(1, round(acc / 5))
            bar = "█" * bar_len
            lines.append(f"{lo:>3}-{hi:<3}% | {acc:5.1f}%{bar:>7s} | {gap:+.1f}%  (n={b['n']})")
        lines.append("</code>")

    # Odds movement summary
    if odds_movement and odds_movement.get("with_1x2_move", 0) >= 3:
        lines.append("")
        lines.append("📉 <b>Движение линий vs результат</b>")
        m = odds_movement
        total_m = m["with_1x2_move"]
        correct_m = m["with_1x2_move_correct"]
        acc_m = round(correct_m / total_m * 100, 1) if total_m else 0
        lines.append(f"Матчи с движением (1X2): <b>{total_m}</b>, точность: <b>{acc_m:.1f}%</b>")
        aligned = m["sharp_aligned"]
        aligned_won = m["sharp_aligned_won"]
        against = m["sharp_against"]
        against_won = m["sharp_against_won"]
        if aligned > 0:
            aligned_acc = round(aligned_won / aligned * 100, 1)
            icon = "🟢" if aligned_acc >= 55 else "🔴"
            lines.append(f"  {icon} Наш пик = sharp money: <b>{aligned_acc:.1f}%</b> ({aligned_won}/{aligned})")
        if against > 0:
            against_acc = round(against_won / against * 100, 1)
            icon = "🟢" if against_acc >= 55 else "🔴"
            lines.append(f"  {icon} Наш пик ≠ sharp money: <b>{against_acc:.1f}%</b> ({against_won}/{against})")

    lines.append("")
    lines.append(sep)
    lines.append("<i>Бот учитывает эту статистику при следующих прогнозах.</i>")
    return "\n".join(lines)



def format_bankroll(bankroll_stats: Optional[dict]) -> str:
    """Format /bankroll reply with performance and bankroll breakdowns."""
    sep = "─" * 32
    lines = ["💼 <b>PAPER BANKROLL</b>", sep, ""]

    def _append_segment_block(title: str, segments: list[dict], limit: int = 5) -> None:
        if not segments:
            return
        lines.append("")
        lines.append(f"<b>{title}</b>")
        for segment in segments[:limit]:
            label = _esc(segment.get("segment", "Unknown"))
            tracked = int(segment.get("tracked_bets", 0) or 0)
            profit = float(segment.get("profit_units", 0.0) or 0.0)
            roi = segment.get("roi_pct")
            roi_text = f" ({float(roi):+.1f}% ROI)" if roi is not None else ""
            lines.append(f"• {label}: <b>{profit:+.2f}u</b>{roi_text}, {tracked} bets")

    if not bankroll_stats:
        lines.append("<i>Пока нет рассчитанных ставок для paper bankroll.</i>")
        lines.append("")
        lines.append("<i>Сначала нужны прогнозы с размером ставки и известным результатом.</i>")
        return "\n".join(lines)

    lines.append(
        f"Банк: <b>{float(bankroll_stats['starting_bankroll_units']):.2f}u → "
        f"{float(bankroll_stats['current_bankroll_units']):.2f}u</b>"
    )
    lines.append(
        f"Прибыль: <b>{float(bankroll_stats['profit_units']):+.2f}u</b> | "
        f"ROI: <b>{float(bankroll_stats['roi_pct']):+.2f}%</b>"
    )
    lines.append(
        f"Ставок: <b>{int(bankroll_stats['tracked_bets'])}</b> | "
        f"W {int(bankroll_stats['wins'])} / L {int(bankroll_stats['losses'])} / P {int(bankroll_stats['pushes'])}"
    )
    if bankroll_stats.get("hit_rate") is not None:
        lines.append(
            f"Hit rate: <b>{float(bankroll_stats['hit_rate']):.1f}%</b> | "
            f"средняя ставка <b>{float(bankroll_stats['avg_stake_units']):.2f}u</b>"
        )
    lines.append(f"Макс. просадка: <b>{float(bankroll_stats['max_drawdown_units']):.2f}u</b>")

    recent_form = bankroll_stats.get("recent_form_20")
    if recent_form:
        lines.append("")
        lines.append(f"<b>Recent form (last {int(recent_form.get('window_size', 20) or 20)} bets)</b>")
        lines.append(
            f"Прибыль: <b>{float(recent_form.get('profit_units', 0.0) or 0.0):+.2f}u</b> | "
            f"ROI: <b>{float(recent_form.get('roi_pct', 0.0) or 0.0):+.2f}%</b>"
        )
        lines.append(
            f"W {int(recent_form.get('wins', 0) or 0)} / "
            f"L {int(recent_form.get('losses', 0) or 0)} / "
            f"P {int(recent_form.get('pushes', 0) or 0)}"
        )
        if recent_form.get("streak_label"):
            lines.append(f"Current streak: <b>{_esc(recent_form.get('streak_label'))}</b>")

    _append_segment_block_with_sample_gate(lines, "Best by market", bankroll_stats.get("by_market") or [], limit=5)
    _append_segment_block_with_sample_gate(lines, "Best by competition", bankroll_stats.get("by_competition") or [], limit=5)
    _append_segment_block_with_sample_gate(lines, "Best by competition + policy", bankroll_stats.get("by_competition_policy") or [], limit=5)
    _append_segment_block_with_sample_gate(lines, "Best by risk", bankroll_stats.get("by_risk") or [], limit=5)
    _append_segment_block_with_sample_gate(lines, "Best by bankroll mode", bankroll_stats.get("by_bankroll_mode") or [], limit=5)
    _append_segment_block_with_sample_gate(lines, "Best by recommendation mode", bankroll_stats.get("by_recommendation_mode") or [], limit=5)
    _append_segment_block_with_sample_gate(lines, "Best by policy flag", bankroll_stats.get("by_policy_flag") or [], limit=5)

    lines.append("")
    lines.append(sep)
    lines.append("<i>/stats — точность и калибровка прогнозов.</i>")
    return "\n".join(lines)


def format_error(team1: str, team2: str, error: str) -> str:
    return (
        "❌ <b>Ошибка анализа</b>\n\n"
        f"Матч: {_esc(team1)} vs {_esc(team2)}\n"
        f"Причина: {_esc(error)}\n\n"
        "<i>Попробуйте ещё раз или проверьте написание команд.</i>"
    )


def format_result_notification(resolved: dict) -> str:
    """Format a push notification for an auto-resolved match result."""
    team1 = str(resolved.get("team1", "?"))
    team2 = str(resolved.get("team2", "?"))
    score = str(resolved.get("score", "?-?"))
    top_rec = str(resolved.get("top_rec") or "—")
    actual_result = str(resolved.get("actual_result", ""))
    acc_line = str(resolved.get("acc_line", ""))

    evaluation = _evaluate_top_rec(top_rec, team1, team2, actual_result, score)
    if evaluation == "correct":
        outcome_icon = "✅"
        outcome_word = "ВЕРНО!"
    elif evaluation == "wrong":
        outcome_icon = "❌"
        outcome_word = "Неверно"
    elif evaluation == "push":
        outcome_icon = "◻"
        outcome_word = "Возврат"
    else:
        outcome_icon = "◻"
        outcome_word = "Без автооценки"

    lines = [
        "📬 <b>Результат матча!</b>",
        "",
        f"⚽ <b>{_esc(team1)} {_esc(score)} {_esc(team2)}</b>",
        f"🏆 Ваша ставка: <b>{_esc(top_rec)}</b> — {outcome_icon} {outcome_word}",
    ]
    if acc_line:
        lines += ["", f"📊 {_esc(acc_line)}"]
    lines += ["", "<i>/stats — полная статистика</i>"]
    return "\n".join(lines)


def format_schedule(leagues: list) -> str:
    """Format /schedule reply — today's matches grouped by league."""
    from datetime import date

    today_str = date.today().strftime("%d.%m.%Y")
    sep = "─" * 32
    lines = [f"⚽ <b>МАТЧИ СЕГОДНЯ — {today_str}</b>", sep, ""]

    if not leagues:
        lines.append("<i>Нет матчей в топ-лигах сегодня.</i>")
        return "\n".join(lines)

    for league in leagues:
        lines.append(f"{league.country_flag} <b>{_esc(league.league_name)}</b>")
        for match in league.matches:
            lines.append(f"  {_esc(match.time_utc)}  {_esc(match.home)} vs {_esc(match.away)}")
            lines.append(f"  <code>/predict {_esc(match.home)} vs {_esc(match.away)}</code>")
        lines.append("")

    lines += [sep, "<i>Время UTC. /predict — прогноз матча.</i>"]
    return _truncate_telegram("\n".join(lines))


def format_scraper_metrics(snapshot: dict[str, dict[str, object]]) -> str:
    """Format runtime scraper metrics for /debugsources."""
    lines = [
        "🧪 <b>Диагностика источников</b>",
        "",
        f"Активных источников в registry: <b>{len(SCRAPER_REGISTRY)}</b>",
    ]

    if not snapshot:
        lines += [
            "",
            "<i>Пока нет runtime-метрик. Запустите хотя бы один /predict.</i>",
        ]
        return "\n".join(lines)

    for spec in SCRAPER_REGISTRY:
        metric = snapshot.get(spec.key, {})
        runs = int(metric.get("runs", 0) or 0)
        successes = int(metric.get("successes", 0) or 0)
        failures = int(metric.get("failures", 0) or 0)
        avg_duration_ms = metric.get("avg_duration_ms", 0.0) or 0.0
        max_duration_ms = int(metric.get("max_duration_ms", 0) or 0)
        last_duration_ms = int(metric.get("last_duration_ms", 0) or 0)
        if runs:
            success_rate = round(successes / runs * 100)
            summary = (
                f"{runs} runs, ok {successes}, fail {failures}, "
                f"success {success_rate}%, avg {avg_duration_ms}ms, "
                f"last {last_duration_ms}ms, max {max_duration_ms}ms"
            )
        else:
            summary = "no runs yet"

        lines += [
            "",
            f"{_esc(spec.progress_label)} <b>{_esc(spec.provider)}</b>",
            _esc(summary),
        ]

        last_error = metric.get("last_error_message")
        last_exception_type = metric.get("last_exception_type")
        if last_error:
            suffix = f" ({last_exception_type})" if last_exception_type else ""
            lines.append(f"Last error{_esc(suffix)}: {_esc(last_error)}")

    return _truncate_telegram("\n".join(lines))


def format_health(summary: dict[str, object]) -> str:
    """Format a compact operational health summary for the bot."""
    def _humanize_policy_flag(flag: str) -> str:
        mapping = {
            "path_pre_ai_gate": "pre-AI gate",
            "path_agent_chain": "agent chain",
            "path_single_call": "single-call",
            "path_fallback_numeric": "numeric fallback",
            "no_bet": "no bet",
            "action_mode_pick": "action mode",
            "top_pick_normalized_core": "normalized to core",
            "top_market_core": "top core",
            "top_market_derived": "top derived",
            "top_family_positive_trust": "positive trust",
            "top_family_negative_trust": "negative trust",
            "btts_borderline_rerank": "BTTS rerank",
            "pre_ai_gate_playable_seed": "playable seed",
        }
        normalized = str(flag or "").strip().lower()
        return mapping.get(normalized, normalized.replace("_", " "))

    backend = str(summary.get("backend", "unknown"))
    agent_chain = "on" if summary.get("agent_chain_enabled") else "off"
    source_count = int(summary.get("source_count", 0) or 0)
    prediction_count = int(summary.get("prediction_count", 0) or 0)
    unresolved_count = int(summary.get("unresolved_count", 0) or 0)
    active_lessons = int(summary.get("active_lessons_count", 0) or 0)
    unsynced_lessons = int(summary.get("unsynced_lessons_count", 0) or 0)
    metrics = summary.get("scrape_metrics", {}) or {}
    analysis_metrics = summary.get("analysis_metrics", {}) or {}
    cache_stats = summary.get("cache_stats", {}) or {}
    vector_stats = summary.get("vector_stats") or {}

    total_runs = sum(int(item.get("runs", 0) or 0) for item in metrics.values())
    total_failures = sum(int(item.get("failures", 0) or 0) for item in metrics.values())
    sources_with_runs = sum(1 for item in metrics.values() if int(item.get("runs", 0) or 0) > 0)
    last_errors = [
        f"{spec.label}: {metric.get('last_error_message')}"
        for spec in SCRAPER_REGISTRY
        if (metric := metrics.get(spec.key)) and metric.get("last_error_message")
    ]

    lines = [
        "🩺 <b>Health</b>",
        "",
        f"Backend: <b>{_esc(backend)}</b> | agent chain: <b>{agent_chain}</b>",
        f"Sources: <b>{source_count}</b> configured, <b>{sources_with_runs}</b> seen in runtime",
        f"Predictions: <b>{prediction_count}</b> total, unresolved <b>{unresolved_count}</b>",
        f"Lessons: <b>{active_lessons}</b> active, unsynced <b>{unsynced_lessons}</b>",
    ]

    if cache_stats:
        lines.append(
            "Cache: "
            f"<b>{int(cache_stats.get('entries', 0) or 0)}</b> entries, "
            f"inflight <b>{int(cache_stats.get('inflight', 0) or 0)}</b>, "
            f"ttl {int(cache_stats.get('ttl_seconds', 0) or 0)}s"
        )

    if vector_stats:
        lines.append(
            "Vector memory: "
            f"matches <b>{int(vector_stats.get('matches', 0) or 0)}</b>, "
            f"lessons <b>{int(vector_stats.get('lessons', 0) or 0)}</b>, "
            f"patterns <b>{int(vector_stats.get('team_patterns', 0) or 0)}</b>"
        )
    else:
        lines.append("Vector memory: <i>unavailable</i>")

    lines.append(
        "Scrapers: "
        f"runs <b>{total_runs}</b>, failures <b>{total_failures}</b>"
    )
    if analysis_metrics:
        lines.append(
            "Analysis: "
            f"calls <b>{int(analysis_metrics.get('total_calls', 0) or 0)}</b>, "
            f"pre-AI skips <b>{int(analysis_metrics.get('pre_ai_gate_skips', 0) or 0)}</b>, "
            f"1X2 hybrid <b>{int(analysis_metrics.get('live_hybrid_1x2_applied', 0) or 0)}</b>, "
            f"chain <b>{int(analysis_metrics.get('agent_chain_runs', 0) or 0)}</b>, "
            f"single-call <b>{int(analysis_metrics.get('single_call_runs', 0) or 0)}</b>, "
            f"fallbacks <b>{int(analysis_metrics.get('fallback_reports', 0) or 0)}</b>"
        )
        lines.append(
            "AI requests: "
            f"Anthropic <b>{int(analysis_metrics.get('anthropic_requests', 0) or 0)}</b>, "
            f"OpenClaw <b>{int(analysis_metrics.get('openclaw_requests', 0) or 0)}</b>, "
            f"OpenClaw fallback <b>{int(analysis_metrics.get('openclaw_fallbacks', 0) or 0)}</b>"
        )
        policy_flag_counts = analysis_metrics.get("policy_flag_counts", {}) or {}
        if policy_flag_counts:
            top_flags = sorted(
                (
                    (str(flag), int(count or 0))
                    for flag, count in policy_flag_counts.items()
                    if int(count or 0) > 0
                ),
                key=lambda item: (item[1], item[0]),
                reverse=True,
            )[:4]
            if top_flags:
                rendered = ", ".join(
                    f"{_humanize_policy_flag(flag)} <b>{count}</b>"
                    for flag, count in top_flags
                )
                lines.append(f"Policy flags: {rendered}")

    # Scraper health alerts — flag sources with < 50% success rate
    degraded = []
    for key, metric in metrics.items():
        runs = int(metric.get("runs", 0) or 0)
        successes = int(metric.get("successes", 0) or 0)
        if runs >= 3 and successes / runs < 0.5:
            label = metric.get("label", key)
            rate = int(successes / runs * 100)
            degraded.append(f"{label}: {rate}% ({successes}/{runs})")
    if degraded:
        lines.append("")
        lines.append("⚠️ <b>Degraded sources (< 50% success):</b>")
        for item in degraded:
            lines.append(f"  ⚠ {_esc(item)}")

    # Domain cooldowns (rate-limit protection)
    cooldowns = summary.get("domain_cooldowns") or {}
    if cooldowns:
        lines.append("")
        lines.append("🛑 <b>Rate-limit cooldowns:</b>")
        for domain, remaining in cooldowns.items():
            lines.append(f"  • {_esc(domain)}: {int(remaining)}s remaining")

    if last_errors:
        lines.append("")
        lines.append("<b>Last source errors:</b>")
        for item in last_errors[:4]:
            lines.append(f"  • {_esc(item)}")

    if summary.get("debug_snapshots_enabled"):
        lines.append("")
        lines.append(
            f"Debug snapshots: <b>on</b> ({_esc(summary.get('debug_snapshots_dir', ''))})"
        )
    else:
        lines.append("")
        lines.append("Debug snapshots: <b>off</b>")

    return _truncate_telegram("\n".join(lines))


def format_history(
    rows: list[dict],
    *,
    title: str = "📋 <b>История прогнозов</b>",
    empty_message: str = "<i>История пуста. Сделайте первый прогноз командой /predict</i>",
    footer: str = "<i>Последние 10 прогнозов. /stats — точность статистики.</i>",
) -> str:
    """Format prediction history for either global or match-specific queries."""
    sep = "─" * 32
    lines = [title, sep, ""]

    if not rows:
        lines.append(empty_message)
        return "\n".join(lines)

    for i, row in enumerate(rows, 1):
        team1 = str(row.get("team1", "?"))
        team2 = str(row.get("team2", "?"))
        date_str = str(row.get("predicted_at") or "")[:10]
        top_rec = str(row.get("top_rec") or "—")
        top_confidence_pct = row.get("top_confidence_pct")
        risk_level = row.get("risk_level")
        stake_units = row.get("stake_units")
        stake_label = row.get("stake_label")
        methodology_version = row.get("methodology_version")
        prompt_version = row.get("prompt_version")
        actual_result = row.get("actual_result")
        actual_score = row.get("actual_score")
        completeness = row.get("completeness")

        if actual_result and actual_score:
            result_map = {
                "W1": f"✅ {_esc(team1)} победил",
                "W2": f"✅ {_esc(team2)} победил",
                "DRAW": "✅ Ничья",
            }
            result_str = result_map.get(actual_result, _esc(actual_result))
            result_line = f"  📉 Итог: <b>{_esc(actual_score)}</b> — {result_str}"
        else:
            result_line = "  📉 Итог: <i>ожидается</i>"

        if completeness is not None:
            comp_pct = int(completeness * 100) if completeness <= 1.0 else int(completeness)
            comp_icon = "🟢" if comp_pct >= 75 else "🟡" if comp_pct >= 50 else "🔴"
        else:
            comp_icon = "◻"
            comp_pct = 0

        meta_parts: list[str] = []
        if top_confidence_pct is not None:
            meta_parts.append(f"confidence {float(top_confidence_pct):.0f}%")
        if risk_level:
            meta_parts.append(f"risk {_esc(risk_level)}")
        if stake_units is not None:
            stake_text = f"stake {float(stake_units):.2f}u"
            if stake_label:
                stake_text += f" {_esc(str(stake_label))}"
            meta_parts.append(stake_text)
        if methodology_version:
            meta_parts.append(f"method {_esc(str(methodology_version))}")
        if prompt_version:
            meta_parts.append(f"prompt {_esc(str(prompt_version))}")

        lines += [
            f"<b>{i}. {_esc(team1)} vs {_esc(team2)}</b>  <i>{_esc(date_str)}</i>",
            f"  🏆 Ставка: <b>{_esc(top_rec)}</b>",
            *( [f"  ℹ️ {' | '.join(meta_parts)}"] if meta_parts else [] ),
            result_line,
            f"  {comp_icon} Данных: {comp_pct}%",
            "",
        ]

    lines.append(sep)
    lines.append(footer)
    return _truncate_telegram("\n".join(lines))



def format_report(report: PredictionReport) -> str:
    """Return a clean Telegram HTML-formatted prediction message."""
    t1 = _esc(report.team1)
    t2 = _esc(report.team2)
    sep = "─" * 32

    lines: list[str] = [
        "⚽ <b>ПРОГНОЗ МАТЧА</b> ⚽",
        "",
        f"🏟 <b>{t1.upper()} vs {t2.upper()}</b>",
        sep,
        "",
        "📊 <b>АНАЛИЗ</b>",
        _esc(report.analysis_summary),
        "",
        "🔍 <b>КЛЮЧЕВЫЕ ФАКТОРЫ</b>",
        f"<b>{t1}</b>",
    ]

    for strength in report.team1_strengths[:3]:
        lines.append(f"  ✅ {_esc(strength)}")
    for weakness in report.team1_weaknesses[:2]:
        lines.append(f"  ❌ {_esc(weakness)}")

    lines.append(f"<b>{t2}</b>")
    for strength in report.team2_strengths[:3]:
        lines.append(f"  ✅ {_esc(strength)}")
    for weakness in report.team2_weaknesses[:2]:
        lines.append(f"  ❌ {_esc(weakness)}")

    lines += [
        "",
        sep,
        "📈 <b>ПРОГНОЗЫ ПО РЫНКАМ</b>",
        "",
        "🎯 <b>Исход матча (1X2)</b>",
        f"  {t1}: {_bar(report.prob_w1)}",
        f"  Ничья: {_bar(report.prob_draw)}",
        f"  {t2}: {_bar(report.prob_w2)}",
        f"  Уверенность: {_badge(report.result_confidence)}",
        "",
        "⚽ <b>Тотал голов</b>",
        f"  Больше 2.5: {_bar(report.prob_over_2_5)}",
        f"  Меньше 2.5: {_bar(report.prob_under_2_5)}",
        f"  xG (прогноз): <b>{report.expected_goals:.1f} гола</b>",
        f"  Уверенность: {_badge(report.goals_confidence)}",
        "",
        "🏑 <b>Азиатский гандикап</b>",
    ]

    if report.market_ah_line:
        odds_str = f" @ <b>{report.market_ah_odds:.2f}</b>" if report.market_ah_odds else ""
        lines.append(f"  Линия рынка: <b>{_esc(report.market_ah_line)}</b>{odds_str}")
    lines += [
        f"  Рекомендация: <b>{_esc(report.asian_handicap_rec)}</b>",
        f"  Уверенность: {_badge(report.asian_handicap_confidence)}",
        "",
        "🔵🔴 <b>Обе забьют</b>",
        f"  Да:  {_bar(report.prob_btts_yes)}",
        f"  Нет: {_bar(report.prob_btts_no)}",
        f"  Уверенность: {_badge(report.btts_confidence)}",
        "",
        sep,
        "🏆 <b>ТОП СТАВКА</b>",
        "",
        f"  📌 <b>{_esc(report.top_recommendation)}</b>",
    ]

    if report.top_recommendation_odds:
        lines.append(f"  💰 Коэф: <b>{report.top_recommendation_odds:.2f}</b>")
    lines.append(f"  🎯 Уверенность: <b>{report.top_confidence_pct:.0f}%</b>")
    lines.append(f"  {_RISK_EMOJI.get(report.risk_level, '◻')} Риск: <b>{_esc(report.risk_level)}</b>")
    if report.stake_units is not None:
        stake_line = f"  💸 Stake: <b>{report.stake_units:.2f}u</b>"
        if report.stake_label:
            stake_line += f" <i>({_esc(report.stake_label)})</i>"
        lines.append(stake_line)
    if report.kelly_pct and report.kelly_pct > 0.1:
        lines.append(f"  📊 Kelly: <b>{report.kelly_pct:.1f}%</b> банкролла")
    if report.stake_rationale:
        lines.append(f"  🧮 {_esc(report.stake_rationale)}")
    if report.top_pick_context:
        lines.append(f"  🧠 {_esc(report.top_pick_context)}")
    lines.append("")

    extra_market_lines: list[str] = []
    if report.prob_over_1_5 is not None and report.prob_under_1_5 is not None:
        extra_market_lines.append(
            f"  O/U 1.5: {_bar(report.prob_over_1_5)} / {_bar(report.prob_under_1_5)}"
        )
    if report.prob_over_3_5 is not None and report.prob_under_3_5 is not None:
        extra_market_lines.append(
            f"  O/U 3.5: {_bar(report.prob_over_3_5)} / {_bar(report.prob_under_3_5)}"
        )
    if (
        report.prob_double_chance_1x is not None
        and report.prob_double_chance_12 is not None
        and report.prob_double_chance_x2 is not None
    ):
        extra_market_lines.append(
            f"  1X / 12 / X2: {_bar(report.prob_double_chance_1x)} / {_bar(report.prob_double_chance_12)} / {_bar(report.prob_double_chance_x2)}"
        )
    if report.prob_dnb_team1 is not None and report.prob_dnb_team2 is not None:
        extra_market_lines.append(
            f"  DNB {t1} / {t2}: {_bar(report.prob_dnb_team1)} / {_bar(report.prob_dnb_team2)}"
        )
    if extra_market_lines:
        lines += [sep, "📦 <b>ДОП. РЫНКИ</b>", "", *extra_market_lines, ""]

    if report.value_bets:
        lines += [sep, "💎 <b>ЦЕННЫЕ СТАВКИ</b>", ""]
        for value_bet in report.value_bets:
            lines.append(f"  ✅ {_esc(value_bet)}")
        lines.append("")

    # Odds movement signals
    feat = getattr(report, "feature_snapshot", None)
    if feat:
        move_w1 = getattr(feat, "movement_score_w1", None)
        move_over = getattr(feat, "movement_score_over", None)
        if move_w1 is not None or move_over is not None:
            lines += [sep, "📉 <b>ДВИЖЕНИЕ ЛИНИЙ</b>", ""]
            if move_w1 is not None and move_w1 != 0:
                arrow = "⬇️" if move_w1 > 0 else "⬆️"
                strength = abs(move_w1)
                label = "сильное" if strength >= 3 else ("умеренное" if strength >= 2 else "слабое")
                side = t1 if move_w1 > 0 else t2
                lines.append(f"  {arrow} 1X2: коэфф. на {side} падает ({label}, {move_w1:+d})")
            elif move_w1 is not None:
                lines.append("  ➡️ 1X2: линия стабильна")
            if move_over is not None and move_over != 0:
                arrow = "⬇️" if move_over > 0 else "⬆️"
                strength = abs(move_over)
                label = "сильное" if strength >= 3 else ("умеренное" if strength >= 2 else "слабое")
                direction = "Over" if move_over > 0 else "Under"
                lines.append(f"  {arrow} Тотал: деньги идут на {direction} ({label}, {move_over:+d})")
            elif move_over is not None:
                lines.append("  ➡️ Тотал: линия стабильна")
            fav_gap = getattr(feat, "favourite_gap_pct", None)
            if fav_gap is not None and fav_gap > 0:
                lines.append(f"  📊 Перевес фаворита: <b>{fav_gap:.1f}%</b>")
            lines.append("")

    # CLV snapshot if Pinnacle odds available
    pin = getattr(report, "pinnacle_odds_snapshot", None)
    if pin and (pin.pin_w1 is not None or pin.pin_over_2_5 is not None):
        lines += [sep, "📌 <b>PINNACLE ODDS (CLV)</b>", ""]
        if pin.pin_w1 is not None and pin.pin_draw is not None and pin.pin_w2 is not None:
            lines.append(
                f"  1X2: <b>{pin.pin_w1:.2f}</b> / "
                f"<b>{pin.pin_draw:.2f}</b> / "
                f"<b>{pin.pin_w2:.2f}</b>"
            )
        if pin.pin_over_2_5 is not None and pin.pin_under_2_5 is not None:
            lines.append(
                f"  O/U 2.5: <b>{pin.pin_over_2_5:.2f}</b> / "
                f"<b>{pin.pin_under_2_5:.2f}</b>"
            )
        lines.append("  <i>CLV рассчитается после закрытия линий</i>")
        lines.append("")

    if report.data_warnings:
        lines.append("⚠️ <b>Примечания</b>")
        for warning in report.data_warnings:
            lines.append(f"  • {_esc(warning)}")
        lines.append("")

    lines += [sep, "<i>Прогнозы носят информационный характер.</i>"]
    return "\n".join(lines)


def format_clv(stats: dict, entries: list[dict]) -> str:
    """Format /clv reply with CLV statistics and recent entries."""
    sep = "\u2500" * 32
    lines = ["\U0001f4cc <b>CLV TRACKING (Pinnacle)</b>", sep, ""]

    total = int(stats.get("total", 0) or 0)
    resolved = int(stats.get("resolved", 0) or 0)

    if total == 0:
        lines.append("<i>\u041f\u043e\u043a\u0430 \u043d\u0435\u0442 \u0434\u0430\u043d\u043d\u044b\u0445 \u043f\u043e CLV.</i>")
        lines.append("")
        lines.append("<i>CLV \u0442\u0440\u0435\u043a\u0438\u043d\u0433 \u043d\u0430\u0447\u043d\u0451\u0442\u0441\u044f \u0430\u0432\u0442\u043e\u043c\u0430\u0442\u0438\u0447\u0435\u0441\u043a\u0438,")
        lines.append("\u043a\u043e\u0433\u0434\u0430 Pinnacle odds \u0431\u0443\u0434\u0443\u0442 \u0434\u043e\u0441\u0442\u0443\u043f\u043d\u044b.</i>")
        return "\n".join(lines)

    avg_clv_ou = stats.get("avg_clv_ou")
    avg_clv_1x2 = stats.get("avg_clv_1x2")
    avg_clv_top = stats.get("avg_clv_top")
    positive = int(stats.get("positive_clv_count", 0) or 0)
    negative = int(stats.get("negative_clv_count", 0) or 0)

    lines.append(f"\u0412\u0441\u0435\u0433\u043e: <b>{total}</b> \u043f\u0440\u043e\u0433\u043d\u043e\u0437\u043e\u0432 | Resolved: <b>{resolved}</b>")
    lines.append("")

    if resolved > 0:
        lines.append("<b>\u0421\u0440\u0435\u0434\u043d\u0438\u0439 CLV:</b>")
        if avg_clv_ou is not None:
            emoji = "\U0001f7e2" if avg_clv_ou > 0 else "\U0001f534"
            lines.append(f"  {emoji} O/U: <b>{avg_clv_ou:+.2f}%</b>")
        if avg_clv_1x2 is not None:
            emoji = "\U0001f7e2" if avg_clv_1x2 > 0 else "\U0001f534"
            lines.append(f"  {emoji} 1X2: <b>{avg_clv_1x2:+.2f}%</b>")
        if avg_clv_top is not None:
            emoji = "\U0001f7e2" if avg_clv_top > 0 else "\U0001f534"
            lines.append(f"  {emoji} Top pick: <b>{avg_clv_top:+.2f}%</b>")
        lines.append("")

        if positive + negative > 0:
            pct = positive / (positive + negative) * 100
            lines.append(
                f"Beat closing: <b>{positive}/{positive + negative}</b> "
                f"({pct:.0f}%)"
            )
            lines.append("")

    if entries:
        lines.append("<b>\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u0438\u0435:</b>")
        for e in entries[:8]:
            t1 = _esc(e.get("team1", ""))
            t2 = _esc(e.get("team2", ""))
            market = _esc(e.get("top_market", "?"))
            clv = e.get("clv_top_pick_pct")
            if clv is not None:
                emoji = "\U0001f7e2" if clv > 0 else "\U0001f534"
                lines.append(f"  {emoji} {t1} vs {t2}: <b>{clv:+.1f}%</b> ({market})")
            else:
                lines.append(f"  \u23f3 {t1} vs {t2} ({market})")

    lines.append("")
    lines.append("<i>CLV &gt; 0 = \u0432\u044b \u043f\u043e\u0431\u0438\u043b\u0438 \u0437\u0430\u043a\u0440\u044b\u0432\u0430\u044e\u0449\u0443\u044e \u043b\u0438\u043d\u0438\u044e Pinnacle</i>")
    lines.append("<i>\u0421\u0442\u0430\u0431\u0438\u043b\u044c\u043d\u044b\u0439 +CLV = \u0434\u043e\u043b\u0433\u043e\u0441\u0440\u043e\u0447\u043d\u0430\u044f \u043f\u0440\u0438\u0431\u044b\u043b\u044c</i>")
    return _truncate_telegram("\n".join(lines))


def format_daily_pnl(summary: Optional[dict]) -> str:
    """Format daily P&L summary notification."""
    sep = "─" * 32
    lines = ["📊 <b>ИТОГИ ДНЯ</b>", sep, ""]

    if not summary or (summary.get("settled_count", 0) == 0 and summary.get("placed_count", 0) == 0):
        lines.append("<i>За сегодня нет активности по ставкам.</i>")
        return "\n".join(lines)

    day = summary.get("day", "?")
    lines.append(f"📅 <b>{_esc(day)}</b>")
    lines.append("")

    placed = summary.get("placed_count", 0)
    settled = summary.get("settled_count", 0)
    wins = summary.get("wins", 0)
    losses = summary.get("losses", 0)
    pushes = summary.get("pushes", 0)
    profit = float(summary.get("profit_units", 0.0))
    roi = float(summary.get("roi_pct", 0.0))

    lines.append(f"Новых ставок: <b>{placed}</b>")
    lines.append(f"Рассчитано: <b>{settled}</b>")

    if settled > 0:
        lines.append(f"W {wins} / L {losses} / P {pushes}")
        emoji = "🟢" if profit >= 0 else "🔴"
        lines.append(f"{emoji} P&amp;L: <b>{profit:+.2f}u</b> (ROI: {roi:+.1f}%)")

    details = summary.get("details") or []
    if details:
        lines.append("")
        lines.append("<b>Матчи:</b>")
        for d in details[:8]:
            t1 = _esc(d.get("team1", ""))
            t2 = _esc(d.get("team2", ""))
            outcome = d.get("outcome", "?")
            p = float(d.get("profit", 0))
            score = _esc(d.get("score", ""))
            icon = "✅" if outcome == "WIN" else "❌" if outcome == "LOSS" else "➖"
            lines.append(f"  {icon} {t1} vs {t2} {score}: <b>{p:+.2f}u</b>")

    lines.append("")
    lines.append(sep)
    lines.append("<i>/bankroll — полная статистика</i>")
    return _truncate_telegram("\n".join(lines))
