"""Build a bounded, self-consistent Gallery Work sample for a release.

The source gallery stays read-only.  The output contains a fresh SQLite
database plus only the image files referenced by that database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from db import Database  # noqa: E402
from db_compression import decompress_if_needed  # noqa: E402


BLOCKED_SAMPLE_TERMS = (
    "r-18",
    "r18",
    "18禁",
    "nsfw",
    "nude",
    "naked",
    "sex",
    "rape",
    "guro",
    "loli",
    "shota",
    "ロリ",
    "ショタ",
    "裸",
    "性交",
    "凌辱",
)
PRIVATE_STATE_KEYS = {
    "last_search_page",
    "last_detail_id",
    "last_preview_id",
    "last_error",
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _is_safe_metadata(row: sqlite3.Row) -> bool:
    text = " ".join(
        str(row[key] or "")
        for key in ("title", "caption", "tags")
    ).casefold()
    return not any(term.casefold() in text for term in BLOCKED_SAMPLE_TERMS)


def _normalize_image_relative(raw: Any) -> str | None:
    text = str(raw or "").strip().replace("\\", "/")
    if not text:
        return None
    if text.startswith("/data/"):
        text = text[6:]
    elif text.startswith("data/"):
        text = text[5:]
    text = text.lstrip("/")
    relative = Path(text)
    if relative.is_absolute() or not relative.parts:
        return None
    if relative.parts[0].lower() == "nai":
        relative = Path("images", *relative.parts)
    elif relative.parts[0].lower() != "images":
        return None
    if any(part in {"", ".", ".."} for part in relative.parts):
        return None
    return Path("images", *relative.parts[1:]).as_posix()


def _build_image_file_index(source_data: Path) -> dict[str, tuple[Path, int]]:
    data_root = source_data.resolve()
    images_root = (data_root / "images").resolve()
    index: dict[str, tuple[Path, int]] = {}
    if not images_root.is_dir():
        return index
    pending = [images_root]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                    continue
                if not entry.is_file(follow_symlinks=False):
                    continue
                path = Path(entry.path)
                relative = path.relative_to(images_root)
                canonical = Path("images", *relative.parts).as_posix()
                index[canonical] = (path, entry.stat(follow_symlinks=False).st_size)
    return index


def _insert_rows(
    connection: sqlite3.Connection,
    table: str,
    rows: Iterable[dict[str, Any]],
) -> None:
    columns = [
        str(row[1])
        for row in connection.execute(f"PRAGMA table_info({table})")
    ]
    values = list(rows)
    if not values:
        return
    placeholders = ",".join("?" for _ in columns)
    names = ",".join(columns)
    connection.executemany(
        f"INSERT INTO {table}({names}) VALUES ({placeholders})",
        [[item.get(column) for column in columns] for item in values],
    )


def _candidate_groups(
    source: sqlite3.Connection,
    file_index: dict[str, tuple[Path, int]],
    *,
    max_work_bytes: int,
    allow_unfiltered_content: bool = False,
) -> list[dict[str, Any]]:
    works = {
        int(row["id"]): dict(row)
        for row in source.execute(
            """
            SELECT id, title, caption, tags, create_date, image_count, preview_path
            FROM works
            WHERE LOWER(TRIM(COALESCE(ai_type, ''))) = 'nai'
              AND COALESCE(
                    CASE WHEN json_valid(list_json)
                         THEN json_extract(list_json, '$.source')
                         ELSE '' END,
                    ''
                  ) = 'pixiv-direct'
            ORDER BY create_date DESC, id DESC
            """
        )
        if allow_unfiltered_content or _is_safe_metadata(row)
    }
    images_by_work: dict[int, list[tuple[int, str, Path, int]]] = defaultdict(list)
    for row in source.execute(
        """
        SELECT work_id, page_index, local_path FROM work_images
        WHERE downloaded = 1
          AND TRIM(COALESCE(local_path, '')) <> ''
        ORDER BY work_id, page_index
        """
    ):
        work_id = int(row["work_id"])
        if work_id not in works:
            continue
        relative = _normalize_image_relative(row["local_path"])
        indexed = file_index.get(relative or "")
        if relative is None or indexed is None:
            continue
        path, size = indexed
        images_by_work[work_id].append((int(row["page_index"]), relative, path, size))

    groups: list[dict[str, Any]] = []
    for work_id, work_row in works.items():
        images = images_by_work.get(work_id) or []
        expected = max(1, int(work_row["image_count"] or 0))
        if len(images) < expected:
            continue
        images = images[:expected]
        unique_files = {relative: (path, size) for _, relative, path, size in images}
        total_bytes = sum(size for _, size in unique_files.values())
        if total_bytes <= 0 or total_bytes > max_work_bytes:
            continue
        preview_relative = _normalize_image_relative(work_row.get("preview_path"))
        preview = file_index.get(preview_relative or "")
        if preview_relative is not None and preview is not None:
            unique_files.setdefault(preview_relative, preview)
        groups.append(
            {
                "work": work_row,
                "page_indexes": [page_index for page_index, *_ in images],
                "preview_path": preview_relative if preview is not None else images[0][1],
                "files": unique_files,
                "bytes": sum(size for _, size in unique_files.values()),
            }
        )
    return groups


def _hydrate_selected_groups(
    source: sqlite3.Connection,
    selected: list[dict[str, Any]],
) -> None:
    for group in selected:
        work_id = int(group["work"]["id"])
        work_row = source.execute("SELECT * FROM works WHERE id = ?", (work_id,)).fetchone()
        if work_row is None:
            raise RuntimeError(f"selected work disappeared from source snapshot: {work_id}")
        work = dict(work_row)
        work["image_count"] = len(group["page_indexes"])
        work["preview_path"] = group["preview_path"]
        work["preview_downloaded"] = 1
        placeholders = ",".join("?" for _ in group["page_indexes"])
        rows = source.execute(
            f"SELECT * FROM work_images WHERE work_id = ? AND page_index IN ({placeholders}) ORDER BY page_index",
            [work_id, *group["page_indexes"]],
        ).fetchall()
        images = []
        for row in rows:
            item = dict(row)
            relative = _normalize_image_relative(item.get("local_path"))
            if relative not in group["files"]:
                raise RuntimeError(f"selected image path changed during snapshot: {work_id}:{item.get('page_index')}")
            item["local_path"] = relative
            images.append(item)
        if len(images) != len(group["page_indexes"]):
            raise RuntimeError(f"selected image rows changed during snapshot: {work_id}")
        group["work"] = work
        group["images"] = images


def _select_groups(
    groups: list[dict[str, Any]],
    *,
    target_bytes: int,
    minimum_bytes: int,
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    selected_paths: set[str] = set()
    total = 0
    soft_limit = int(target_bytes * 1.08)
    for group in groups:
        new_bytes = sum(
            size
            for relative, (_, size) in group["files"].items()
            if relative not in selected_paths
        )
        if selected and total + new_bytes > soft_limit:
            continue
        selected.append(group)
        selected_paths.update(group["files"])
        total += new_bytes
        if total >= target_bytes:
            break
    if total < minimum_bytes:
        raise RuntimeError(
            f"not enough complete, content-filtered local images: "
            f"{total / 1048576:.1f} MiB available, "
            f"{minimum_bytes / 1048576:.1f} MiB required"
        )
    return selected


def build_sample_gallery(
    source_db: Path,
    source_data: Path,
    output_data: Path,
    *,
    target_bytes: int,
    minimum_bytes: int | None = None,
    max_work_bytes: int = 64 * 1024 * 1024,
    allow_unfiltered_content: bool = False,
) -> dict[str, Any]:
    source_db = source_db.resolve()
    source_data = source_data.resolve()
    output_data = output_data.resolve()
    minimum_bytes = minimum_bytes if minimum_bytes is not None else int(target_bytes * 0.75)
    if not source_db.is_file():
        raise FileNotFoundError(f"source database not found: {source_db}")
    if output_data == source_data or source_data in output_data.parents:
        raise ValueError("sample output must be outside the source data directory")
    output_images = output_data / "images"
    output_db = output_data / "aitag.db"
    if output_db.exists() or (output_images.exists() and any(output_images.iterdir())):
        raise FileExistsError("sample output data directory is not empty")
    output_images.mkdir(parents=True, exist_ok=True)

    file_index = _build_image_file_index(source_data)
    uri = f"file:{source_db.as_posix()}?mode=ro"
    source = sqlite3.connect(uri, uri=True, timeout=30.0)
    source.row_factory = sqlite3.Row
    try:
        groups = _candidate_groups(
            source,
            file_index,
            max_work_bytes=max_work_bytes,
            allow_unfiltered_content=allow_unfiltered_content,
        )
        selected = _select_groups(
            groups,
            target_bytes=target_bytes,
            minimum_bytes=minimum_bytes,
        )
        _hydrate_selected_groups(source, selected)
    finally:
        source.close()

    temp_db = output_data / f".aitag.sample.{os.getpid()}.{time.time_ns()}.db"
    database = Database(temp_db)
    try:
        connection = database.conn
        works = [group["work"] for group in selected]
        images = [item for group in selected for item in group["images"]]
        _insert_rows(connection, "works", works)
        _insert_rows(connection, "work_images", images)
        connection.executemany(
            "INSERT INTO works_fts(work_id, title, caption, tags, ai_type) VALUES (?, ?, ?, ?, ?)",
            [
                (
                    work["id"],
                    work.get("title"),
                    work.get("caption"),
                    work.get("tags"),
                    work.get("ai_type"),
                )
                for work in works
            ],
        )
        prompts_by_work: dict[int, list[str]] = defaultdict(list)
        for item in images:
            prompt = str(
                item.get("prompt_text")
                or decompress_if_needed(item.get("ai_json"))
                or ""
            ).strip()
            if not prompt:
                continue
            work_id = int(item["work_id"])
            prompts_by_work[work_id].append(prompt)
            connection.execute(
                "INSERT INTO prompt_fts(work_id, prompt_text) VALUES (?, ?)",
                (work_id, prompt),
            )
        connection.executemany(
            "INSERT INTO prompt_work_fts(work_id, prompt_text) VALUES (?, ?)",
            [(work_id, "\n".join(prompts)) for work_id, prompts in prompts_by_work.items()],
        )
        connection.executemany(
            "INSERT INTO crawl_state(key, value) VALUES (?, ?)",
            [
                ("prompt_work_fts_ready", "1"),
                ("release_sample", "1"),
            ],
        )
        connection.commit()
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"sample database integrity check failed: {integrity}")
    finally:
        database.close()

    cleanup = sqlite3.connect(temp_db)
    try:
        cleanup.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        cleanup.execute("PRAGMA journal_mode=DELETE")
        cleanup.execute("VACUUM")
    finally:
        cleanup.close()
    os.replace(temp_db, output_db)

    copied: dict[str, dict[str, Any]] = {}
    for group in selected:
        for relative, (source_path, expected_size) in group["files"].items():
            if relative in copied:
                continue
            destination = output_data / Path(relative)
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source_path, destination)
            if destination.stat().st_size != expected_size:
                raise RuntimeError(f"sample image size changed during copy: {source_path}")
            copied[relative] = {
                "bytes": expected_size,
                "sha256": _sha256(destination),
            }

    verify = sqlite3.connect(f"file:{output_db.as_posix()}?mode=ro", uri=True)
    verify.row_factory = sqlite3.Row
    try:
        missing = []
        for row in verify.execute(
            "SELECT work_id, page_index, local_path FROM work_images WHERE downloaded = 1"
        ):
            path = output_data / str(row["local_path"])
            if not path.is_file():
                missing.append(f"{row['work_id']}:{row['page_index']}:{row['local_path']}")
        if missing:
            raise RuntimeError(f"sample database references missing images: {missing[:5]}")
        counts = {
            "works": int(verify.execute("SELECT COUNT(*) FROM works").fetchone()[0]),
            "work_images": int(verify.execute("SELECT COUNT(*) FROM work_images").fetchone()[0]),
            "works_fts": int(verify.execute("SELECT COUNT(*) FROM works_fts").fetchone()[0]),
        }
    finally:
        verify.close()
    if counts["works"] != counts["works_fts"]:
        raise RuntimeError("sample FTS work count does not match works")

    image_bytes = sum(item["bytes"] for item in copied.values())
    manifest = {
        "schema_version": 1,
        "kind": "beginner_sample_gallery",
        "content_policy": "metadata-filtered-non-explicit",
        "source_state_copied": sorted(set(PRIVATE_STATE_KEYS) & {"release_sample"}),
        "target_bytes": int(target_bytes),
        "image_bytes": image_bytes,
        "database_bytes": output_db.stat().st_size,
        "counts": counts,
        "work_ids": [int(group["work"]["id"]) for group in selected],
        "database_sha256": _sha256(output_db),
        "files": copied,
    }
    (output_data / "sample_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source-db", type=Path, required=True)
    parser.add_argument("--source-data", type=Path, required=True)
    parser.add_argument("--output-data", type=Path, required=True)
    parser.add_argument("--target-mib", type=float, default=384.0)
    parser.add_argument("--minimum-mib", type=float)
    parser.add_argument(
        "--allow-unfiltered-content",
        action="store_true",
        help="include otherwise content-filtered works in a private test bundle",
    )
    args = parser.parse_args()
    target = max(1, int(args.target_mib * 1024 * 1024))
    minimum = (
        max(1, int(args.minimum_mib * 1024 * 1024))
        if args.minimum_mib is not None
        else None
    )
    manifest = build_sample_gallery(
        args.source_db,
        args.source_data,
        args.output_data,
        target_bytes=target,
        minimum_bytes=minimum,
        allow_unfiltered_content=args.allow_unfiltered_content,
    )
    print(
        json.dumps(
            {
                "ok": True,
                "works": manifest["counts"]["works"],
                "images": manifest["counts"]["work_images"],
                "image_mib": round(manifest["image_bytes"] / 1048576, 2),
                "database_mib": round(manifest["database_bytes"] / 1048576, 2),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
