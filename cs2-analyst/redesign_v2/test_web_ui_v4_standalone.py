from __future__ import annotations

import ast
import importlib.util
import json
import subprocess
import sys
import time
import unittest
from pathlib import Path

import web_ui_v4


HERE = Path(__file__).resolve().parent
ARTIFACT = HERE / "web_ui_v4_standalone.py"
FIXTURE = HERE / "sample_state.json"


def _load_artifact():
    spec = importlib.util.spec_from_file_location("web_ui_v4_standalone_test", ARTIFACT)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load standalone renderer")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class StandaloneV4Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        subprocess.run(
            [sys.executable, str(HERE / "build_standalone_v4.py"), "--check"],
            cwd=HERE,
            check=True,
            capture_output=True,
            text=True,
        )
        cls.standalone = _load_artifact()
        cls.state = json.loads(FIXTURE.read_text(encoding="utf-8"))
        # Make freshness output deterministic across both renderer calls.
        cls.state["ts"] = time.time() + 30

    def test_has_no_local_runtime_imports_or_aliases(self) -> None:
        tree = ast.parse(ARTIFACT.read_text(encoding="utf-8"), filename=str(ARTIFACT))
        imported: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".", 1)[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".", 1)[0])
        self.assertFalse(imported & {"web_ui_v2", "web_ui_v3", "web_ui_v4"})
        self.assertFalse({node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} & {"base", "v3"})

    def test_cs2_output_is_byte_identical(self) -> None:
        self.assertEqual(web_ui_v4.render(self.state), self.standalone.render(self.state))

    def test_football_output_is_byte_identical(self) -> None:
        self.assertEqual(web_ui_v4.render_football(self.state), self.standalone.render_football(self.state))

    def test_imports_and_renders_in_isolated_python(self) -> None:
        code = r'''import importlib.util, json, pathlib, sys
artifact, fixture = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("isolated_web_ui", artifact)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
state = json.loads(fixture.read_text(encoding="utf-8"))
assert "CS2 ANALYST" in module.render(state)
assert "Футбол" in module.render_football(state)
'''
        subprocess.run(
            [sys.executable, "-I", "-c", code, str(ARTIFACT), str(FIXTURE)],
            cwd=HERE,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    unittest.main()
