from __future__ import annotations

import ast
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BACKEND_ROOT = PROJECT_ROOT / "nai_char_modules"
FRONTEND_ROOT = PROJECT_ROOT / "web" / "plugins" / "char-swap"


def _python_import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".", 1)[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            roots.add(node.module.split(".", 1)[0])
    return roots


def _javascript_imports(path: Path) -> set[str]:
    source = path.read_text(encoding="utf-8")
    imports = re.findall(r'\bfrom\s+["\'](\./[^"\']+\.js)(?:\?[^"\']*)?["\']', source)
    imports += re.findall(r'\bimport\s+["\'](\./[^"\']+\.js)(?:\?[^"\']*)?["\']', source)
    return {Path(value).name for value in imports}


def _assert_acyclic(graph: dict[str, set[str]]) -> None:
    visiting: list[str] = []
    visited: set[str] = set()

    def visit(node: str) -> None:
        if node in visiting:
            cycle = visiting[visiting.index(node) :] + [node]
            raise AssertionError(f"char-swap JavaScript import cycle: {' -> '.join(cycle)}")
        if node in visited:
            return
        visiting.append(node)
        for dependency in sorted(graph.get(node, set())):
            if dependency in graph:
                visit(dependency)
        visiting.pop()
        visited.add(node)

    for node in sorted(graph):
        visit(node)


def test_backend_deep_modules_do_not_import_the_compatibility_facade_or_routes() -> None:
    expected = {
        "generation.py",
        "metadata.py",
        "remix.py",
        "sanitization.py",
        "snapshots.py",
        "style.py",
    }
    module_paths = list(BACKEND_ROOT.glob("*.py"))
    assert expected.issubset({path.name for path in module_paths})
    for path in module_paths:
        imports = _python_import_roots(path)
        assert "nai_char" not in imports, f"{path.name} imports the legacy facade"
        assert "routes" not in imports, f"{path.name} imports an HTTP Adapter"
        assert len(path.read_text(encoding="utf-8").splitlines()) <= 600, (
            f"{path.name} has become another monolith; deepen its Interface first"
        )


def test_frontend_modules_have_an_acyclic_import_direction() -> None:
    paths = list(FRONTEND_ROOT.glob("*.js"))
    graph = {path.name: _javascript_imports(path) for path in paths}
    _assert_acyclic(graph)


def test_frontend_orchestrators_cannot_grow_back_into_the_extracted_modules() -> None:
    budgets = {
        "batch.js": {"lines": 925, "bytes": 39_000},
        "panel.js": {"lines": 1_300, "bytes": 50_000},
        "panel_shell.js": {"lines": 700, "bytes": 38_000},
        "presets.js": {"lines": 50, "bytes": 2_000},
    }
    for name, budget in budgets.items():
        path = FRONTEND_ROOT / name
        source = path.read_text(encoding="utf-8")
        assert len(source.splitlines()) <= budget["lines"], (
            f"{name} exceeds its orchestration line budget; extract a deep Module"
        )
        assert len(source.encode("utf-8")) <= budget["bytes"], (
            f"{name} exceeds its orchestration byte budget; extract a deep Module"
        )


def test_extracted_frontend_modules_remain_focused() -> None:
    budgets = {
        "character_references.js": 100,
        "dom_adapter.js": 75,
        "draft_commands.js": 200,
        "reference_modals.js": 575,
        "style_references.js": 125,
        "style_workflows.js": 625,
        "workbench_bridge.js": 50,
    }
    for name, max_lines in budgets.items():
        path = FRONTEND_ROOT / name
        assert path.is_file(), name
        assert len(path.read_text(encoding="utf-8").splitlines()) <= max_lines, (
            f"{name} has become shallow orchestration; split by domain Interface"
        )


def test_frontend_module_graph_cache_stamps_match_content_hashes() -> None:
    # 版本戳是内容哈希（scripts/asset_versions.py 维护）：模块图内每个
    # ?v= 引用都必须等于目标文件的当前内容哈希，过期戳意味着缓存幽灵。
    from scripts.asset_versions import asset_version

    stale: list[str] = []
    for path in FRONTEND_ROOT.glob("*.js"):
        source = path.read_text(encoding="utf-8")
        for url, stamp in re.findall(r'["\'](\./[^"\']+?\.js)\?v=([0-9a-f]+)["\']', source):
            target = (path.parent / url).resolve()
            assert target.is_file(), f"{path.name}: missing {url}"
            expected = asset_version(target)
            if stamp != expected:
                stale.append(f"{path.name}: {url} ?v={stamp} -> {expected}")
    assert not stale, "stale cache stamps:\n" + "\n".join(stale)
