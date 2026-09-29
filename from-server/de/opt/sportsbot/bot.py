"""
Telegram bot entry point.

Flow for /predict command:
  1. Parse team names (fuzzy match + aliases)
  2. Per-user rate limit (1 req / 30 sec)
  3. TTL cache lookup — cache hit → instant reply
  4. Send "loading" message
  5. scrape_all_parallel() — all registered sources with live progress
  6. Edit message to "analysing..."
  7. analyze_match() → AI backend
  8. Cache result, save to DB, edit message with final report

Self-learning commands:
  /result <team1> <score> <team2>  — e.g. /result Arsenal 2-1 Chelsea
  /stats                           — show historical accuracy stats

Background job every 3h: auto-fetch results from Fotmob for unresolved predictions.

Uses python-telegram-bot v22.5 Application pattern.
Loading message is EDITED with the result — one clean message per prediction.
"""
from __future__ import annotations

import html
import logging

from telegram import Update, constants
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)

import config
import database as db
from cache import match_cache, TTLCache
from memory import close_shared_vector_memory
from models import SourceStatus
from scrapers import get_scrape_metrics_snapshot, scrape_all_parallel
from scrapers.registry import (
    SCRAPER_REGISTRY,
    get_loading_source_lines,
    get_scraper_progress_labels,
)
from analyzer import analyze_match, get_analysis_runtime_metrics_snapshot
from formatter import (
    format_report, format_loading, format_analysing,
    format_error, format_stats, format_history,
    format_result_notification, format_health,
    format_scraper_metrics, format_bankroll,
    format_clv, format_daily_pnl,
    split_telegram_message,
)
from prediction_resolution import (
    process_prediction_result,
    store_prediction_memory,
    sync_prediction_to_historical,
)
from team_parser import parse_teams_from_input, suggest_teams
from result_fetcher import check_pending_results

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

HTML = constants.ParseMode.HTML


def _build_registry_block() -> str:
    return "\n".join(html.escape(line) for line in get_loading_source_lines())


def _build_start_text() -> str:
    return (
        "👋 <b>Football Analyst Bot</b>\n\n"
        f"Я собираю данные из <b>{len(SCRAPER_REGISTRY)}</b> активных источников "
        "и использую AI для анализа матча.\n\n"
        "<b>Сейчас подключено:</b>\n"
        f"{_build_registry_block()}\n\n"
        "<b>Команды:</b>\n"
        "  /predict &lt;команда1&gt; vs &lt;команда2&gt;\n"
        "  /result &lt;команда1&gt; &lt;счёт&gt; &lt;команда2&gt;\n"
        "  /stats — статистика точности\n"
        "  /bankroll — paper bankroll и ROI\n"
        "  /clv — CLV трекинг (Pinnacle)\n"
        "  /history — история прогнозов\n"
        "  /historymatch — история конкретной пары\n"
        "  /schedule — матчи сегодня\n"
        "  /predict_day — пакетный прогноз на день\n"
        "  /lessons — уроки из прошлых ошибок\n"
        "  /health — краткий health summary\n"
        "  /debugsources — runtime-диагностика источников\n"
        "  /help — подробная справка\n\n"
        "<b>Примеры:</b>\n"
        "  <code>/predict Arsenal vs Chelsea</code>\n"
        "  <code>/result Arsenal 2-1 Chelsea</code>\n"
        "  <code>/predict Реал Мадрид vs Барселона</code>"
    )


