"""SQLite source of truth for the isolated, prospectively registered paper council."""

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import quote

from .core import (
    COUNCIL_MEMBERS,
    EXPERIMENT_VERSION,
    INPUT_BUILDER_VERSION,
    INTENDED_MODEL_ROSTER,
    POLICY_VERSION,
    PROMPT_VERSION,
    SCHEMA_VERSION,
    CouncilForecast,
    aggregate_consensus,
    serialise_council_result,
)


APPLICATION_ID = 0x434E434C  # "CNCL"; prevents accidental adoption of sportsbot.db.
DB_USER_VERSION = 2

SCHEMA = """
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS council_runs (
    run_id                       TEXT PRIMARY KEY,
    event_id                     TEXT NOT NULL,
    attempt_no                   INTEGER NOT NULL,
    is_designated                INTEGER NOT NULL DEFAULT 0,
    team1                        TEXT NOT NULL,
    team2                        TEXT NOT NULL,
    sport_key                    TEXT NOT NULL,
    competition                  TEXT,
    kickoff_utc                  TEXT NOT NULL,
    as_of_utc                    TEXT NOT NULL,
    horizon_hours                REAL NOT NULL,
    context_hash                 TEXT NOT NULL DEFAULT 'PENDING',
    data_completeness            REAL,
    evidence_count               INTEGER NOT NULL DEFAULT 0,
    status                       TEXT NOT NULL,
    schema_version               TEXT NOT NULL,
    prompt_version               TEXT NOT NULL,
    experiment_version           TEXT NOT NULL,
    policy_version               TEXT NOT NULL,
    input_builder_version        TEXT NOT NULL,
    model_roster_json            TEXT NOT NULL,
    odds_snapshot_json           TEXT,
    benchmark_probabilities_json TEXT,
    input_envelope_json          TEXT,
    error_type                   TEXT,
    error_message                TEXT,
    created_at                   TEXT NOT NULL,
    finished_at                  TEXT,
    UNIQUE(event_id, attempt_no)
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_council_designated_event
ON council_runs(event_id) WHERE is_designated = 1;

CREATE INDEX IF NOT EXISTS idx_council_runs_kickoff ON council_runs(kickoff_utc);

CREATE TABLE IF NOT EXISTS council_member_forecasts (
    run_id                 TEXT NOT NULL,
    round_no               INTEGER NOT NULL,
    member                 TEXT NOT NULL,
    status                 TEXT NOT NULL,
    latency_ms             INTEGER,
    output_hash            TEXT,
    served_worker          TEXT,
    served_model           TEXT,
    identity_source        TEXT,
    request_id             TEXT,
    claimed_market_code    TEXT,
    claimed_selection_code TEXT,
    claimed_changed_mind   INTEGER,
    policy_edge_pp         REAL,
    error_type             TEXT,
    error_message          TEXT,
    forecast_json          TEXT,
    PRIMARY KEY(run_id, round_no, member),
    FOREIGN KEY(run_id) REFERENCES council_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS council_consensus (
    run_id                      TEXT NOT NULL,
    stage                       TEXT NOT NULL,
    status                      TEXT NOT NULL,
    market_code                 TEXT NOT NULL,
    selection_code              TEXT NOT NULL,
    prob_w1                     REAL,
    prob_draw                   REAL,
    prob_w2                     REAL,
    prob_over_2_5               REAL,
    prob_under_2_5              REAL,
    consensus_probability       REAL,
    consensus_edge_pp           REAL,
    agreement_count             INTEGER,
    agreement_ratio             REAL,
    selected_probability_stddev REAL,
    benchmark_price             REAL,
    benchmark_is_executable     INTEGER NOT NULL DEFAULT 0,
    votes_json                  TEXT,
    ranges_json                 TEXT,
    details_json                TEXT,
    reason                      TEXT,
    PRIMARY KEY(run_id, stage),
    FOREIGN KEY(run_id) REFERENCES council_runs(run_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS council_match_results (
    event_id            TEXT PRIMARY KEY,
    home_goals          INTEGER NOT NULL,
    away_goals          INTEGER NOT NULL,
    actual_1x2          TEXT NOT NULL,
    actual_totals_2_5   TEXT NOT NULL,
    source              TEXT NOT NULL,
    score_basis         TEXT NOT NULL,
    provider_updated_at TEXT,
    settled_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS council_resolution_status (
    event_id       TEXT PRIMARY KEY,
    status         TEXT NOT NULL,
    attempt_count  INTEGER NOT NULL DEFAULT 0,
    detail         TEXT,
    last_attempt_at TEXT NOT NULL
);
"""


