"""Tunable CS2 betting params — the knobs the self-learning loop (cs2_autolearn.py) adjusts.
Modules read via get(key, default); if params.json is missing/bad they fall back to the default,
so the live system NEVER breaks on a bad config. Auto-learn writes proposals+applied changes here."""
from __future__ import annotations
import json
from pathlib import Path

_PATH = Path(__file__).resolve().parent / "data" / "cs2_params.json"
_cache = None
_mtime = None

DEFAULTS = {
    "totals_min_edge": 0.06,      # totals value MIN_EDGE (raised 0.04->0.06, council)
    "totals_max_edge": 0.12,
    "map_calib_shrink": 0.6,      # calibrate_map_p: 0.5 + clamp((p-0.5)*shrink, -cap, +cap)
    "map_calib_cap": 0.10,
    "series_gate_p": 0.53,        # skip series value bets below this model_p
    "prematch_edge_lo": 0.07,     # prematch VALUE edge band
    "prematch_edge_hi": 0.15,
    "antisweep_min_edge": 0.06,
}


def _load() -> dict:
    global _cache, _mtime
    try:
        m = _PATH.stat().st_mtime
        if _cache is None or m != _mtime:
            _cache = json.loads(_PATH.read_text(encoding="utf-8"))
            _mtime = m
    except Exception:  # noqa: BLE001 — missing/corrupt config -> defaults
        _cache = {}
    return _cache or {}


def get(key: str, default=None):
    """Return the live tuned value, or the module-provided default / DEFAULTS."""
    v = _load().get(key)
    if v is None:
        return DEFAULTS.get(key, default) if default is None else default
    return v


def all_params() -> dict:
    out = dict(DEFAULTS)
    out.update(_load())
    return out


def set_param(key: str, value, reason: str = "") -> None:
    """Persist a tuned value + an audit trail (used only by cs2_autolearn)."""
    from datetime import datetime, timezone
    cur = {}
    try:
        cur = json.loads(_PATH.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        cur = {}
    cur[key] = value
    hist = cur.setdefault("_history", [])
    hist.append({"key": key, "value": value, "reason": reason, "at": datetime.now(timezone.utc).isoformat()})
    cur["_history"] = hist[-100:]
    _PATH.parent.mkdir(parents=True, exist_ok=True)
    _PATH.write_text(json.dumps(cur, ensure_ascii=False, indent=2), encoding="utf-8")
