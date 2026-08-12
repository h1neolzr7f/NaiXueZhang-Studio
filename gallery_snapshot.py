"""Verifiable, credential-free snapshots for a local Gallery."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import secrets
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Callable, Iterator

from atomic_io import atomic_write_text


SNAPSHOT_VERSION = 1

# Maintenance-mode lock: while this file exists in the data directory the
# intake persist path and crawler start must refuse writes (snapshot restore
# swaps the database and images tree underneath them).
MAINTENANCE_LOCK_NAME = ".maintenance_lock"


def maintenance_lock_path(data_dir: Path) -> Path:
    return Path(data_dir) / MAINTENANCE_LOCK_NAME


def maintenance_mode_active(data_dir: Path) -> bool:
    return maintenance_lock_path(data_dir).is_file()


@contextmanager
def maintenance_mode(data_dir: Path) -> Iterator[Path]:
    """Hold the cross-process maintenance lock for one restore operation."""

    path = maintenance_lock_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        path,
        json.dumps(
            {
                "pid": os.getpid(),
                "created_at": datetime.now(timezone.utc).isoformat(
                    timespec="seconds"
                ),
            }
        )
        + "\n",
    )
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _auto_stop_crawler() -> dict[str, object]:
    """Best-effort crawler shutdown used when no explicit stopper is injected."""

    try:
        import crawler_control

        return dict(crawler_control.stop_pixiv_crawler_processes())
    except Exception as exc:  # pragma: no cover - defensive: never block backups
        return {"error": type(exc).__name__}

# 备份保留策略：每次创建快照后，仅保留 backups 目录里最新的 N 份。
BACKUP_RETENTION = 10
# 只匹配本工具自己创建的命名（pixiv-nai-gallery-<utc 时间戳>.zip），
# 用户的其他文件一律不动。
BACKUP_NAME_RE = re.compile(r"^pixiv-nai-gallery-\d{8}T\d{6}Z\.zip$")


def prune_backups(backups_dir: Path, keep: int = BACKUP_RETENTION) -> list[Path]:
    """Delete older backup zips under ``backups_dir``, keeping the newest ``keep``.

    Only files matching :data:`BACKUP_NAME_RE` are considered. Returns the
    list of deleted paths (newest-first ordering of the survivors is kept).
    """

    backups_dir = Path(backups_dir)
    if keep < 1 or not backups_dir.is_dir():
        return []
    candidates = [
        path
        for path in backups_dir.iterdir()
        if path.is_file() and BACKUP_NAME_RE.match(path.name)
    ]
    candidates.sort(key=lambda path: (path.stat().st_mtime, path.name), reverse=True)
    removed: list[Path] = []
    for stale in candidates[keep:]:
        try:
            stale.unlink()
        except OSError:
            continue
        removed.append(stale)
    return removed


def _sha256_stream(stream: BinaryIO) -> str:
    digest = hashlib.sha256()
    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
        digest.update(chunk)
    return digest.hexdigest()


class GallerySnapshotManager:
    """Create and verify one portable Gallery Snapshot."""

    def __init__(
        self,
        database_path: Path,
        images_dir: Path,
        *,
        crawler_stopper: Callable[[], dict[str, object]] | None = None,
    ) -> None:
        self.database_path = Path(database_path).resolve()
        self.images_dir = Path(images_dir).resolve()
        # Stopping the crawler before create/restore keeps the DB copy (T0)
        # and the image pack (T1) from observing a half-written intake.
        self._crawler_stopper = crawler_stopper or _auto_stop_crawler

    def _stop_crawler(self) -> dict[str, object]:
        try:
            return dict(self._crawler_stopper())
        except Exception as exc:  # never block a backup on control-plane errors
            return {"error": type(exc).__name__}

    def create(self, destination: Path) -> dict[str, object]:
        if not self.database_path.is_file():
            raise FileNotFoundError(self.database_path)
        stopped = self._stop_crawler()
        destination = Path(destination).resolve()
        try:
            destination.relative_to(self.images_dir)
        except ValueError:
            pass
        else:
            raise ValueError("snapshot destination must not be inside served images")
        destination.parent.mkdir(parents=True, exist_ok=True)
        temporary_zip = destination.with_suffix(
            destination.suffix + f".{secrets.token_hex(6)}.tmp"
        )
        entries: list[dict[str, object]] = []
        asset_files = 0
        try:
            with tempfile.TemporaryDirectory(
                prefix="gallery-snapshot-", dir=destination.parent
            ) as temporary_dir:
                database_copy = Path(temporary_dir) / "gallery.db"
                with closing(sqlite3.connect(self.database_path)) as source:
                    with closing(sqlite3.connect(database_copy)) as target:
                        source.backup(target)
                entries.append(self._file_entry(database_copy, "gallery.db"))
                image_entries: list[tuple[Path, str]] = []
                if self.images_dir.is_dir():
                    for source in sorted(self.images_dir.rglob("*")):
                        if not source.is_file() or source.is_symlink():
                            continue
                        relative = source.relative_to(self.images_dir).as_posix()
                        image_entries.append((source, f"images/{relative}"))
                        entries.append(self._file_entry(source, f"images/{relative}"))
                        asset_files += 1
                manifest = {
                    "version": SNAPSHOT_VERSION,
                    "created_at": datetime.now(timezone.utc).isoformat(
                        timespec="seconds"
                    ),
                    "files": entries,
                }
                with zipfile.ZipFile(
                    temporary_zip,
                    mode="w",
                    compression=zipfile.ZIP_DEFLATED,
                    compresslevel=6,
                ) as archive:
                    archive.write(database_copy, "gallery.db")
                    for source, name in image_entries:
                        archive.write(source, name)
                    archive.writestr(
                        "manifest.json",
                        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                    )
            os.replace(temporary_zip, destination)
        finally:
            temporary_zip.unlink(missing_ok=True)
        return {
            "ok": True,
            "path": str(destination),
            "asset_files": asset_files,
            "files": len(entries),
            "bytes": destination.stat().st_size,
            "crawler_stopped": stopped,
        }

    def verify(self, snapshot: Path) -> dict[str, object]:
        snapshot = Path(snapshot).resolve()
        with zipfile.ZipFile(snapshot) as archive:
            infos = archive.infolist()
            names = [info.filename for info in infos if not info.is_dir()]
            for name in names:
                pure = PurePosixPath(name)
                if pure.is_absolute() or ".." in pure.parts or "\\" in name:
                    raise ValueError("snapshot contains an unsafe path")
            if "manifest.json" not in names:
                raise ValueError("snapshot manifest is missing")
            manifest = json.loads(archive.read("manifest.json"))
            if int(manifest.get("version") or 0) != SNAPSHOT_VERSION:
                raise ValueError("unsupported snapshot version")
            files = manifest.get("files")
            if not isinstance(files, list):
                raise ValueError("snapshot manifest files are invalid")
            expected = {str(item.get("path") or ""): item for item in files}
            if not expected or set(names) != set(expected) | {"manifest.json"}:
                raise ValueError("snapshot file inventory does not match manifest")
            for name, item in expected.items():
                with archive.open(name) as stream:
                    actual_hash = _sha256_stream(stream)
                if actual_hash != str(item.get("sha256") or ""):
                    raise ValueError(f"snapshot hash mismatch: {name}")
                if archive.getinfo(name).file_size != int(item.get("bytes") or -1):
                    raise ValueError(f"snapshot size mismatch: {name}")
            with tempfile.TemporaryDirectory(prefix="gallery-snapshot-verify-") as temp:
                database_copy = Path(temp) / "gallery.db"
                database_copy.write_bytes(archive.read("gallery.db"))
                with closing(sqlite3.connect(database_copy)) as connection:
                    integrity = str(
                        connection.execute("PRAGMA integrity_check").fetchone()[0]
                    )
            if integrity != "ok":
                raise ValueError(f"snapshot database integrity failed: {integrity}")
        return {
            "ok": True,
            "database_integrity": integrity,
            "files": len(expected),
            "asset_files": sum(1 for name in expected if name.startswith("images/")),
        }

    def restore(self, snapshot: Path, *, confirm: bool = False) -> dict[str, object]:
        if confirm is not True:
            raise PermissionError("snapshot restore requires explicit confirmation")
        verification = self.verify(snapshot)
        stopped = self._stop_crawler()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        # Block intake/crawler writes for the whole swap; the lock is removed
        # even when the restore fails and rolls back.
        with maintenance_mode(self.database_path.parent):
            return self._restore_locked(snapshot, verification, stopped)

    def _restore_locked(
        self,
        snapshot: Path,
        verification: dict[str, object],
        stopped: dict[str, object],
    ) -> dict[str, object]:
        with tempfile.TemporaryDirectory(
            prefix="gallery-restore-", dir=self.database_path.parent
        ) as temporary_dir:
            workspace = Path(temporary_dir)
            extracted = workspace / "snapshot"
            extracted.mkdir()
            with zipfile.ZipFile(Path(snapshot).resolve()) as archive:
                archive.extractall(extracted)
            staged_database = extracted / "gallery.db"
            staged_images = extracted / "images"
            staged_images.mkdir(exist_ok=True)
            rescue_database = workspace / "rescue.db"
            if self.database_path.is_file():
                with closing(sqlite3.connect(self.database_path)) as source:
                    with closing(sqlite3.connect(rescue_database)) as target:
                        source.backup(target)
            rescue_images = workspace / "rescue-images"
            images_moved = False
            database_replaced = False
            new_images_installed = False
            try:
                if self.images_dir.exists():
                    os.replace(self.images_dir, rescue_images)
                    images_moved = True
                with closing(sqlite3.connect(staged_database)) as source:
                    with closing(sqlite3.connect(self.database_path)) as target:
                        source.backup(target)
                database_replaced = True
                os.replace(staged_images, self.images_dir)
                new_images_installed = True
            except Exception:
                if new_images_installed and self.images_dir.exists():
                    shutil.rmtree(self.images_dir)
                if database_replaced and rescue_database.is_file():
                    with closing(sqlite3.connect(rescue_database)) as source:
                        with closing(sqlite3.connect(self.database_path)) as target:
                            source.backup(target)
                if images_moved and rescue_images.exists():
                    os.replace(rescue_images, self.images_dir)
                raise
        return {
            "ok": True,
            "database_integrity": verification["database_integrity"],
            "asset_files": verification["asset_files"],
            "crawler_stopped": stopped,
        }

    @staticmethod
    def _file_entry(source: Path, archive_path: str) -> dict[str, object]:
        with source.open("rb") as stream:
            digest = _sha256_stream(stream)
        return {
            "path": archive_path,
            "bytes": source.stat().st_size,
            "sha256": digest,
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pixiv NAI Gallery snapshot tool")
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("create")
    create.add_argument("destination", nargs="?", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("snapshot", type=Path)
    restore = commands.add_parser("restore")
    restore.add_argument("snapshot", type=Path)
    restore.add_argument("--confirm", action="store_true")
    args = parser.parse_args()
    root = args.root.resolve()
    manager = GallerySnapshotManager(root / "data" / "aitag.db", root / "data" / "images")
    if args.command == "create":
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        destination = args.destination or (
            root / "backups" / f"pixiv-nai-gallery-{timestamp}.zip"
        )
        result = manager.create(destination)
        pruned = prune_backups(destination.parent)
        if pruned:
            result["pruned_backups"] = [str(path) for path in pruned]
    elif args.command == "verify":
        result = manager.verify(args.snapshot)
    else:
        result = manager.restore(args.snapshot, confirm=args.confirm)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