def _build_help_text() -> str:
    return (
        "📖 <b>Справка</b>\n\n"
        "<b>/predict</b> — прогноз матча\n"
        "  Пример: <code>/predict Bayern Munich vs Borussia Dortmund</code>\n\n"
        "<b>/result</b> — ввести реальный счёт матча\n"
        "  Пример: <code>/result Bayern Munich 3-1 Borussia Dortmund</code>\n"
        "  Бот найдёт последний незавершённый прогноз на этот матч и обновит статистику.\n\n"
        "<b>/stats</b> — точность прогнозов бота\n"
        "  Показывает Brier score и % верных исходов по сохранённым результатам.\n\n"
        "<b>/bankroll</b> — paper bankroll и performance\n"
        "  Показывает ROI, просадку, recent form и лучшие сегменты.\n\n"
        "<b>/history</b> — последние 10 прогнозов\n"
        "  Показывает матч, ставку, реальный результат и полноту данных.\n\n"
        "<b>/historymatch</b> — история конкретной пары команд\n"
        "  Пример: <code>/historymatch Arsenal vs Chelsea</code>\n"
        "  Или с лимитом: <code>/historymatch 5 Arsenal vs Chelsea</code>\n\n"
        "<b>/schedule</b> — матчи на сегодня\n"
        "  Топ-лиги и быстрые команды для /predict.\n\n"
        "<b>/predict_day</b> — пакетный прогноз на все матчи дня\n"
        "  Можно фильтровать по лиге: <code>/predict_day Premier League</code>\n\n"
        "<b>/lessons</b> — уроки из прошлых ошибок\n"
        "  Бот автоматически извлекает lessons после resolved матчей.\n\n"
        "<b>/health</b> — operational summary\n"
        "  База, cache, источники, lessons и vector memory в одном сообщении.\n\n"
        "<b>/clv</b> — CLV трекинг (Closing Line Value)\n"
        "  Сравнивает ваши odds с закрывающей линией Pinnacle.\n"
        "  Стабильный +CLV = долгосрочная прибыль.\n\n"
        "<b>/debugsources</b> — runtime-метрики источников\n"
        "  Показывает runs/failures/latency и последний error по каждому scraper.\n\n"
        f"<b>Активные источники ({len(SCRAPER_REGISTRY)}):</b>\n"
        f"{_build_registry_block()}\n\n"
        "<b>Формат прогноза:</b>\n"
        "  🎯 Исход (1X2) с вероятностями\n"
        "  ⚽ Тотал голов и xG\n"
        "  🏑 Азиатский гандикап\n"
        "  🔵🔴 Обе забьют\n"
        "  🏆 Топ-ставка\n\n"
        "⏱ Время ответа обычно 15–35 секунд\n"
        "💾 Кэш: 30 минут\n"
        "🧠 Самообучение: lessons, calibration, vector memory"
    )


# ── Handlers ───────────────────────────────────────────────────────────────────

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(_build_start_text())


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_html(_build_help_text())


async def debugsources_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/debugsources — show runtime metrics and last errors for each scraper."""
    await update.message.reply_html(
        format_scraper_metrics(get_scrape_metrics_snapshot())
    )


async def health_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/health — compact operational summary for runtime, cache, DB and memory."""
    vector_stats = None
    try:
        from memory import get_shared_vector_memory

        vm = await get_shared_vector_memory()
        vector_stats = await vm.get_stats()
    except Exception as exc:
        logger.debug("health vector stats unavailable: %s", exc)

    from scrapers.base import get_domain_cooldown_status
    summary = {
        "backend": "OpenClaw" if config.USE_OPENCLAW else "Anthropic",
        "agent_chain_enabled": config.AGENT_CHAIN_ENABLED,
        "source_count": len(SCRAPER_REGISTRY),
        "prediction_count": await db.get_prediction_count(),
        "unresolved_count": await db.get_unresolved_prediction_count(),
        "active_lessons_count": await db.get_active_lessons_count(),
        "unsynced_lessons_count": await db.get_unsynced_lessons_count(),
        "scrape_metrics": get_scrape_metrics_snapshot(),
        "analysis_metrics": get_analysis_runtime_metrics_snapshot(),
        "cache_stats": await match_cache.get_stats(),
        "vector_stats": vector_stats,
        "debug_snapshots_enabled": config.SCRAPER_DEBUG_SNAPSHOTS,
        "debug_snapshots_dir": config.SCRAPER_DEBUG_SNAPSHOTS_DIR,
        "domain_cooldowns": get_domain_cooldown_status(),
    }
    await update.message.reply_html(format_health(summary))


