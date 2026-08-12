"""Read-only indexing for derived gallery artifacts.

The post-processing pipeline stores source PNGs and derived outputs next to
each other.  This module owns the naming and lookup rules so backlog scans can
use one stable seam without importing pipeline execution or configuration.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import TypeAlias


ArtifactKind = str
ArtifactIndex: TypeAlias = dict[str, dict[ArtifactKind, list[Path]]]

_DERIVED_SUFFIX_RE = re.compile(r"(_up\d+x(?:_(?:mosaic|clean))?|_mosaic|_clean)$")
_UPSCALE_RE = re.compile(r"_up\d+x$")
_UPSCALE_WITH_METADATA_RE = re.compile(r"_up\d+x_clean$")


def base_stem(stem: str) -> str:
    """Return the source stem for a generated artifact stem."""

    current = str(stem or "")
    while True:
        stripped = _DERIVED_SUFFIX_RE.sub("", current)
        if stripped == current:
            return current
        current = stripped


def artifact_rank(path: Path) -> tuple[int, int, str]:
    """Sort artifacts from the latest pipeline stage to the earliest."""

    name = path.stem
    if name.endswith("_clean") or _UPSCALE_WITH_METADATA_RE.search(name):
        return (3, len(name), name)
    if "_mosaic" in name:
        return (2, len(name), name)
    if _UPSCALE_RE.search(name):
        return (1, len(name), name)
    return (0, len(name), name)


def pipeline_artifacts(root: Path, stem: str) -> list[Path]:
    """List derived PNGs for one source image in pipeline order."""

    artifacts: list[Path] = []
    try:
        for path in root.glob(f"{stem}*.png"):
            if path.stem in {stem, f"{stem}_final"}:
                continue
            artifacts.append(path)
    except OSError:
        return []
    artifacts.sort(key=artifact_rank, reverse=True)
    return artifacts


def build_artifact_index(root: Path) -> ArtifactIndex:
    """Index every derived PNG in one directory pass.

    Missing or temporarily unreadable directories are treated as an empty
    index so status/backlog reads remain safe during cleanup or release work.
    """

    index: ArtifactIndex = {}
    try:
        for path in root.iterdir():
            if not path.is_file() or path.suffix.lower() != ".png":
                continue
            derived_stem = path.stem
            base = base_stem(derived_stem)
            if base == derived_stem:
                continue
            kind = ""
            if derived_stem.endswith("_clean"):
                kind = "clean"
            elif "_mosaic" in derived_stem:
                kind = "mosaic"
            elif _UPSCALE_RE.search(derived_stem):
                kind = "upscale"
            if not kind:
                continue
            index.setdefault(base, {}).setdefault(kind, []).append(path)
    except OSError:
        return index

    for kinds in index.values():
        for matches in kinds.values():
            matches.sort()
    return index


def find_artifact(
    root: Path,
    stem: str,
    kind: ArtifactKind,
    *,
    artifact_index: ArtifactIndex | None = None,
) -> Path | None:
    """Find the newest artifact of ``kind`` for ``stem``."""

    if artifact_index is not None:
        matches = artifact_index.get(stem, {}).get(kind, [])
        return matches[-1] if matches else None

    if kind == "mosaic":
        matches = sorted(
            list(root.glob(f"{stem}_mosaic.png"))
            + list(root.glob(f"{stem}_up*_mosaic.png"))
        )
        return matches[-1] if matches else None
    if kind == "clean":
        matches = sorted(
            list(root.glob(f"{stem}_clean.png"))
            + list(root.glob(f"{stem}_up*_clean.png"))
        )
        return matches[-1] if matches else None
    if kind == "upscale":
        matches = [
            path
            for path in sorted(root.glob(f"{stem}_up*x.png"))
            if "_mosaic" not in path.stem and not path.stem.endswith("_clean")
        ]
        return matches[-1] if matches else None
    return None


__all__ = [
    "ArtifactIndex",
    "ArtifactKind",
    "artifact_rank",
    "base_stem",
    "build_artifact_index",
    "find_artifact",
    "pipeline_artifacts",
]