def _safe_db_path(path: str | Path) -> Path:
    db_path = Path(path).expanduser().resolve()
    if db_path.name.lower() in {"sportsbot.db", "football.db", "predictions.db"}:
        raise ValueError(f"refusing non-council database path: {db_path.name}")
    return db_path


def _application_id(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA application_id").fetchone()[0])


def connect(path: str | Path) -> sqlite3.Connection:
    """Open/create only a marked council DB; never adopt an arbitrary SQLite DB."""
    db_path = _safe_db_path(path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    app_id = _application_id(conn)
    user_tables = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    if app_id == 0 and not user_tables:
        conn.execute(f"PRAGMA application_id={APPLICATION_ID}")
        conn.execute(f"PRAGMA user_version={DB_USER_VERSION}")
    elif app_id != APPLICATION_ID:
        conn.close()
        raise ValueError("database is not marked as a paper-council store")
    elif int(conn.execute("PRAGMA user_version").fetchone()[0]) != DB_USER_VERSION:
        conn.close()
        raise ValueError("unsupported paper-council database version")
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(SCHEMA)
    return conn


def connect_readonly(path: str | Path) -> sqlite3.Connection:
    """Open an existing, correctly marked store with SQLite-enforced query-only mode."""
    db_path = _safe_db_path(path)
    if not db_path.is_file():
        raise FileNotFoundError(f"council database does not exist: {db_path}")
    encoded = quote(db_path.as_posix(), safe="/:")
    conn = sqlite3.connect(f"file:{encoded}?mode=ro", uri=True, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    if _application_id(conn) != APPLICATION_ID:
        conn.close()
        raise ValueError("database is not marked as a paper-council store")
    if int(conn.execute("PRAGMA user_version").fetchone()[0]) != DB_USER_VERSION:
        conn.close()
        raise ValueError("unsupported paper-council database version")
    return conn


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _redact(value: Any) -> str:
    text = str(value or "")[:1000]
    text = re.sub(
        r"(?i)(api[_-]?key|token|password|secret)(=|%3D|\s*:\s*)[^&\s]+",
        r"\1\2[REDACTED]",
        text,
    )
    return text


def has_designated_event(conn: sqlite3.Connection, event_id: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM council_runs WHERE event_id=? AND is_designated=1 LIMIT 1",
        (event_id,),
    ).fetchone() is not None


def _next_attempt_no_locked(conn: sqlite3.Connection, event_id: str) -> int:
    row = conn.execute(
        "SELECT COALESCE(MAX(attempt_no),0)+1 FROM council_runs WHERE event_id=?",
        (event_id,),
    ).fetchone()
    return int(row[0])


def reserve_council_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    event_id: str,
    team1: str,
    team2: str,
    sport_key: str,
    competition: str,
    kickoff_utc: str,
    as_of_utc: str,
    horizon_hours: float,
    odds_snapshot: Mapping[str, Any],
    created_at: str,
) -> bool:
    """Atomically pre-register the first eligible attempt before scrape/model work."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        if has_designated_event(conn, event_id):
            conn.rollback()
            return False
        attempt_no = _next_attempt_no_locked(conn, event_id)
        conn.execute(
            """
            INSERT INTO council_runs(
              run_id,event_id,attempt_no,is_designated,team1,team2,sport_key,competition,
              kickoff_utc,as_of_utc,horizon_hours,status,schema_version,prompt_version,
              experiment_version,policy_version,input_builder_version,model_roster_json,
              odds_snapshot_json,created_at
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                run_id,event_id,attempt_no,1,team1,team2,sport_key,competition,
                kickoff_utc,as_of_utc,round(float(horizon_hours),3),"RUNNING",
                SCHEMA_VERSION,PROMPT_VERSION,EXPERIMENT_VERSION,POLICY_VERSION,
                INPUT_BUILDER_VERSION,_json(INTENDED_MODEL_ROSTER),_json(odds_snapshot),created_at,
            ),
        )
        for round_no in (1, 2):
            for member in COUNCIL_MEMBERS:
                conn.execute(
                    """INSERT INTO council_member_forecasts(run_id,round_no,member,status)
                       VALUES(?,?,?,'NOT_RUN')""",
                    (run_id, round_no, member),
                )
        for stage in ("independent", "debated"):
            conn.execute(
                """INSERT INTO council_consensus(
                     run_id,stage,status,market_code,selection_code,reason
                   ) VALUES(?,?,'QUORUM_FAILED','NO_BET','NO_BET','run reserved; forecast unavailable')""",
                (run_id, stage),
            )
        conn.commit()
        return True
    except Exception:
        conn.rollback()
        raise


def mark_council_run_failed(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    error_type: str,
    error_message: str,
    finished_at: str,
) -> None:
    with conn:
        cursor = conn.execute(
            """UPDATE council_runs
               SET status='PIPELINE_ERROR',error_type=?,error_message=?,finished_at=?
               WHERE run_id=? AND status='RUNNING'""",
            (_redact(error_type), _redact(error_message), finished_at, run_id),
        )
        if cursor.rowcount != 1:
            raise ValueError("reserved council run is missing or already terminal")


def reconcile_stale_running(
    conn: sqlite3.Connection,
    *,
    stale_before_utc: str,
    finished_at: str,
) -> int:
    """Keep killed/timed-out first attempts in the denominator as terminal failures."""
    with conn:
        cursor = conn.execute(
            """UPDATE council_runs
               SET status='PIPELINE_TIMEOUT',error_type='StaleRunning',
                   error_message='reserved run exceeded the recovery deadline',finished_at=?
               WHERE is_designated=1 AND status='RUNNING' AND created_at<?""",
            (finished_at, stale_before_utc),
        )
    return int(cursor.rowcount)


def _validated_result(
    result: Mapping[str, Any], *, context_hash: str
) -> tuple[dict[str, Any], str]:
    serialised = serialise_council_result(result)
    if serialised.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("council result schema version mismatch")
    if serialised.get("prompt_version") != PROMPT_VERSION:
        raise ValueError("council result prompt version mismatch")
    if serialised.get("experiment_version") != EXPERIMENT_VERSION:
        raise ValueError("council result experiment version mismatch")
    if serialised.get("policy_version") != POLICY_VERSION:
        raise ValueError("council result policy version mismatch")
    if serialised.get("input_builder_version") != INPUT_BUILDER_VERSION:
        raise ValueError("council result input-builder version mismatch")
    if serialised.get("context_hash") != context_hash:
        raise ValueError("council result context hash mismatch")
    if tuple(serialised.get("members") or ()) != COUNCIL_MEMBERS:
        raise ValueError("council result member roster mismatch")
    benchmark = serialised.get("benchmark_probabilities") or {}
    valid_rounds: dict[int, dict[str, CouncilForecast]] = {1: {}, 2: {}}
    for round_no, key in ((1, "round1"), (2, "round2")):
        items = serialised.get(key) or {}
        if set(items) - set(COUNCIL_MEMBERS):
            raise ValueError(f"round {round_no} contains an unknown council member")
        for member, attempt in items.items():
            if attempt.get("model") != member:
                raise ValueError("member result is stored under a different seat")
            status = attempt.get("status")
            if status not in {"VALID", "ERROR"}:
                raise ValueError("member result has an invalid terminal status")
            if status != "VALID":
                continue
            if attempt.get("served_worker") != member:
                raise ValueError("valid forecast lacks an exact served-worker identity")
            if not attempt.get("served_model") or not attempt.get("request_id"):
                raise ValueError("valid forecast lacks served-model/request telemetry")
            if attempt.get("identity_source") not in {
                "gateway-response",
                "gateway-config-override",
                "gateway-static-default",
            }:
                raise ValueError("valid forecast lacks a model identity source")
            if str(attempt.get("served_model")) != INTENDED_MODEL_ROSTER[member]:
                raise ValueError("served model differs from the pre-registered roster")
            forecast = CouncilForecast.model_validate(attempt.get("forecast"))
            if forecast.input_hash != context_hash:
                raise ValueError("member forecast context hash differs from council run")
            valid_rounds[round_no][member] = forecast
    expected_status = (
        "QUORUM_FAILED_ROUND1"
        if len(valid_rounds[1]) != len(COUNCIL_MEMBERS)
        else "QUORUM_FAILED_ROUND2"
        if len(valid_rounds[2]) != len(COUNCIL_MEMBERS)
        else "COMPLETE"
    )
    if serialised.get("status") != expected_status:
        raise ValueError("council terminal status does not match exact round quorum")
    serialised["independent_consensus"] = aggregate_consensus(
        valid_rounds[1], benchmark_probabilities=benchmark
    )
    serialised["debated_consensus"] = aggregate_consensus(
        valid_rounds[2], benchmark_probabilities=benchmark
    )
    for member in COUNCIL_MEMBERS:
        models = {
            str((serialised[f"round{round_no}"][member]).get("served_model"))
            for round_no in (1, 2)
            if member in (serialised.get(f"round{round_no}") or {})
            and (serialised[f"round{round_no}"][member]).get("status") == "VALID"
        }
        if len(models) > 1:
            raise ValueError("served model changed between council rounds")
    return serialised, expected_status


def _benchmark_price(
    consensus: Mapping[str, Any], odds_snapshot: Mapping[str, Any]
) -> float | None:
    market = str(consensus.get("market_code") or "")
    selection = str(consensus.get("selection_code") or "")
    if market == "1X2":
        key = {"TEAM1": "home", "DRAW": "draw", "TEAM2": "away"}.get(selection)
        value = (odds_snapshot.get("h2h") or {}).get(key) if key else None
    elif market == "TOTALS_2_5":
        key = {"OVER": "over", "UNDER": "under"}.get(selection)
        value = (odds_snapshot.get("totals_2_5") or {}).get(key) if key else None
    else:
        value = None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 1.0 else None


def finalize_council_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    as_of_utc: str,
    context_hash: str,
    data_completeness: float | None,
    evidence_count: int,
    input_envelope: Mapping[str, Any],
    result: Mapping[str, Any],
    finished_at: str,
) -> None:
    serialised, terminal_status = _validated_result(result, context_hash=context_hash)
    benchmark = serialised.get("benchmark_probabilities") or {}
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT status,odds_snapshot_json FROM council_runs WHERE run_id=?",
            (run_id,),
        ).fetchone()
        if row is None or row["status"] != "RUNNING":
            raise ValueError("reserved council run is missing or already terminal")
        odds_snapshot = json.loads(row["odds_snapshot_json"] or "{}")
        conn.execute(
            """UPDATE council_runs
               SET as_of_utc=?,context_hash=?,data_completeness=?,evidence_count=?,status=?,
                   benchmark_probabilities_json=?,input_envelope_json=?,finished_at=?
               WHERE run_id=?""",
            (
                as_of_utc,context_hash,data_completeness,int(evidence_count),terminal_status,
                _json(benchmark),_json(input_envelope),finished_at,run_id,
            ),
        )
        for round_no, key in ((1, "round1"), (2, "round2")):
            items = serialised.get(key) or {}
            for member in COUNCIL_MEMBERS:
                attempt = items.get(member) or {"status": "NOT_RUN", "model": member}
                forecast = attempt.get("forecast")
                conn.execute(
                    """UPDATE council_member_forecasts SET
                         status=?,latency_ms=?,output_hash=?,served_worker=?,served_model=?,identity_source=?,
                         request_id=?,claimed_market_code=?,claimed_selection_code=?,
                         claimed_changed_mind=?,policy_edge_pp=?,error_type=?,error_message=?,forecast_json=?
                       WHERE run_id=? AND round_no=? AND member=?""",
                    (
                        attempt.get("status","NOT_RUN"),attempt.get("latency_ms"),
                        attempt.get("output_hash"),attempt.get("served_worker"),
                        attempt.get("served_model"),attempt.get("identity_source"),attempt.get("request_id"),
                        attempt.get("claimed_market_code"),attempt.get("claimed_selection_code"),
                        int(bool(attempt.get("claimed_changed_mind")))
                        if attempt.get("claimed_changed_mind") is not None else None,
                        attempt.get("policy_edge_pp"),attempt.get("error_type"),
                        _redact(attempt.get("error")),_json(forecast) if forecast else None,
                        run_id,round_no,member,
                    ),
                )
        for stage, key in (("independent", "independent_consensus"), ("debated", "debated_consensus")):
            consensus = serialised[key]
            conn.execute(
                """UPDATE council_consensus SET
                     status=?,market_code=?,selection_code=?,prob_w1=?,prob_draw=?,prob_w2=?,
                     prob_over_2_5=?,prob_under_2_5=?,consensus_probability=?,consensus_edge_pp=?,
                     agreement_count=?,agreement_ratio=?,selected_probability_stddev=?,benchmark_price=?,
                     benchmark_is_executable=0,votes_json=?,ranges_json=?,details_json=?,reason=?
                   WHERE run_id=? AND stage=?""",
                (
                    consensus.get("status","QUORUM_FAILED"),consensus.get("market_code","NO_BET"),
                    consensus.get("selection_code","NO_BET"),consensus.get("prob_w1"),
                    consensus.get("prob_draw"),consensus.get("prob_w2"),
                    consensus.get("prob_over_2_5"),consensus.get("prob_under_2_5"),
                    consensus.get("consensus_probability"),consensus.get("consensus_edge_pp"),
                    consensus.get("agreement_count"),consensus.get("agreement_ratio"),
                    consensus.get("selected_probability_stddev"),
                    _benchmark_price(consensus, odds_snapshot),_json(consensus.get("votes") or {}),
                    _json(consensus.get("probability_ranges") or {}),_json(consensus),
                    consensus.get("reason"),run_id,stage,
                ),
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def save_council_run(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    event_id: str,
    team1: str,
    team2: str,
    sport_key: str,
    competition: str,
    kickoff_utc: str,
    as_of_utc: str,
    horizon_hours: float,
    context_hash: str,
    data_completeness: float | None,
    evidence_count: int,
    odds_snapshot: Mapping[str, Any],
    result: Mapping[str, Any],
    created_at: str,
    input_envelope: Mapping[str, Any] | None = None,
) -> bool:
    """Compatibility helper: reserve first, then finalize the same designated run."""
    designated = reserve_council_run(
        conn,run_id=run_id,event_id=event_id,team1=team1,team2=team2,
        sport_key=sport_key,competition=competition,kickoff_utc=kickoff_utc,
        as_of_utc=as_of_utc,horizon_hours=horizon_hours,odds_snapshot=odds_snapshot,
        created_at=created_at,
    )
    if not designated:
        return False
    finalize_council_run(
        conn,run_id=run_id,as_of_utc=as_of_utc,context_hash=context_hash,data_completeness=data_completeness,
        evidence_count=evidence_count,input_envelope=input_envelope or {},result=result,
        finished_at=created_at,
    )
    return True


def pending_designated_runs(conn: sqlite3.Connection, *, now_utc: str) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """SELECT r.* FROM council_runs r
               LEFT JOIN council_match_results m ON m.event_id=r.event_id
               WHERE r.is_designated=1 AND r.kickoff_utc<=? AND m.event_id IS NULL
               ORDER BY r.kickoff_utc""",
            (now_utc,),
        ).fetchall()
    ]