async def predict_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    raw_args = " ".join(context.args) if context.args else ""

    # ── Validate input ────────────────────────────────────────────────────────
    if not raw_args:
        await update.message.reply_html(
            "❓ <b>Укажите команды:</b>\n"
            "<code>/predict Arsenal vs Chelsea</code>\n"
            "<i>или</i>\n"
            "<code>/predict Реал Мадрид - Барселона</code>"
        )
        return

    team1, team2 = parse_teams_from_input(raw_args)
    if not team1 or not team2:
        safe_args = html.escape(raw_args)
        suggestions = suggest_teams(raw_args)
        hint = ""
        if suggestions:
            hint = "\n\n💡 <b>Возможно, вы имели в виду:</b>\n" + "\n".join(
                f"  • {html.escape(s)}" for s in suggestions
            )
        await update.message.reply_html(
            f"❌ Не удалось распознать команды из: <code>{safe_args}</code>\n\n"
            "Используйте формат: <code>Команда1 vs Команда2</code>"
            + hint
        )
        return

    # ── Rate limiting (persistent — survives bot restarts) ────────────────────
    from datetime import datetime, timezone, timedelta
    last_ts_str = await db.get_last_request(user_id)
    if last_ts_str:
        try:
            last_dt = datetime.fromisoformat(last_ts_str)
            elapsed = (datetime.now(timezone.utc) - last_dt).total_seconds()
            wait = config.USER_RATE_LIMIT_SECONDS - elapsed
            if wait > 0:
                await update.message.reply_text(
                    f"⏳ Пожалуйста, подождите ещё {int(wait)} секунд."
                )
                return
        except ValueError:
            pass  # malformed timestamp — allow request
    await db.set_last_request(user_id)

    cache_key = TTLCache.make_key(team1, team2)
    cache_state, cache_value = await match_cache.claim(cache_key)
    if cache_state == "cached":
        cached = cache_value
        logger.info("Cache hit: %s vs %s", team1, team2)
        for part in split_telegram_message(format_report(cached)):
            await update.message.reply_html(part)
        return
    if cache_state == "wait":
        wait_msg = await update.message.reply_html(
            "⏳ <b>Такой матч уже анализируется.</b>\n"
            "<i>Отправлю готовый прогноз, как только расчёт завершится.</i>"
        )
        try:
            report = await cache_value
            report_parts = split_telegram_message(format_report(report))
            try:
                if len(report_parts) == 1:
                    await wait_msg.edit_text(report_parts[0], parse_mode=HTML)
                else:
                    await wait_msg.delete()
                    for part in report_parts:
                        await update.message.reply_html(part)
            except TelegramError:
                for part in report_parts:
                    await update.message.reply_html(part)
        except Exception as exc:
            try:
                await wait_msg.edit_text(format_error(team1, team2, str(exc)), parse_mode=HTML)
            except TelegramError:
                await update.message.reply_html(format_error(team1, team2, str(exc)))
        return

    # ── Loading message ───────────────────────────────────────────────────────
    loading_msg = await update.message.reply_html(
        format_loading(team1, team2),
    )

    async def edit(text: str, *, fallback_reply: bool = False) -> None:
        try:
            await loading_msg.edit_text(text, parse_mode=HTML)
        except TelegramError as exc:
            logger.warning("edit_text failed: %s", exc)
            if fallback_reply:
                try:
                    await update.message.reply_html(text)
                except TelegramError as reply_exc:
                    logger.warning("reply_html fallback failed: %s", reply_exc)

    try:
        # ── Scrape with live progress updates ─────────────────────────────────
        logger.info("Scraping: %s vs %s (user %d)", team1, team2, user_id)

        # Track per-source results for live display
        _source_status: dict[str, SourceStatus] = {}

        progress_order = get_scraper_progress_labels()

        async def _on_progress(status: SourceStatus) -> None:
            _source_status[status.key] = status
            safe_team1 = html.escape(team1)
            safe_team2 = html.escape(team2)
            lines = [f"⏳ <b>Сбор данных</b> — {safe_team1} vs {safe_team2}\n"]
            for source_key, progress_label in progress_order:
                source_status = _source_status.get(source_key)
                if source_status is None:
                    lines.append(f"  ⏳ {progress_label}")
                    continue
                icon = "✅" if source_status.scrape_success else "❌"
                suffix_parts: list[str] = []
                if source_status.duration_ms is not None:
                    suffix_parts.append(f"{source_status.duration_ms}ms")
                if not source_status.scrape_success and source_status.error_message:
                    error_message = html.escape(source_status.error_message)
                    if len(error_message) > 60:
                        error_message = error_message[:57] + "..."
                    suffix_parts.append(error_message)
                suffix = f" ({'; '.join(suffix_parts)})" if suffix_parts else ""
                lines.append(f"  {icon} {progress_label}{suffix}")
            await edit("\n".join(lines))

        match_data = await scrape_all_parallel(team1, team2, on_progress=_on_progress)

        completeness = int(match_data.data_completeness * 100)
        applicable_completeness = int(match_data.applicable_data_completeness * 100)
        logger.info(
            "Scrape done: raw=%d%% applicable=%d%%, failed: %s, unsupported: %s",
            completeness,
            applicable_completeness,
            match_data.failed_sources,
            match_data.unsupported_sources,
        )

        # ── Analysing message ─────────────────────────────────────────────────
        logger.info(
            "Source run summary: %s",
            ", ".join(
                (
                    f"{status.label}={'ok' if status.scrape_success else 'fail'}"
                    f"@{status.duration_ms or 0}ms"
                    + (
                        f": {status.error_message}"
                        if not status.scrape_success and status.error_message
                        else ""
                    )
                )
                for status in match_data.resolved_source_statuses
            ),
        )
        # Extract resolved team names from Fotmob data (if available)
        resolved_t1: str | None = None
        resolved_t2: str | None = None
        if match_data.flashscore and match_data.flashscore.scrape_success:
            resolved_t1 = match_data.flashscore.team1_name
            resolved_t2 = match_data.flashscore.team2_name

        await edit(
            format_analysing(
                team1,
                team2,
                completeness,
                applicable_completeness=applicable_completeness,
                unsupported_count=match_data.unsupported_source_count,
                resolved_team1=resolved_t1,
                resolved_team2=resolved_t2,
            )
        )

        # ── AI analysis ───────────────────────────────────────────────────────
        report = await analyze_match(match_data)

        await match_cache.resolve(cache_key, report)
        report_text = format_report(report)
        parts = split_telegram_message(report_text)
        if len(parts) == 1:
            await edit(parts[0], fallback_reply=True)
        else:
            # Delete loading message, send report as separate messages
            try:
                await loading_msg.delete()
            except TelegramError:
                pass
            for part in parts:
                try:
                    await update.message.reply_html(part)
                except TelegramError as part_exc:
                    logger.warning("Failed to send report part: %s", part_exc)

        # ── Persist to self-learning DB ───────────────────────────────────────
        try:
            prediction_id = await db.save_prediction(
                report,
                match_data.data_completeness,
                user_id=user_id,
            )
        except Exception as db_exc:
            logger.warning("DB save failed (non-critical): %s", db_exc)
        else:
            try:
                await sync_prediction_to_historical(prediction_id, report, match_data)
            except Exception as hist_exc:
                logger.warning("Historical sync failed (non-critical): %s", hist_exc)

            try:
                await store_prediction_memory(prediction_id, report, match_data)
            except Exception as mem_exc:
                logger.warning("store_prediction_memory failed: %s", mem_exc)

        logger.info(
            "Prediction sent: %s vs %s (top: %s, %.0f%%)",
            team1, team2, report.top_recommendation, report.top_confidence_pct
        )

    except Exception as exc:
        await match_cache.fail(cache_key, exc)
        logger.error("Prediction pipeline failed: %s", exc, exc_info=True)
        await edit(format_error(team1, team2, str(exc)), fallback_reply=True)


