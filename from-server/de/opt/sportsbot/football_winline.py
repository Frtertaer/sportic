#!/usr/bin/env python3
"""Produce a strict snapshot of Winline's public football prematch board.

The Winline board is virtualised, so visible cards are accumulated while the
page scrolls.  Only complete prematch ``Match`` rows with exact 1X2 and total
2.5 prices are published.  Winline labels totals as ``М - line - Б``: the
first total price is UNDER (``Меньше``), the second is OVER (``Больше``).

No account state or betting cookies are loaded.  A degraded scrape exits
non-zero and preserves the prior snapshot; a successful replacement is made
atomically with mode 0644 for the unprivileged council reader.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping


SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR if (SCRIPT_DIR / "paper_council").is_dir() else SCRIPT_DIR.parent
sys.path.insert(0, str(ROOT))

from paper_council.winline import (  # noqa: E402
    WINLINE_SNAPSHOT_SCHEMA,
    WINLINE_SOURCE,
    WINLINE_SOURCE_URL,
    WinlineEvent,
    WinlineSnapshot,
    parse_kickoff_utc,
)


DEFAULT_OUTPUT = ROOT / "data" / "football_winline.json"
DEFAULT_MIN_VALID_ROWS = max(1, int(os.getenv("WINLINE_MIN_VALID_ROWS", "100")))
DEFAULT_MIN_BOARD_ROWS = max(1, int(os.getenv("WINLINE_MIN_BOARD_ROWS", "150")))
DEFAULT_MAX_SCROLLS = max(1, int(os.getenv("WINLINE_MAX_SCROLLS", "60")))
DEFAULT_STAGNANT_SCROLLS = max(2, int(os.getenv("WINLINE_STAGNANT_SCROLLS", "7")))

_EVENT_HREF_RE = re.compile(r"^/stavki/event/([1-9][0-9]{0,19})/?$")
_DOM_EVENT_ID_RE = re.compile(r"^eventId-([1-9][0-9]{0,19})$")
_DECIMAL_RE = re.compile(r"^[0-9]{1,4}(?:[.,][0-9]{1,4})?$")


_EXTRACT_VISIBLE = r"""() => {
  const text = element => (element?.innerText || '').replace(/\u00a0/g, ' ').trim();
  const out = [];
  for (const card of document.querySelectorAll('.card:not(.card--live)')) {
    const anchor = card.querySelector('.card__competitors a[href*="/stavki/event/"]')
      || card.querySelector('a[href*="/stavki/event/"]');
    const eventRoot = card.closest('ww-feature-block-event-dsk');
    const tournament = card.closest('.block-tournament, ww-feature-block-tournament-dsk');
    const tournamentHeader = tournament?.querySelector('.block-tournament-header-wrapper');
    const names = text(anchor).split('\n').map(x => x.trim()).filter(Boolean).slice(0, 2);
    const times = [...card.querySelectorAll('.card__competitors .header-left__time')]
      .map(text).filter(Boolean);
    const body = [...card.querySelectorAll('.card__body')]
      .find(node => !node.classList.contains('card__body--second'));
    const markets = body ? [...body.querySelectorAll('ww-feature-event-market-dsk')] : [];
    const oneXTwo = markets[0];
    const total25 = markets[2];
    const marketLabel = text(body?.closest('.coeffs-wrapper')?.querySelector('.match-row-label'));
    const layoutOk = marketLabel === '\u041c\u0430\u0442\u0447'
      && oneXTwo?.querySelectorAll('.coefficient-button_generic3').length === 3
      && total25?.querySelectorAll('.coefficient-button_total2').length === 2
      && (() => {
      const middle = text(total25.querySelector('.coefficient-middle'));
      return middle.replace(',', '.') === '2.5';
      })();
    out.push({
      dom_event_id: eventRoot?.id || card.id || '',
      href: anchor?.getAttribute('href') || '',
      competition: text(tournamentHeader).split('\n').map(x => x.trim()).filter(Boolean)[0] || '',
      team_names: names,
      kickoff_label: times.find(value => !/^\+\d+$/.test(value)) || '',
      is_live: false,
      market_label: marketLabel,
      market_layout_ok: Boolean(layoutOk),
      total_order_contract: layoutOk ? 'UNDER_LINE_OVER' : '',
      h2h_prices: oneXTwo
        ? [...oneXTwo.querySelectorAll('.coefficient-button_generic3 span')].map(text).filter(Boolean)
        : [],
      total_line: total25 ? text(total25.querySelector('.coefficient-middle')) : '',
      total_prices: total25
        ? [...total25.querySelectorAll('.coefficient-button_total2 span')].map(text).filter(Boolean)
        : [],
    });
  }
  return out;
}"""


_SCROLL_BOARD = r"""() => {
  const cards = document.querySelectorAll('ww-feature-block-event-dsk');
  if (cards.length) cards[cards.length - 1].scrollIntoView({block: 'end'});
  const root = document.querySelector('main, .main__wrapper, [class*="main__wrapper"]');
  if (root) {
    root.scrollTop = root.scrollHeight;
  }
  window.scrollTo(0, document.documentElement.scrollHeight);
}"""


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _parse_decimal(value: Any) -> float:
    if not isinstance(value, str):
        raise ValueError("rendered decimal price is not text")
    raw = value.strip().replace(",", ".")
    if not _DECIMAL_RE.fullmatch(raw):
        raise ValueError("rendered decimal price is missing or malformed")
    return float(raw)


def raw_card_to_event(
    row: Mapping[str, Any],
    *,
    observed_at: datetime | str,
) -> dict[str, Any] | None:
    """Convert one extracted DOM row; incomplete/live rows are not candidates."""

    if not isinstance(row, Mapping) or row.get("is_live") is True:
        return None
    if (
        row.get("market_label") != "\u041c\u0430\u0442\u0447"
        or row.get("market_layout_ok") is not True
        or row.get("total_order_contract") != "UNDER_LINE_OVER"
    ):
        return None
    dom_match = _DOM_EVENT_ID_RE.fullmatch(str(row.get("dom_event_id") or ""))
    href_match = _EVENT_HREF_RE.fullmatch(str(row.get("href") or ""))
    if not href_match:
        return None
    if dom_match and dom_match.group(1) != href_match.group(1):
        raise ValueError("Winline DOM id and event href disagree")
    names = row.get("team_names")
    h2h = row.get("h2h_prices")
    totals = row.get("total_prices")
    if not isinstance(names, list) or len(names) != 2:
        return None
    if not isinstance(h2h, list) or len(h2h) != 3:
        return None
    if not isinstance(totals, list) or len(totals) != 2:
        return None
    try:
        line = float(str(row.get("total_line") or "").replace(",", "."))
    except ValueError:
        return None
    if line != 2.5:
        return None
    try:
        observed = (
            observed_at
            if isinstance(observed_at, datetime)
            else datetime.fromisoformat(str(observed_at).replace("Z", "+00:00"))
        )
        if observed.tzinfo is None or observed.utcoffset() is None:
            raise ValueError("observed_at must include a timezone")
        observed = observed.astimezone(timezone.utc)
        kickoff = parse_kickoff_utc(str(row.get("kickoff_label") or ""), now=observed)
        # Winline's header order is M (under), line, B (over).
        event = WinlineEvent.model_validate(
            {
                "provider": "winline",
                "event_id": href_match.group(1),
                "competition": row.get("competition"),
                "home_team": names[0],
                "away_team": names[1],
                "kickoff_utc": kickoff,
                "captured_at": observed,
                "h2h": {
                    "home": _parse_decimal(h2h[0]),
                    "draw": _parse_decimal(h2h[1]),
                    "away": _parse_decimal(h2h[2]),
                },
                "totals_2_5": {
                    "under": _parse_decimal(totals[0]),
                    "over": _parse_decimal(totals[1]),
                },
            }
        )
    except (TypeError, ValueError):
        return None
    return event.model_dump(mode="json")


def _identity_tuple(row: Mapping[str, Any]) -> tuple[str, ...]:
    return (
        str(row.get("dom_event_id") or ""),
        str(row.get("href") or ""),
        *(str(item) for item in (row.get("team_names") or [])),
        str(row.get("kickoff_label") or ""),
    )


def _row_completeness(row: Mapping[str, Any]) -> int:
    """Prefer the most completely rendered copy of a virtualised event card."""

    return sum(
        (
            len(row.get("team_names") or []) == 2,
            bool(row.get("kickoff_label")),
            len(row.get("h2h_prices") or []) == 3,
            len(row.get("total_prices") or []) == 2,
            bool(row.get("competition")),
        )
    )


def merge_visible_rows(
    accumulated: dict[str, tuple[dict[str, Any], datetime]],
    rows: list[dict[str, Any]],
    *,
    observed_at: datetime,
) -> int:
    """Accumulate virtualised cards, rejecting an event-id identity collision."""

    added = 0
    for row in rows:
        href_match = _EVENT_HREF_RE.fullmatch(str(row.get("href") or ""))
        if not href_match:
            continue
        event_id = href_match.group(1)
        dom_match = _DOM_EVENT_ID_RE.fullmatch(str(row.get("dom_event_id") or ""))
        if dom_match and dom_match.group(1) != event_id:
            raise RuntimeError("Winline DOM id and event href disagree during accumulation")
        previous = accumulated.get(event_id)
        if previous is not None:
            old_row = previous[0]
            old_names = tuple(str(item).strip().casefold() for item in old_row.get("team_names") or [])
            new_names = tuple(str(item).strip().casefold() for item in row.get("team_names") or [])
            if len(old_names) == 2 and len(new_names) == 2 and old_names != new_names:
                raise RuntimeError(f"Winline event {event_id} changed identity (teams) during scrape")
        if previous is None:
            added += 1
        if previous is None or _row_completeness(row) >= _row_completeness(previous[0]):
            accumulated[event_id] = (dict(row), observed_at)
    return added


async def scrape_snapshot(
    *,
    max_scrolls: int = DEFAULT_MAX_SCROLLS,
    stagnant_scrolls: int = DEFAULT_STAGNANT_SCROLLS,
) -> dict[str, Any]:
    """Scrape and validate one complete public-board snapshot."""

    if max_scrolls <= 0 or stagnant_scrolls <= 0:
        raise ValueError("scroll limits must be positive")
    from playwright.async_api import async_playwright

    accumulated: dict[str, tuple[dict[str, Any], datetime]] = {}
    async with async_playwright() as playwright:
        launch_kwargs: dict[str, Any] = {
            "headless": True,
            "args": ["--no-sandbox", "--disable-dev-shm-usage"],
        }
        executable = str(os.getenv("WINLINE_CHROMIUM_EXECUTABLE", "") or "").strip()
        if executable:
            launch_kwargs["executable_path"] = executable
        browser = await playwright.chromium.launch(**launch_kwargs)
        try:
            context = await browser.new_context(
                viewport={"width": 1500, "height": 1100},
                locale="ru-RU",
                timezone_id="UTC",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
            response = await page.goto(
                WINLINE_SOURCE_URL,
                wait_until="domcontentloaded",
                timeout=60_000,
            )
            if response is None or response.status >= 400:
                raise RuntimeError("Winline board navigation failed")
            await page.wait_for_timeout(8_000)
            stagnant = 0
            scan_stop_reason = "bounded"
            scan_iterations = 0
            for scan_iterations in range(1, max_scrolls + 1):
                observed = utc_now()
                rows = await page.evaluate(_EXTRACT_VISIBLE)
                added = merge_visible_rows(accumulated, rows, observed_at=observed)
                stagnant = 0 if added else stagnant + 1
                if stagnant >= stagnant_scrolls:
                    scan_stop_reason = "stable"
                    break
                await page.evaluate(_SCROLL_BOARD)
                await page.wait_for_timeout(450)
            observed = utc_now()
            merge_visible_rows(
                accumulated,
                await page.evaluate(_EXTRACT_VISIBLE),
                observed_at=observed,
            )
        finally:
            await browser.close()

    valid: dict[str, dict[str, Any]] = {}
    for event_id, (row, observed_at) in accumulated.items():
        event = raw_card_to_event(row, observed_at=observed_at)
        if event is not None:
            valid[event_id] = event
    events = sorted(
        valid.values(),
        key=lambda item: (str(item["kickoff_utc"]), str(item["event_id"])),
    )
    completed_at = utc_now()
    snapshot = WinlineSnapshot.model_validate(
        {
            "schema_version": WINLINE_SNAPSHOT_SCHEMA,
            "source": WINLINE_SOURCE,
            "source_url": WINLINE_SOURCE_URL,
            "captured_at": completed_at,
            "scan_complete": True,
            "scan_stop_reason": scan_stop_reason,
            "scan_iterations": scan_iterations,
            "board_event_count": len(accumulated),
            "event_count": len(events),
            "events": events,
        }
    )
    return snapshot.model_dump(mode="json")


def write_snapshot_atomic(output: str | Path, snapshot: Mapping[str, Any]) -> None:
    """Atomically replace ``output`` with a validated world-readable snapshot."""

    target = Path(output)
    validated = WinlineSnapshot.model_validate(dict(snapshot)).model_dump(mode="json")
    target.parent.mkdir(parents=True, exist_ok=True)
    file_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    temporary = Path(temporary_name)
    try:
        payload = (json.dumps(validated, ensure_ascii=False, sort_keys=True) + "\n").encode("utf-8")
        with os.fdopen(file_descriptor, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
            if hasattr(os, "fchmod"):
                os.fchmod(handle.fileno(), 0o644)
        # Windows has no fchmod; setting the temporary path before replace keeps
        # the Linux production transition atomic while leaving tests portable.
        os.chmod(temporary, 0o644)
        os.replace(temporary, target)
        try:
            directory_flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
            directory_fd = os.open(target.parent, directory_flags)
        except OSError:
            directory_fd = None
        if directory_fd is not None:
            try:
                try:
                    os.fsync(directory_fd)
                except OSError:
                    # Never report a failure after the atomic replacement already
                    # superseded the previous recoverable snapshot.
                    pass
            finally:
                os.close(directory_fd)
    finally:
        if temporary.exists():
            temporary.unlink()


def publish_snapshot(
    output: str | Path,
    snapshot: Mapping[str, Any],
    *,
    min_valid_rows: int = DEFAULT_MIN_VALID_ROWS,
    min_board_rows: int = DEFAULT_MIN_BOARD_ROWS,
) -> None:
    """Publish only a healthy scrape, otherwise preserve the previous target."""

    if min_valid_rows <= 0 or min_board_rows <= 0:
        raise ValueError("minimum row counts must be positive")
    event_count = snapshot.get("event_count") if isinstance(snapshot, Mapping) else None
    if not isinstance(event_count, int) or isinstance(event_count, bool):
        raise RuntimeError("Winline scrape did not report a valid event count")
    if event_count < min_valid_rows:
        raise RuntimeError(
            f"Winline scrape produced {event_count} complete rows; minimum is {min_valid_rows}"
        )
    board_count = snapshot.get("board_event_count")
    if not isinstance(board_count, int) or board_count < min_board_rows:
        raise RuntimeError(
            f"Winline scan covered {board_count!r} board rows; minimum is {min_board_rows}"
        )
    write_snapshot_atomic(output, snapshot)


async def async_main(args: argparse.Namespace) -> None:
    snapshot = await scrape_snapshot(
        max_scrolls=args.max_scrolls,
        stagnant_scrolls=args.stagnant_scrolls,
    )
    publish_snapshot(
        args.output,
        snapshot,
        min_valid_rows=args.min_valid_rows,
        min_board_rows=args.min_board_rows,
    )
    print(
        json.dumps(
            {
                "status": "ok",
                "schema_version": WINLINE_SNAPSHOT_SCHEMA,
                "event_count": snapshot["event_count"],
                "captured_at": snapshot["captured_at"],
                "output": str(args.output),
            },
            ensure_ascii=False,
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Winline public football prematch snapshot")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--min-valid-rows", type=int, default=DEFAULT_MIN_VALID_ROWS)
    parser.add_argument("--min-board-rows", type=int, default=DEFAULT_MIN_BOARD_ROWS)
    parser.add_argument("--max-scrolls", type=int, default=DEFAULT_MAX_SCROLLS)
    parser.add_argument("--stagnant-scrolls", type=int, default=DEFAULT_STAGNANT_SCROLLS)
    return parser


def main() -> None:
    asyncio.run(async_main(build_parser().parse_args()))


if __name__ == "__main__":
    main()