def recent_designated_runs(
    conn: sqlite3.Connection, *, now_utc: str, since_utc: str
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in conn.execute(
            """SELECT r.* FROM council_runs r
               WHERE r.is_designated=1 AND r.kickoff_utc<=? AND r.kickoff_utc>=?
               ORDER BY r.kickoff_utc""",
            (now_utc, since_utc),
        ).fetchall()
    ]


def mark_resolution_status(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    status: str,
    detail: str,
    attempted_at: str,
) -> None:
    with conn:
        conn.execute(
            """INSERT INTO council_resolution_status(
                 event_id,status,attempt_count,detail,last_attempt_at
               ) VALUES(?,?,1,?,?)
               ON CONFLICT(event_id) DO UPDATE SET
                 status=excluded.status,attempt_count=attempt_count+1,
                 detail=excluded.detail,last_attempt_at=excluded.last_attempt_at""",
            (event_id, status, _redact(detail), attempted_at),
        )


def save_match_result(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    home_goals: int,
    away_goals: int,
    source: str,
    settled_at: str,
    provider_updated_at: str | None = None,
    score_basis: str = "REGULATION_ONLY_LEAGUE_FINAL",
) -> str:
    actual_1x2 = "TEAM1" if home_goals > away_goals else "TEAM2" if away_goals > home_goals else "DRAW"
    actual_totals = "OVER" if home_goals + away_goals > 2.5 else "UNDER"
    previous = conn.execute(
        "SELECT home_goals,away_goals,provider_updated_at FROM council_match_results WHERE event_id=?",
        (event_id,),
    ).fetchone()
    values = (
        event_id,int(home_goals),int(away_goals),actual_1x2,actual_totals,source,
        score_basis,provider_updated_at,settled_at,
    )
    with conn:
        conn.execute(
            """INSERT INTO council_match_results(
                 event_id,home_goals,away_goals,actual_1x2,actual_totals_2_5,source,
                 score_basis,provider_updated_at,settled_at
               ) VALUES(?,?,?,?,?,?,?,?,?)
               ON CONFLICT(event_id) DO UPDATE SET
                 home_goals=excluded.home_goals,away_goals=excluded.away_goals,
                 actual_1x2=excluded.actual_1x2,actual_totals_2_5=excluded.actual_totals_2_5,
                 source=excluded.source,score_basis=excluded.score_basis,
                 provider_updated_at=excluded.provider_updated_at,
                 settled_at=excluded.settled_at""",
            values,
        )
        conn.execute(
            """INSERT INTO council_resolution_status(
                 event_id,status,attempt_count,detail,last_attempt_at
               ) VALUES(?,'RESOLVED',1,'exact provider event id',?)
               ON CONFLICT(event_id) DO UPDATE SET
                 status='RESOLVED',attempt_count=attempt_count+1,
                 detail='exact provider event id',last_attempt_at=excluded.last_attempt_at""",
            (event_id, settled_at),
        )
    if previous is None:
        return "inserted"
    if (
        int(previous["home_goals"]),int(previous["away_goals"]),previous["provider_updated_at"]
    ) == (int(home_goals),int(away_goals),provider_updated_at):
        return "unchanged"
    return "updated"