async def result_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /result Arsenal 2-1 Chelsea
    Finds the most recent unresolved prediction for this match and stores the result.
    """
    args = context.args or []
    if len(args) < 3:
        await update.message.reply_html(
            "❓ <b>Формат:</b> <code>/result Команда1 счёт Команда2</code>\n"
            "Пример: <code>/result Arsenal 2-1 Chelsea</code>"
        )
        return

    # Score is the token that looks like "2-1" or "0-0"
    score_idx = None
    for i, token in enumerate(args):
        if "-" in token and all(p.isdigit() for p in token.split("-", 1) if p):
            score_idx = i
            break

    if score_idx is None:
        await update.message.reply_html(
            "❌ Не найден счёт. Формат: <code>/result Arsenal 2-1 Chelsea</code>"
        )
        return

    team1_raw = " ".join(args[:score_idx]).strip()
    score = args[score_idx]
    team2_raw = " ".join(args[score_idx + 1:]).strip()

    if not team1_raw or not team2_raw:
        await update.message.reply_html(
            "❌ Укажите обе команды. Формат: <code>/result Arsenal 2-1 Chelsea</code>"
        )
        return

    from team_parser import normalize_team_name
    team1 = normalize_team_name(team1_raw)
    team2 = normalize_team_name(team2_raw)

    prediction = await db.find_latest_unresolved_prediction(team1, team2)
    if not prediction:
        await update.message.reply_html(
            f"⚠️ Не найден прогноз для матча <b>{team1} vs {team2}</b>.\n"
            "Возможно, прогноз ещё не делался или уже имеет результат."
        )
        return

    resolved = await process_prediction_result(
        prediction["id"],
        score,
        score_team1=team1,
        score_team2=team2,
    )
    if not resolved:
        await update.message.reply_html(
            "❌ Не удалось сохранить результат. Попробуйте ещё раз позже."
        )
        return

    stats = await db.get_calibration_stats()

    msg = (
        f"✅ <b>Результат сохранён</b>\n"
        f"{resolved['team1']} {resolved['actual_score']} {resolved['team2']}\n\n"
    )
    if stats:
        msg += (
            f"📊 Точность на {stats['n_with_result']} матчах: "
            f"<b>{stats['acc_1x2']:.1f}%</b> (1X2)"
        )

    await update.message.reply_html(msg)


async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/stats — show historical accuracy."""
    stats = await db.get_calibration_stats()
    n_total = await db.get_prediction_count()
    bankroll_stats = await db.get_paper_bankroll_stats()
    per_league = await db.get_per_league_calibration()
    cal_curve = await db.get_calibration_curve()
    odds_move = await db.get_odds_movement_stats()
    await update.message.reply_html(
        format_stats(
            stats, n_total, bankroll_stats,
            per_league_stats=per_league,
            calibration_curve=cal_curve,
            odds_movement=odds_move,
        )
    )


