from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def load_renderer():
    spec = importlib.util.spec_from_file_location("web_ui_v2", ROOT / "web_ui_v2.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("could not load web_ui_v2.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> None:
    renderer = load_renderer()
    state = json.loads((ROOT / "sample_state.json").read_text(encoding="utf-8"))
    state["ts"] = int(time.time())
    output = renderer.render(state)
    if "grok:render" in output.lower() or "render_inline_citation" in output.lower():
        raise RuntimeError("provider markup leaked into rendered HTML")
    if "style=\"--pa:50%\"" not in output:
        raise RuntimeError("probability width contract missing")
    target = ROOT / "preview.html"
    target.write_text(output, encoding="utf-8")
    print(f"preview={target} bytes={target.stat().st_size}")

    football_output = renderer.render_football(state)
    if "</html>" not in football_output:
        raise RuntimeError("football renderer returned incomplete HTML")
    football_target = ROOT / "football_preview.html"
    football_target.write_text(football_output, encoding="utf-8")
    print(f"football_preview={football_target} bytes={football_target.stat().st_size}")


if __name__ == "__main__":
    main()
