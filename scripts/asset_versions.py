"""Content-hashed cache-bust versions for web assets.

Single source of truth for ``?v=`` stamps. Every local asset reference of the
form ``/assets/...?v=...`` or ``./...?v=...`` / ``../...?v=...`` inside
``web/**/*.html`` and ``web/**/*.js`` is rewritten to ``?v=<sha256[:10]>`` of
the referenced file's current content. References whose target does not exist
on disk (external CDN, runtime-generated URLs) are left untouched.

Local ``.js`` / ``.css`` references that have NO query string at all (same
URL prefixes) are treated as stale too: apply mode inserts ``?v=<hash>``
before the closing quote, and ``--check`` reports them and exits 1.
Runtime-templated URLs (containing ``${``), URLs carrying other query
params or fragments, and external URLs are never matched.

Usage:
    python scripts/asset_versions.py           # rewrite stale refs in place
    python scripts/asset_versions.py --check   # exit 1 if any ref is stale

The script is idempotent: running it twice in a row produces no changes. It
also refreshes ``tests/regression_manifest.json`` (consumed by
``scripts/run_regression_guards.ps1`` for the browser probe). The manifest
keeps the legacy ``app_js_version`` keys and additionally records an
``entry_versions`` map (filename -> hash) for every top-level ``web/*.js``
referenced by ``web/index.html``.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WEB = ROOT / "web"
MANIFEST = ROOT / "tests" / "regression_manifest.json"

REF_RE = re.compile(
    r"(?P<quote>[\"'`])(?P<url>(?:/assets/|\./|\.\./)[^\"'`?#]+?)\?v=[A-Za-z0-9_-]+"
)
# 无查询串的本地 .js/.css 引用：扩展名后必须紧跟闭合引号，因此已带 ?v=
# 或其他查询参数/fragment 的引用不会被误判；含 ${ 的模板串另行跳过。
UNSTAMPED_RE = re.compile(
    r"(?P<quote>[\"'`])(?P<url>(?:/assets/|\./|\.\./)[^\"'`?#]+?\.(?:js|css))(?P<endquote>[\"'`])"
)
HASH_LEN = 10


@dataclass(frozen=True)
class Ref:
    holder: Path  # file containing the reference
    url: str  # url without query string
    version: str | None  # current ?v= value; None when the ref is unstamped
    span: tuple[int, int]  # match span in holder source

    @property
    def target(self) -> Path | None:
        if self.url.startswith("/assets/"):
            candidate = (WEB / self.url[len("/assets/") :]).resolve()
        else:
            candidate = (self.holder.parent / self.url).resolve()
        try:
            candidate.relative_to(WEB.resolve())
        except ValueError:
            return None
        return candidate


def _is_backup(path: Path) -> bool:
    return any("backup" in part or ".bak-" in part for part in path.parts)


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


_STAMP_RE = re.compile(r"\?v=[A-Za-z0-9_-]+")


def asset_digest(path: Path) -> str:
    # 哈希前先把内容里的 ?v= 戳全部归一化：否则给 A 重打戳会改变 A 的内容，
    # 进而改变 A 自己的哈希，循环依赖的模块图永远无法收敛。
    # 副作用是戳本身不参与哈希——可接受，戳是派生数据。
    # read_text 统一换行符，CRLF/LF 差异不影响版本。
    text = path.read_text(encoding="utf-8", errors="ignore")
    normalized = _STAMP_RE.sub("?v=", text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def asset_version(path: Path) -> str:
    return asset_digest(path)[:HASH_LEN]


def iter_refs(holder: Path) -> list[Ref]:
    source = holder.read_text(encoding="utf-8", errors="ignore")
    refs: list[Ref] = []
    for match in REF_RE.finditer(source):
        url = match.group("url")
        version = match.group(0).rsplit("?v=", 1)[1]
        refs.append(
            Ref(
                holder=holder,
                url=url,
                version=version,
                span=(match.start(), match.end()),
            )
        )
    for match in UNSTAMPED_RE.finditer(source):
        url = match.group("url")
        if "${" in url:
            continue  # runtime-templated URL, not a plain literal
        refs.append(
            Ref(
                holder=holder,
                url=url,
                version=None,
                span=(match.start(), match.end()),
            )
        )
    refs.sort(key=lambda ref: ref.span)
    return refs


def iter_holders() -> list[Path]:
    holders = sorted(WEB.rglob("*.html")) + sorted(WEB.rglob("*.js"))
    return [p for p in holders if not _is_backup(p)]


def collect_stale() -> list[tuple[Ref, str]]:
    """Return (ref, expected_version) for every ref whose stamp is stale."""
    stale: list[tuple[Ref, str]] = []
    for holder in iter_holders():
        for ref in iter_refs(holder):
            target = ref.target
            if target is None or not target.is_file():
                continue
            expected = asset_version(target)
            if ref.version != expected:
                stale.append((ref, expected))
    return stale


def apply_updates() -> list[str]:
    changed: list[str] = []
    for holder in iter_holders():
        source = holder.read_text(encoding="utf-8", errors="ignore")
        refs = iter_refs(holder)
        if not refs:
            continue
        out: list[str] = []
        cursor = 0
        dirty = False
        for ref in refs:
            start, end = ref.span
            out.append(source[cursor:start])
            segment = source[start:end]
            target = ref.target
            if target is not None and target.is_file():
                expected = asset_version(target)
                if ref.version != expected:
                    if ref.version is None:
                        # 无戳引用：在闭合引号前插入 ?v=<hash>
                        segment = segment[:-1] + f"?v={expected}" + segment[-1]
                    else:
                        segment = segment[: segment.rfind("?v=")] + f"?v={expected}"
                    dirty = True
            out.append(segment)
            cursor = end
        out.append(source[cursor:])
        if dirty:
            new_source = "".join(out)
            holder.write_text(new_source, encoding="utf-8", newline="")
            changed.append(_rel(holder))
    return changed


ENTRY_SCRIPT_RE = re.compile(r"/assets/([^/\"'`?#]+\.js)")


def entry_scripts() -> list[Path]:
    """Top-level ``web/*.js`` referenced by ``web/index.html`` (entry scripts)."""
    index = WEB / "index.html"
    if not index.is_file():
        return []
    names: list[str] = []
    for name in ENTRY_SCRIPT_RE.findall(
        index.read_text(encoding="utf-8", errors="ignore")
    ):
        if name not in names and (WEB / name).is_file():
            names.append(name)
    return [WEB / name for name in names]


def refresh_manifest() -> None:
    app_js = WEB / "app.js"
    if not app_js.is_file() or not MANIFEST.parent.is_dir():
        return
    digest = asset_digest(app_js)
    MANIFEST.write_text(
        json.dumps(
            {
                "app_js_version": digest[:HASH_LEN],
                "app_js_sha256_12": digest[:12],
                "entry_versions": {
                    script.name: asset_version(script) for script in entry_scripts()
                },
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="only report stale refs; exit 1 when any exist",
    )
    args = parser.parse_args()

    if args.check:
        stale = collect_stale()
        if stale:
            print(f"{len(stale)} stale asset version(s):")
            for ref, expected in stale:
                current = ref.version if ref.version is not None else "(unstamped)"
                print(f"  {_rel(ref.holder)}: {ref.url} ?v={current} -> {expected}")
            print("run `python scripts/asset_versions.py` to fix")
            return 1
        print("all asset versions match content hashes")
        return 0

    changed = apply_updates()
    refresh_manifest()
    if changed:
        print(f"updated {len(changed)} file(s):")
        for rel in changed:
            print(f"  {rel}")
    else:
        print("all asset versions already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
