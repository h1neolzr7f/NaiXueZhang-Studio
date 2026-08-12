"""Maintenance orchestration for the local Gallery Asset Store."""

from __future__ import annotations

import json
import os
import secrets
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from gallery_asset_store import GalleryAssetStore
from gallery_snapshot import GallerySnapshotManager


class GalleryMaintenance:
    """Expose bounded maintenance operations through one local Interface."""

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = Path(data_dir).resolve()
        self.database_path = self.data_dir / "aitag.db"
        self.images_dir = self.data_dir / "images"
        self.backups_dir = self.data_dir.parent / "backups"
        self.assets = GalleryAssetStore(self.images_dir)
        self.snapshots = GallerySnapshotManager(self.database_path, self.images_dir)

    @staticmethod
    def _normalize_image_relative(path: str | None) -> str:
        """Strip legacy ``images/`` or ``data/images/`` prefixes from stored paths."""

        from paths import normalize_image_relative

        return normalize_image_relative(path)

    def rebuild_thumbnails(self, *, workers: int = 4) -> dict[str, int]:
        # Rows whose local_path already lives under _thumbs/ (thumbnail-only
        # pages) ARE the stored preview; deriving a thumbnail from them would
        # produce a nested _thumbs/_thumbs/... path.
        all_references = self._image_references()
        references = [
            item
            for item in all_references
            if not item[2].split("/", 1)[0] == "_thumbs"
        ]
        skipped_thumbs = len(all_references) - len(references)

        def render(item: tuple[int, int, str]) -> tuple[int, int, str, bool] | None:
            work_id, page_index, relative = item
            expected = (
                self.images_dir
                / "_thumbs"
                / Path(relative).parent
                / f"{Path(relative).stem}.webp"
            )
            existed = expected.is_file()
            try:
                thumbnail = self.assets.ensure_thumbnail(relative)
            except (FileNotFoundError, OSError, ValueError):
                return None
            return work_id, page_index, thumbnail, not existed

        rendered: list[tuple[int, int, str, bool]] = []
        with ThreadPoolExecutor(
            max_workers=max(1, min(int(workers), 8)),
            thread_name_prefix="gallery-thumbnail",
        ) as executor:
            for result in executor.map(render, references):
                if result is not None:
                    rendered.append(result)
        first_pages = {
            work_id: thumbnail
            for work_id, page_index, thumbnail, _created in rendered
            if page_index == 0
        }
        if first_pages:
            with closing(sqlite3.connect(self.database_path)) as connection:
                connection.executemany(
                    "UPDATE works SET preview_path=?, preview_downloaded=1 WHERE id=?",
                    [(path, work_id) for work_id, path in first_pages.items()],
                )
                connection.commit()
        return {
            "total": len(references),
            "rendered": len(rendered),
            "created": sum(1 for *_prefix, created in rendered if created),
            "failed": len(references) - len(rendered),
            "skipped_thumbs": skipped_thumbs,
        }

    def rebuild_nai_tag_index(self) -> dict[str, int]:
        from db import Database

        with Database(self.database_path) as database:
            return {"works": int(database.rebuild_nai_tag_index())}

    def storage_status(self, *, quota_bytes: int | None = None) -> dict[str, object]:
        quota = self._configured_quota() if quota_bytes is None else max(0, int(quota_bytes))
        status = dict(self.assets.storage_status(quota_bytes=quota))
        status["database_bytes"] = (
            self.database_path.stat().st_size if self.database_path.is_file() else 0
        )
        return status

    def reconcile(self, *, delete: bool = False) -> dict[str, int]:
        references = {relative for _work, _page, relative in self._image_references()}
        return self.assets.reconcile(references, delete=delete)

    def permanent_skip_report(self) -> dict[str, object]:
        """Audit works that permanently failed NAI collection (no gallery entry)."""

        from pixiv_nai_intake import PERMANENT_REJECT_REASONS

        if not self.database_path.is_file():
            return {
                "permanent_skip_works": 0,
                "permanent_skip_pages": 0,
                "reasons": {},
                "sample_work_ids": [],
            }
        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT work_id, status, reason
                FROM pixiv_nai_receipts
                ORDER BY work_id, source_page_index
                """
            ).fetchall()
            gallery_ids = {
                int(row[0])
                for row in connection.execute("SELECT id FROM works").fetchall()
            }
        by_work: dict[int, list[sqlite3.Row]] = {}
        for row in rows:
            by_work.setdefault(int(row["work_id"]), []).append(row)
        permanent_works = 0
        permanent_pages = 0
        reasons: dict[str, int] = {}
        samples: list[int] = []
        for work_id, page_rows in by_work.items():
            if work_id in gallery_ids:
                continue
            if not page_rows:
                continue
            if any(str(row["status"]) != "rejected" for row in page_rows):
                continue
            page_reasons = [str(row["reason"] or "") for row in page_rows]
            if not page_reasons or any(
                reason not in PERMANENT_REJECT_REASONS for reason in page_reasons
            ):
                continue
            permanent_works += 1
            permanent_pages += len(page_rows)
            for reason in page_reasons:
                reasons[reason] = reasons.get(reason, 0) + 1
            if len(samples) < 20:
                samples.append(work_id)
        return {
            "permanent_skip_works": permanent_works,
            "permanent_skip_pages": permanent_pages,
            "reasons": reasons,
            "sample_work_ids": samples,
        }

    def cleanup_stale_staging(self, *, delete: bool = False) -> dict[str, int]:
        """Remove leftover intake staging files that were never published."""

        staging_dir = self.data_dir / "pixiv_nai_staging"
        if not staging_dir.is_dir():
            return {"staging_files": 0, "staging_bytes": 0, "deleted_files": 0}
        files = [path for path in staging_dir.rglob("*") if path.is_file()]
        total_bytes = sum(path.stat().st_size for path in files)
        deleted = 0
        if delete:
            for path in files:
                path.unlink(missing_ok=True)
                deleted += 1
            for directory in sorted(
                (item for item in staging_dir.rglob("*") if item.is_dir()),
                key=lambda item: len(item.parts),
                reverse=True,
            ):
                try:
                    directory.rmdir()
                except OSError:
                    continue
        return {
            "staging_files": len(files),
            "staging_bytes": total_bytes,
            "deleted_files": deleted if delete else 0,
        }

    def migrate_originals_to_webp(
        self,
        *,
        limit: int = 0,
        dry_run: bool = False,
    ) -> dict[str, int | float]:
        """Re-encode existing PNG/JPEG originals as WebP and rewrite DB paths.

        NAI metadata already lives in the database (ai_json). On-disk originals
        become compact WebP for browsing only.
        """

        from gallery_asset_store import compress_image_for_storage
        import hashlib

        if not self.database_path.is_file():
            return {
                "candidates": 0,
                "migrated": 0,
                "skipped": 0,
                "failed": 0,
                "cleanup_failed": 0,
                "detail_rewritten": 0,
                "bytes_before": 0,
                "bytes_after": 0,
                "bytes_saved": 0,
                "dry_run": int(bool(dry_run)),
            }

        with closing(sqlite3.connect(self.database_path)) as connection:
            connection.row_factory = sqlite3.Row
            rows = connection.execute(
                """
                SELECT work_id, page_index, local_path, source_page_index
                FROM work_images
                WHERE downloaded=1
                  AND TRIM(COALESCE(local_path, '')) <> ''
                ORDER BY work_id, page_index
                """
            ).fetchall()

        candidates = [
            row
            for row in rows
            if Path(str(row["local_path"])).suffix.lower()
            in {".png", ".jpg", ".jpeg"}
        ]
        if limit and limit > 0:
            candidates = candidates[: int(limit)]

        migrated = 0
        skipped = 0
        failed = 0
        cleanup_failed = 0
        bytes_before = 0
        bytes_after = 0
        # work_id -> {old_relative: new_relative}，用于迁移后改写 detail_json 内嵌路径
        migrated_paths: dict[int, dict[str, str]] = {}

        for row in candidates:
            raw_relative = str(row["local_path"]).replace("\\", "/")
            relative = self._normalize_image_relative(raw_relative)
            if not relative:
                failed += 1
                continue
            source = (self.images_dir / relative).resolve()
            try:
                source.relative_to(self.images_dir.resolve())
            except ValueError:
                failed += 1
                continue
            if not source.is_file():
                skipped += 1
                continue
            before = source.stat().st_size
            bytes_before += before
            new_relative = str(Path(relative).with_suffix(".webp").as_posix())
            destination = (self.images_dir / new_relative).resolve()
            if dry_run:
                # Conservative estimate: assume ~45% of original for webp.
                bytes_after += int(before * 0.45)
                migrated += 1
                continue
            staged = destination.with_name(
                f".{destination.stem}.{secrets.token_hex(8)}.migrate.webp"
            )
            rollback_copy = destination.with_name(
                f".{destination.stem}.{secrets.token_hex(8)}.rollback.webp"
            )
            promoted = False
            had_destination = destination.is_file()
            try:
                compress_image_for_storage(
                    source,
                    staged,
                    max_edge=self.assets.original_max_edge,
                    quality=self.assets.original_quality,
                )
                after = staged.stat().st_size
                digest = hashlib.sha256(staged.read_bytes()).hexdigest()
                if had_destination:
                    os.replace(destination, rollback_copy)
                os.replace(staged, destination)
                promoted = True
                thumb_relative = self.assets.ensure_thumbnail(new_relative)
                with closing(sqlite3.connect(self.database_path)) as connection:
                    connection.execute(
                        """
                        UPDATE work_images
                        SET local_path=?, image_path=?, file_name=?, source_sha256=?
                        WHERE work_id=? AND page_index=?
                        """,
                        (
                            new_relative,
                            new_relative,
                            Path(new_relative).name,
                            digest,
                            int(row["work_id"]),
                            int(row["page_index"]),
                        ),
                    )
                    # Normalize both bare and images/-prefixed preview paths.
                    old_preview_variants = {
                        relative,
                        raw_relative,
                        f"images/{relative}",
                        f"data/images/{relative}",
                    }
                    if int(row["page_index"]) == 0:
                        connection.execute(
                            "UPDATE works SET preview_path=?, preview_downloaded=1 WHERE id=?",
                            (thumb_relative, int(row["work_id"])),
                        )
                    else:
                        placeholders = ",".join("?" for _ in old_preview_variants)
                        connection.execute(
                            f"""
                            UPDATE works
                            SET preview_path=?
                            WHERE id=? AND preview_path IN ({placeholders})
                            """,
                            (new_relative, int(row["work_id"]), *old_preview_variants),
                        )
                    connection.execute(
                        """
                        UPDATE pixiv_nai_receipts
                        SET local_path=?, source_sha256=?
                        WHERE work_id=? AND display_page_index=?
                        """,
                        (
                            new_relative,
                            digest,
                            int(row["work_id"]),
                            int(row["page_index"]),
                        ),
                    )
                    connection.commit()
            except Exception:
                failed += 1
                bytes_after += before
                # The database still references the legacy original. Restore any
                # pre-existing WebP and leave that original untouched.
                try:
                    if promoted and destination.is_file():
                        destination.unlink(missing_ok=True)
                    if rollback_copy.is_file():
                        os.replace(rollback_copy, destination)
                except OSError:
                    # The DB continues to reference ``source``, so even a failed
                    # cleanup here cannot create a DB -> missing-file reference.
                    pass
                try:
                    staged.unlink(missing_ok=True)
                except OSError:
                    # An unreferenced staging artifact is preferable to risking
                    # the still-live legacy original during rollback.
                    pass
                continue

            # The DB commit is the ownership boundary. Cleanup is deliberately
            # best-effort: never remove the new DB-referenced WebP after commit.
            if rollback_copy.is_file():
                try:
                    rollback_copy.unlink()
                except OSError:
                    cleanup_failed += 1
            try:
                source.unlink()
            except OSError:
                cleanup_failed += 1
            bytes_after += after + (before if source.is_file() else 0)
            migrated += 1
            migrated_paths.setdefault(int(row["work_id"]), {})[relative] = new_relative

        # detail_json 内嵌的 images[].local_path/image_path/file_name 仍指向旧
        # 后缀；读取侧虽会以 work_images 为准自愈，但存储 blob 不应长期保留
        # 失效路径。文件迁移已全部落库后再做本步，失败不影响已完成的迁移。
        detail_rewritten = 0
        if migrated_paths and not dry_run:
            with closing(sqlite3.connect(self.database_path)) as connection:
                connection.row_factory = sqlite3.Row
                for work_id, path_map in migrated_paths.items():
                    try:
                        if self._rewrite_detail_json_paths(connection, work_id, path_map):
                            detail_rewritten += 1
                    except Exception:
                        # 列缺失或 blob 损坏都不应让维护任务整体失败
                        pass
                connection.commit()

        return {
            "candidates": len(candidates),
            "migrated": migrated,
            "skipped": skipped,
            "failed": failed,
            "cleanup_failed": cleanup_failed,
            "detail_rewritten": detail_rewritten,
            "bytes_before": bytes_before,
            "bytes_after": bytes_after,
            "bytes_saved": max(0, bytes_before - bytes_after),
            "dry_run": int(bool(dry_run)),
        }

    @staticmethod
    def _rewrite_detail_json_paths(
        connection: sqlite3.Connection,
        work_id: int,
        path_map: dict[str, str],
    ) -> bool:
        """Rewrite stale image paths embedded in works.detail_json in place.

        ``path_map`` maps pre-migration relative paths (normalized, no
        ``images/`` prefix) to their new ``.webp`` relatives. Prefix style of
        each stored value is preserved.
        """

        from db_compression import compress_text, decompress_if_needed

        row = connection.execute(
            "SELECT detail_json FROM works WHERE id = ?", (work_id,)
        ).fetchone()
        if not row or not row["detail_json"]:
            return False
        detail = json.loads(decompress_if_needed(row["detail_json"]))
        images = detail.get("images")
        if not isinstance(images, list):
            return False

        def swap(value: object) -> object:
            raw = str(value or "").replace("\\", "/")
            if not raw:
                return value
            relative = raw.lstrip("/")
            prefix = ""
            for known in ("data/images/", "images/"):
                if relative.startswith(known):
                    prefix = known
                    relative = relative[len(known) :]
                    break
            new_relative = path_map.get(relative)
            if new_relative is None:
                return value
            return f"{prefix}{new_relative}"

        changed = False
        for image in images:
            if not isinstance(image, dict):
                continue
            for key in ("local_path", "image_path"):
                if key in image:
                    updated = swap(image.get(key))
                    if updated != image.get(key):
                        image[key] = updated
                        changed = True
            name = str(image.get("file_name") or "")
            if name:
                for old_relative, new_relative in path_map.items():
                    if name == Path(old_relative).name:
                        image["file_name"] = Path(new_relative).name
                        changed = True
                        break
        if not changed:
            return False
        connection.execute(
            "UPDATE works SET detail_json = ? WHERE id = ?",
            (compress_text(json.dumps(detail, ensure_ascii=False)), work_id),
        )
        return True

    def create_snapshot(self) -> dict[str, object]:
        from gallery_snapshot import prune_backups

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        self.backups_dir.mkdir(parents=True, exist_ok=True)
        result = self.snapshots.create(
            self.backups_dir / f"pixiv-nai-gallery-{timestamp}.zip"
        )
        pruned = prune_backups(self.backups_dir)
        if pruned:
            result["pruned_backups"] = [str(path) for path in pruned]
        return result

    def _image_references(self) -> list[tuple[int, int, str]]:
        if not self.database_path.is_file():
            return []
        with closing(sqlite3.connect(self.database_path)) as connection:
            rows = connection.execute(
                "SELECT work_id, COALESCE(page_index, 0), local_path "
                "FROM work_images WHERE downloaded=1 AND TRIM(COALESCE(local_path, ''))<>'' "
                "ORDER BY work_id, page_index"
            ).fetchall()
        return [
            (int(row[0]), int(row[1]), self._normalize_image_relative(str(row[2])))
            for row in rows
            if self._normalize_image_relative(str(row[2]))
        ]

    def _configured_quota(self) -> int:
        config_path = self.data_dir.parent / "config.json"
        try:
            payload = json.loads(config_path.read_text(encoding="utf-8"))
            return max(0, int(payload.get("gallery_storage_quota_bytes") or 0))
        except (OSError, ValueError, TypeError):
            return 0