async def bankroll_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/bankroll — show paper bankroll performance."""
    bankroll_stats = await db.get_paper_bankroll_stats()
    await update.message.reply_html(format_bankroll(bankroll_stats))


async def clv_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/clv — show CLV (Closing Line Value) tracking statistics."""
    stats = await db.get_clv_stats()
    entries = await db.get_recent_clv_entries(limit=10)
    await update.message.reply_html(format_clv(stats, entries))


async def history_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/history [limit] — show recent predictions with an optional limit."""
    limit = 10
    if context.args:
        token = context.args[0].strip()
        if not token.isdigit():
            await update.message.reply_html(
                "❌ <b>Формат:</b> <code>/history [1-50]</code>\n"
                "Пример: <code>/history 20</code>"
            )
            return
        limit = max(1, min(int(token), 50))

    rows = await db.get_prediction_history(limit=limit)
    await update.message.reply_html(
        format_history(
            rows,
            footer=f"<i>Последние {limit} прогнозов. /stats — точность статистики.</i>",
        )
    )


async def historymatch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/historymatch [limit] team1 vs team2 — show history for a specific match-up."""
    args = context.args or []
    if not args:
        await update.message.reply_html(
            "❓ <b>Формат:</b> <code>/historymatch [1-20] Команда1 vs Команда2</code>\n"
            "Пример: <code>/historymatch 5 Arsenal vs Chelsea</code>"
        )
        return

    limit = 5
    raw_match = " ".join(args)
    if args[0].isdigit():
        limit = max(1, min(int(args[0]), 20))
        raw_match = " ".join(args[1:]).strip()

    if not raw_match:
        await update.message.reply_html(
            "❌ Не указана пара команд. Пример: <code>/historymatch Arsenal vs Chelsea</code>"
        )
        return

    team1, team2 = parse_teams_from_input(raw_match)
    if not team1 or not team2:
        await update.message.reply_html(
            f"❌ Не удалось распознать команды из: <code>{html.escape(raw_match)}</code>\n\n"
            "Используйте формат: <code>/historymatch Arsenal vs Chelsea</code>"
        )
        return

    rows = await db.get_prediction_history_for_match(team1, team2, limit=limit)
    safe_team1 = html.escape(team1)
    safe_team2 = html.escape(team2)
    await update.message.reply_html(
        format_history(
            rows,
            title=f"📋 <b>История: {safe_team1} vs {safe_team2}</b>",
            empty_message=(
                f"<i>Для пары {safe_team1} vs {safe_team2} пока нет сохранённых прогнозов.</i>"
            ),
            footer=(
                f"<i>До {limit} последних прогнозов по этой паре. "
                "/history — общая история.</i>"
            ),
        )
    )


