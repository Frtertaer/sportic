from __future__ import annotations

import importlib.util
import json
import re
import sys
from pathlib import Path


def load_candidate(path: Path):
    spec = importlib.util.spec_from_file_location("web_ui_candidate", path)
    if spec is None or spec.loader is None:
        raise RuntimeError("candidate import spec failed")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    candidate = Path(sys.argv[1]).resolve()
    ui = load_candidate(candidate)

    sys.path.insert(0, str(candidate.parent))
    import web

    state = web.state()
    cs2 = ui.render(state)
    football = ui.render_football(state)

    assert callable(ui.render) and callable(ui.render_football)
    assert cs2.lstrip().lower().startswith("<!doctype html")
    assert football.lstrip().lower().startswith("<!doctype html")
    for anchor in ("main", "signals", "matches", "upcoming", "accuracy"):
        assert re.search(rf'id=["\']?{anchor}(?:["\'\s>])', cs2), anchor
    assert '<meta http-equiv="refresh" content="30">' in cs2
    assert '--brand:#e8864a' in cs2
    assert '<meta name="theme-color" content="#0b1724">' in cs2
    assert not re.search(r"grok:|<grok|render_inline_citation", cs2, re.IGNORECASE)
    assert "atlantic broadcast v4" not in cs2.lower()
    assert '<main id=main>' in football
    assert 'href="/football"' in football and "aria-current=page" in football
    assert '--bg:#0b1724' in football
    assert '<meta name="theme-color" content="#0b1724">' in football
    assert "football atlantic v4" not in football.lower()
    assert "</html>" in cs2 and "</html>" in football

    print(json.dumps({
        "candidate": str(candidate),
        "state_keys": sorted(state),
        "cs2_bytes": len(cs2.encode("utf-8")),
        "football_bytes": len(football.encode("utf-8")),
        "live": len(state.get("live") or []),
        "board": len(state.get("prematch_board") or []),
    }, ensure_ascii=False))


if __name__ == "__main__":
    main()
