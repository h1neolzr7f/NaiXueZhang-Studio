from __future__ import annotations

import argparse
import re
import zipfile
from pathlib import Path


SKIP_DIR_NAMES = {"__pycache__", ".pytest_cache", ".git", "node_modules"}
# Release boundary: the bundle must never carry locally crawled images,
# galleries, databases, credentials, logs or private state.
SKIP_PREFIXES = (
    "data/images",
    "data/galleries",
    "data/generated",
    "data/.cache",
    "data/pixiv_chrome_profile",
    "data/pixiv_chrome_profiles",
    "scripts/logs",
)
BACKUP_NAME_RE = re.compile(r"(?:^|[._-])(?:bak|backup)(?:[._-]|$)", re.IGNORECASE)
SKIP_SUFFIXES = (
    ".db",
    ".db-shm",
    ".db-wal",
    ".local.json",
    ".log",
)


def iter_files(root: Path):
    for path in root.rglob("*"):
        if path.is_file():
            parts = path.parts
            if any(part in SKIP_DIR_NAMES for part in parts):
                continue
            if path.suffix in {".pyc", ".pyo"}:
                continue
            rel = path.relative_to(root).as_posix()
            if any(rel.startswith(prefix) for prefix in SKIP_PREFIXES):
                continue
            if path.suffix.lower() in SKIP_SUFFIXES:
                continue
            if path.name.endswith(".local.json"):
                continue
            # 备份目录（如 web.backup-20260811/）里的文件名字本身可能正常，
            # 必须检查相对路径的每一段，不能只查文件名。注意只查相对段：
            # 源目录本身位于 backup 命名的父目录下不应导致整体排除。
            if any(
                BACKUP_NAME_RE.search(part)
                for part in path.relative_to(root).parts
            ):
                continue
            yield path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source")
    parser.add_argument("zip_path")
    parser.add_argument("--stored", action="store_true", help="store files without compression")
    parser.add_argument("--root-name", help="top-level directory name inside the zip")
    args = parser.parse_args()

    source = Path(args.source).resolve()
    zip_path = Path(args.zip_path).resolve()
    root_name = str(args.root_name or source.name).strip()
    if not root_name or Path(root_name).name != root_name or root_name in {".", ".."}:
        raise SystemExit(f"unsafe zip root name: {root_name!r}")
    compression = zipfile.ZIP_STORED if args.stored else zipfile.ZIP_DEFLATED
    compresslevel = None if args.stored else 1

    if not source.is_dir():
        raise SystemExit(f"source is not a directory: {source}")
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    if zip_path.exists():
        zip_path.unlink()

    files = list(iter_files(source))
    total = len(files)
    with zipfile.ZipFile(
        zip_path,
        "w",
        compression=compression,
        compresslevel=compresslevel,
        allowZip64=True,
    ) as zf:
        for index, path in enumerate(files, 1):
            arcname = root_name + "/" + path.relative_to(source).as_posix()
            zf.write(path, arcname)
            if index % 500 == 0 or index == total:
                print(f"zipped {index}/{total}: {arcname}", flush=True)

    print(f"created {zip_path}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