async def lessons_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/lessons — show active learned lessons."""
    lessons = await db.get_lessons(limit=10)
    if not lessons:
        await update.message.reply_html(
            "📚 <b>Уроки</b>\n\n"
            "Пока нет извлечённых уроков.\n"
            "Они появятся автоматически после нескольких матчей с известными результатами."
        )
        return

    lines = ["📚 <b>Уроки из прошлых прогнозов</b>\n"]
    for i, les in enumerate(lessons, 1):
        cat = les["category"]
        conf = les["confidence"]
        confirmed = les["times_confirmed"]
        conf_str = f" (подтверждено {confirmed}x)" if confirmed > 0 else ""
        lines.append(
            f"  {i}. [{cat}] {les['text']}\n"
            f"      Уверенность: {conf:.0%}{conf_str}"
        )
    lines.append(
        f"\n<i>Всего активных уроков: {len(lessons)}</i>"
    )
    await update.message.reply_html("\n".join(lines))


async def schedule_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/schedule — show today's top-league matches."""
    from scrapers.fotmob import fetch_today_matches
    from formatter import format_schedule

    msg = await update.message.reply_html("⏳ <b>Загружаю расписание...</b>")
    try:
        leagues = await fetch_today_matches()
        text = format_schedule(leagues)
    except Exception as exc:
        logger.warning("Schedule fetch failed: %s", exc)
        text = "❌ Не удалось загрузить расписание. Попробуйте позже."

    try:
        await msg.edit_text(text, parse_mode=HTML)
    except Exception as edit_exc:
        logger.warning("schedule edit_text failed: %s", edit_exc)


