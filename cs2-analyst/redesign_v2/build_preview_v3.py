from __future__ import annotations

import json
import time
from pathlib import Path

import web_ui_v3


ROOT = Path(__file__).resolve().parent
state = json.loads((ROOT / "sample_state.json").read_text(encoding="utf-8"))
state["ts"] = int(time.time())
output = web_ui_v3.render(state)
if any(token in output.lower() for token in ("grok:", "<grok", "render_inline_citation")):
    raise RuntimeError("provider markup leaked")
target = ROOT / "preview_v3.html"
target.write_text(output, encoding="utf-8")
print(f"preview={target} bytes={target.stat().st_size}")

static_target = ROOT / "preview_v3_static.html"
static_target.write_text(output.replace('<meta http-equiv="refresh" content="30">\n', ""), encoding="utf-8")
print(f"static_preview={static_target} bytes={static_target.stat().st_size}")