def evaluation_rows(conn: sqlite3.Connection) -> dict[str, list[dict[str, Any]]]:
    consensus = [
        dict(row)
        for row in conn.execute(
            """SELECT r.run_id,r.event_id,r.team1,r.team2,r.kickoff_utc,r.as_of_utc,
                      r.horizon_hours,r.status AS run_status,r.schema_version,r.prompt_version,
                      r.data_completeness,r.evidence_count,c.*,
                      m.home_goals,m.away_goals,m.actual_1x2,m.actual_totals_2_5
               FROM council_runs r
               JOIN council_consensus c ON c.run_id=r.run_id
               LEFT JOIN council_match_results m ON m.event_id=r.event_id
               WHERE r.is_designated=1 ORDER BY r.kickoff_utc,c.stage"""
        ).fetchall()
    ]
    members = [
        dict(row)
        for row in conn.execute(
            """SELECT r.run_id,r.event_id,r.team1,r.team2,r.kickoff_utc,r.as_of_utc,
                      r.horizon_hours,r.status AS run_status,r.schema_version,r.prompt_version,
                      f.*,m.home_goals,m.away_goals,m.actual_1x2,m.actual_totals_2_5
               FROM council_runs r
               JOIN council_member_forecasts f ON f.run_id=r.run_id
               LEFT JOIN council_match_results m ON m.event_id=r.event_id
               WHERE r.is_designated=1 ORDER BY r.kickoff_utc,f.round_no,f.member"""
        ).fetchall()
    ]
    return {"consensus": consensus, "members": members}