async def predict_day_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/predict_day — batch predict all today's matches from top leagues."""
    from scrapers.fotmob import fetch_today_matches

    user_id = update.effective_user.id
    progress_msg = await update.message.reply_html(
        "⏳ <b>Загружаю расписание для пакетного прогноза...</b>"
    )

    try:
        leagues = await fetch_today_matches()
    except Exception as exc:
        logger.warning("predict_day schedule fetch failed: %s", exc)
        await progress_msg.edit_text(
            "❌ Не удалось загрузить расписание.", parse_mode=HTML
        )
        return

    # Collect all matches
    all_matches: list[tuple[str, str, str]] = []  # (home, away, league)
    for league in leagues:
        for match in league.matches:
            all_matches.append((match.home, match.away, league.league_name))

    if not all_matches:
        await progress_msg.edit_text(
            "📭 <b>Нет матчей в топ-лигах сегодня.</b>", parse_mode=HTML
        )
        return

    # Optional: filter by league name if argument provided
    filter_league = " ".join(context.args).strip() if context.args else ""
    if filter_league:
        filtered = [
            (h, a, lg) for h, a, lg in all_matches
            if filter_league.lower() in lg.lower()
        ]
        if not filtered:
            await progress_msg.edit_text(
                f"❌ Лига <b>{html.escape(filter_league)}</b> не найдена.\n"
                f"Доступно: {', '.join(sorted(set(lg for _, _, lg in all_matches)))}",
                parse_mode=HTML,
            )
            return
        all_matches = filtered

    total = len(all_matches)
    await progress_msg.edit_text(
        f"🔄 <b>Пакетный прогноз:</b> {total} матчей\n\n"
        f"⏳ 0/{total} завершено...",
        parse_mode=HTML,
    )

    done = 0
    failed = 0

    for home, away, league_name in all_matches:
        cache_key = None
        is_leader = False
        try:
            # Check cache first
            cache_key = TTLCache.make_key(home, away)
            cache_state, cache_value = await match_cache.claim(cache_key)

            if cache_state == "cached":
                report = cache_value
                logger.info("Batch predict cache hit: %s vs %s", home, away)
            elif cache_state == "wait":
                try:
                    report = await cache_value
                except Exception:
                    failed += 1
                    done += 1
                    continue
            else:
                is_leader = True
                # Full scrape + analysis
                match_data = await scrape_all_parallel(home, away)
                report = await analyze_match(match_data)
                await match_cache.resolve(cache_key, report)
                is_leader = False  # resolved successfully

                # Save to DB
                try:
                    prediction_id = await db.save_prediction(
                        report,
                        match_data.data_completeness,
                        user_id=user_id,
                    )
                    await sync_prediction_to_historical(prediction_id, report, match_data)
                    await store_prediction_memory(prediction_id, report, match_data)
                except Exception as db_exc:
                    logger.warning("Batch DB save failed: %s", db_exc)

            # Send individual report
            report_text = format_report(report)
            for part in split_telegram_message(report_text):
                await update.message.reply_html(part)

        except Exception as exc:
            # Unblock any waiters on this cache key
            if is_leader and cache_key is not None:
                try:
                    await match_cache.fail(cache_key, exc)
                except Exception:
                    pass
            logger.warning(
                "Batch predict failed for %s vs %s: %s", home, away, exc
            )
            await update.message.reply_html(
                f"❌ <b>{html.escape(home)} vs {html.escape(away)}</b> "
                f"({html.escape(league_name)}): ошибка"
            )
            failed += 1

        done += 1
        # Update progress
        try:
            await progress_msg.edit_text(
                f"🔄 <b>Пакетный прогноз:</b> {total} матчей\n\n"
                f"{'✅' if done == total else '⏳'} {done}/{total} завершено"
                + (f" (❌ {failed} ошибок)" if failed else ""),
                parse_mode=HTML,
            )
        except TelegramError:
            pass

    # Final summary
    try:
        await progress_msg.edit_text(
            f"✅ <b>Пакетный прогноз завершён</b>\n\n"
            f"Всего: {total} матчей\n"
            f"Успешно: {total - failed}\n"
            + (f"Ошибки: {failed}" if failed else ""),
            parse_mode=HTML,
        )
    except TelegramError:
        pass


# ── Background job ──────────────────────────────────────────────────────────────

async def check_results_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job: check Fotmob for results and notify users."""
    try:
        resolved_list = await check_pending_results()
        if resolved_list:
            logger.info(
                "Background result check: found %d new results", len(resolved_list)
            )
            for resolved in resolved_list:
                uid = resolved.get("user_id")
                if uid:
                    try:
                        msg = format_result_notification(resolved)
                        await context.bot.send_message(
                            chat_id=uid,
                            text=msg,
                            parse_mode=HTML,
                        )
                    except Exception as tg_exc:
                        logger.warning(
                            "Notification send failed for user %d: %s", uid, tg_exc
                        )
                        continue

                    try:
                        await db.mark_notification_sent(resolved["prediction_id"])
                    except Exception as db_exc:
                        logger.warning(
                            "mark_notification_sent failed for prediction %d: %s",
                            resolved["prediction_id"], db_exc,
                        )
    except Exception as exc:
        logger.warning("Background result check failed: %s", exc)


async def pinecone_sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job: sync unsynced lessons to Pinecone cloud."""
    try:
        unsynced = await db.get_unsynced_lessons()
        if not unsynced:
            return
        from memory import get_shared_vector_memory
        vm = await get_shared_vector_memory()
        synced = await vm.sync_to_pinecone(unsynced)
        if synced > 0:
            for les in unsynced[:synced]:
                await db.mark_lesson_synced(les["id"])
            logger.info("Pinecone sync: %d lessons synced", synced)
    except Exception as exc:
        logger.warning("Pinecone sync job failed: %s", exc)


