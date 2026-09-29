"""Build a dependency-free production renderer from the local v2/v3/v4 layers.

The generated module keeps only standard-library imports and exposes the same
``render`` and ``render_football`` contract as ``web_ui_v4``.  The build is
deterministic for a given set of source files and can be verified with
``--check`` without touching the output.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import os
import tempfile
from pathlib import Path


HERE = Path(__file__).resolve().parent
V2 = HERE / "web_ui_v2.py"
V3 = HERE / "web_ui_v3.py"
V4 = HERE / "web_ui_v4.py"
OUTPUT = HERE / "web_ui_v4_standalone.py"

_ALLOWED_IMPORT_ROOTS = {
    "__future__",
    "datetime",
    "html",
    "json",
    "math",
    "re",
    "time",
}


def _source(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _body_without_prelude(path: Path) -> list[ast.stmt]:
    body = ast.parse(_source(path), filename=str(path)).body
    result: list[ast.stmt] = []
    for index, node in enumerate(body):
        if index == 0 and isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
            continue
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            continue
        result.append(node)
    return result


def _assigned_names(node: ast.stmt) -> set[str]:
    if not isinstance(node, (ast.Assign, ast.AnnAssign)):
        return set()
    targets = node.targets if isinstance(node, ast.Assign) else [node.target]
    return {target.id for target in targets if isinstance(target, ast.Name)}


class _ResolveBase(ast.NodeTransformer):
    """Turn ``base.NAME`` references in v3 into local ``NAME`` references."""

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        node = self.generic_visit(node)
        if isinstance(node.value, ast.Name) and node.value.id == "base":
            return ast.copy_location(ast.Name(id=node.attr, ctx=node.ctx), node)
        return node


def _dotted_name(node: ast.AST) -> str | None:
    parts: list[str] = []
    current = node
    while isinstance(current, ast.Attribute):
        parts.append(current.attr)
        current = current.value
    if not isinstance(current, ast.Name):
        return None
    parts.append(current.id)
    return ".".join(reversed(parts))


class _ResolveV4(ast.NodeTransformer):
    """Resolve the two constants inherited by v4 from its local parents."""

    _MAPPING = {
        "v3.CS2_HEAD_V3": "CS2_HEAD_V3",
        "v3.base.HEAD": "HEAD",
    }

    def visit_Attribute(self, node: ast.Attribute) -> ast.expr:
        replacement = self._MAPPING.get(_dotted_name(node))
        if replacement:
            return ast.copy_location(ast.Name(id=replacement, ctx=node.ctx), node)
        return self.generic_visit(node)


def _validate_module(tree: ast.Module) -> None:
    unresolved = sorted({node.id for node in ast.walk(tree) if isinstance(node, ast.Name) and node.id in {"base", "v3"}})
    if unresolved:
        raise RuntimeError(f"unresolved local module aliases: {', '.join(unresolved)}")

    imported_roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_roots.add(node.module.split(".", 1)[0])
    unexpected = sorted(imported_roots - _ALLOWED_IMPORT_ROOTS)
    if unexpected:
        raise RuntimeError(f"non-stdlib imports in standalone artifact: {', '.join(unexpected)}")

    functions = {node.name for node in tree.body if isinstance(node, ast.FunctionDef)}
    missing = {"render", "render_football"} - functions
    if missing:
        raise RuntimeError(f"missing production entry points: {', '.join(sorted(missing))}")


def build_source() -> str:
    v2_body = _body_without_prelude(V2)

    # v3 only aliases its parent's renderers at the end.  Preserve the v2
    # football renderer explicitly and omit those runtime-module aliases.
    v3_body = [
        node
        for node in _body_without_prelude(V3)
        if not (_assigned_names(node) & {"render_football", "render_v2"})
    ]
    v3_body = [_ResolveBase().visit(node) for node in v3_body]

    wanted_v4 = {"V4_OVERRIDES", "CS2_HEAD_V4", "FOOTBALL_V4_OVERRIDES", "FOOTBALL_HEAD_V4"}
    v4_body = [node for node in _body_without_prelude(V4) if _assigned_names(node) & wanted_v4]
    v4_body = [_ResolveV4().visit(node) for node in v4_body]

    prelude = ast.parse(
        '"""Generated standalone Atlantic Broadcast v4 renderer. Do not edit by hand."""\n'
        "from __future__ import annotations\n"
        "import html\nimport json\nimport math\nimport re\nimport time\n"
        "from datetime import date, timedelta\n"
    ).body
    preserve_football = ast.parse("_render_football_v2 = render_football\n").body
    preserve_cs2 = ast.parse("_render_cs2_v3 = render\n").body
    final_renderers = ast.parse(
        "def render(data: dict) -> str:\n"
        "    return _render_cs2_v3(data).replace(CS2_HEAD_V3, CS2_HEAD_V4, 1)\n\n"
        "def render_football(data: dict) -> str:\n"
        "    return _render_football_v2(data).replace(HEAD, FOOTBALL_HEAD_V4, 1)\n"
    ).body

    tree = ast.Module(
        body=prelude + v2_body + preserve_football + v3_body + preserve_cs2 + v4_body + final_renderers,
        type_ignores=[],
    )
    ast.fix_missing_locations(tree)
    _validate_module(tree)

    hashes = " ".join(
        f"{path.name}={hashlib.sha256(_source(path).encode('utf-8')).hexdigest()}"
        for path in (V2, V3, V4)
    )
    generated = f"# Source SHA256: {hashes}\n" + ast.unparse(tree) + "\n"
    compile(generated, str(OUTPUT), "exec")
    return generated


def _atomic_write(path: Path, content: str) -> None:
    handle, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="fail if the generated artifact is missing or stale")
    args = parser.parse_args()
    expected = build_source()
    byte_count = len(expected.encode("utf-8"))
    if args.check:
        if not OUTPUT.exists() or OUTPUT.read_text(encoding="utf-8") != expected:
            parser.error(f"{OUTPUT.name} is missing or stale; run this script without --check")
        print(f"ok: {OUTPUT.name} is current ({byte_count} bytes)")
        return 0
    _atomic_write(OUTPUT, expected)
    print(f"built: {OUTPUT} ({byte_count} bytes)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