def store_status(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute(
        """SELECT COUNT(*) AS attempts,
                  COALESCE(SUM(is_designated=1),0) AS designated,
                  COALESCE(SUM(status='COMPLETE'),0) AS complete,
                  COALESCE(SUM(status='RUNNING'),0) AS running,
                  COALESCE(SUM(status!='RUNNING'),0) AS terminal
           FROM council_runs"""
    ).fetchone()
    settled = int(conn.execute("SELECT COUNT(*) FROM council_match_results").fetchone()[0])
    designated_settled = int(
        conn.execute(
            """SELECT COUNT(*) FROM council_runs r JOIN council_match_results m
               ON m.event_id=r.event_id WHERE r.is_designated=1"""
        ).fetchone()[0]
    )
    status = {**dict(row), "settled_events": settled, "designated_settled": designated_settled}
    status["resolution_expired"] = int(
        conn.execute(
            "SELECT COUNT(*) FROM council_resolution_status WHERE status='UNRESOLVED_EXPIRED'"
        ).fetchone()[0]
    )
    status["quorum_completion_rate"] = (
        round(int(status["complete"]) / int(status["designated"]), 4)
        if status["designated"] else None
    )
    status["resolution_coverage"] = (
        round(designated_settled / int(status["designated"]), 4)
        if status["designated"] else None
    )
    return status
