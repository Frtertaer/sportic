from __future__ import annotations

import json
import time
import urllib.request


BASE = "http://127.0.0.1:8920"


def get(path: str) -> bytes:
    last_error: Exception | None = None
    for _ in range(6):
        try:
            with urllib.request.urlopen(BASE + path, timeout=30) as response:
                assert response.status == 200
                return response.read()
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"GET {path} failed") from last_error


api_raw = get("/api")
api = json.loads(api_raw)
assert {"live", "football", "ts"} <= api.keys()

cs2 = get("/").decode("utf-8")
assert "id=signals" in cs2 and "</html>" in cs2
assert "grok:render" not in cs2.lower() and "render_inline_citation" not in cs2.lower()

football = get("/football").decode("utf-8")
assert "<main id=main>" in football and "aria-current=page" in football and "</html>" in football

print(json.dumps({
    "api_ok": True,
    "live": len(api.get("live") or []),
    "board": len(api.get("prematch_board") or []),
    "cs2_bytes": len(cs2.encode("utf-8")),
    "football_bytes": len(football.encode("utf-8")),
}, ensure_ascii=False))
