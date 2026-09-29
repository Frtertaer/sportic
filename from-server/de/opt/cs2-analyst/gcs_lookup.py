"""Shared lookup into g-cs.ru results (full CS2 coverage) for the resolvers. find() returns the
finished match for a (team1, team2) pair, or None. Diacritic-folded uppercase matching."""
import json, unicodedata
from pathlib import Path

ROOT = Path(__file__).resolve().parent
_RES = ROOT / "data" / "gcs_results.json"


def _norm(s):
    s = unicodedata.normalize("NFKD", str(s or ""))
    s = "".join(c for c in s if not unicodedata.combining(c))
    return "".join(c for c in s if c.isalnum()).upper()


def load():
    try:
        return json.loads(_RES.read_text(encoding="utf-8")).get("matches", [])
    except Exception:
        return []


def find(t1, t2, results=None):
    if results is None:
        results = load()
    n1, n2 = _norm(t1), _norm(t2)
    if not (n1 and n2):
        return None
    for r in results:
        g1, g2 = _norm(r["t1"]), _norm(r["t2"])
        if not (g1 and g2):
            continue
        if (any(x and (x in g1 or g1 in x) for x in (n1, n2)) and
                any(x and (x in g2 or g2 in x) for x in (n1, n2))):
            return {"t1": r["t1"], "t2": r["t2"], "s1": r["s1"], "s2": r["s2"],
                    "g1n": g1, "g2n": g2, "winner_n": _norm(r["winner"])}
    return None