async def daily_pnl_summary_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Background job: send daily P&L summary to all active users at ~23:00 UTC."""
    from datetime import datetime, timezone, timedelta

    now = datetime.now(timezone.utc)
    day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)

    try:
        summary = await db.get_daily_pnl_summary(
            day_start.isoformat(), day_end.isoformat()
        )
        if not summary or (summary.get("settled_count", 0) == 0 and summary.get("placed_count", 0) == 0):
            logger.info("Daily P&L: no activity today, skipping notification")
            return

        user_ids = await db.get_active_user_ids()
        msg = format_daily_pnl(summary)

        for uid in user_ids:
            try:
                await context.bot.send_message(
                    chat_id=uid, text=msg, parse_mode=HTML,
                )
            except Exception as tg_exc:
                logger.warning("Daily P&L send failed for user %d: %s", uid, tg_exc)

        logger.info(
            "Daily P&L sent to %d users: %+.2fu (%d settled)",
            len(user_ids),
            float(summary.get("profit_units", 0)),
            summary.get("settled_count", 0),
        )
    except Exception as exc:
        logger.warning("Daily P&L summary job failed: %s", exc)


# ── Entry point ────────────────────────────────────────────────────────────────

def main() -> None:
    config.validate()

    async def post_init(app: Application) -> None:
        await db.init_db()
        logger.info("Self-learning database ready")

        # Initialize vector memory (non-critical — bot works without it)
        try:
            from memory import get_shared_vector_memory
            vm = await get_shared_vector_memory()
            stats = await vm.get_stats()
            logger.info(
                "VectorMemory ready: %d matches, %d lessons, %d patterns",
                stats["matches"], stats["lessons"], stats["team_patterns"],
            )
        except Exception as exc:
            logger.warning("VectorMemory init failed (non-critical): %s", exc)

    async def post_shutdown(app: Application) -> None:
        try:
            await close_shared_vector_memory()
        except Exception as exc:
            logger.warning("VectorMemory shutdown failed (non-critical): %s", exc)

    app = (
        Application.builder()
        .token(config.TELEGRAM_TOKEN)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("predict", predict_handler))
    app.add_handler(CommandHandler("result", result_handler))
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("bankroll", bankroll_handler))
    app.add_handler(CommandHandler("history", history_handler))
    app.add_handler(CommandHandler("historymatch", historymatch_handler))
    app.add_handler(CommandHandler("schedule", schedule_handler))
    app.add_handler(CommandHandler("predict_day", predict_day_handler))
    app.add_handler(CommandHandler("lessons", lessons_handler))
    app.add_handler(CommandHandler("health", health_handler))
    app.add_handler(CommandHandler("debugsources", debugsources_handler))
    app.add_handler(CommandHandler("clv", clv_handler))

    # Auto-check for match results every 3 hours
    app.job_queue.run_repeating(
        check_results_job,
        interval=10800,  # 3 hours
        first=300,       # first run 5 min after startup
    )

    # Pinecone sync every 6 hours (if enabled)
    if config.PINECONE_ENABLED:
        app.job_queue.run_repeating(
            pinecone_sync_job,
            interval=21600,  # 6 hours
            first=600,       # 10 min after startup
        )

    # Daily P&L summary at 23:00 UTC
    from datetime import time as dt_time, timezone
    app.job_queue.run_daily(
        daily_pnl_summary_job,
        time=dt_time(hour=23, minute=0, tzinfo=timezone.utc),
    )

    logger.info("Bot started. Waiting for commands...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
