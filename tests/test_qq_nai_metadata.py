from __future__ import annotations

import gzip
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
from PIL import Image
from PIL.PngImagePlugin import PngInfo

import crawler_qq
from nai_image_metadata import PARSER_VERSION, parse_nai_image
from db import Database
from qq_gallery_ingest import (
    QQIdentity,
    import_parsed_nai,
    load_existing_work_id_index,
    repair_interrupted_upgrade_duplicates,
    rebuild_group_index,
    revalidate_existing_batch,
    resolve_qq_identity,
)


def _write_png(path: Path, metadata: dict[str, str] | None = None) -> None:
    image = Image.new("RGBA", (64, 64), (40, 80, 120, 255))
    pnginfo = PngInfo()
    for key, value in (metadata or {}).items():
        pnginfo.add_text(key, value)
    image.save(path, pnginfo=pnginfo)


def _write_stealth_nai_png(path: Path) -> None:
    payload = {
        "Software": "NovelAI",
        "Source": "NovelAI Diffusion V4.5",
        "Description": "1girl, surtr (arknights), cinematic lighting",
        "Comment": json.dumps({"seed": 42, "uc": "lowres"}),
    }
    packed = gzip.compress(json.dumps(payload).encode("utf-8"))
    header = b"stealth_pngcomp" + (len(packed) * 8).to_bytes(4, "big") + packed
    bits = np.unpackbits(np.frombuffer(header, dtype=np.uint8))
    width = 64
    height = max(64, (len(bits) + width - 1) // width)
    rgba = np.full((height, width, 4), 128, dtype=np.uint8)
    flat = np.full(width * height, 255, dtype=np.uint8)
    flat[: len(bits)] = (flat[: len(bits)] & 0xFE) | bits
    rgba[..., 3] = flat.reshape((width, height)).T
    Image.fromarray(rgba, "RGBA").save(path)


class QQNAIMetadataTests(unittest.TestCase):
    def test_standard_novelai_png_is_accepted_with_prompt_and_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nai.png"
            _write_png(
                path,
                {
                    "Software": "NovelAI",
                    "Source": "NovelAI Diffusion V4.5",
                    "Description": "1girl, texas (arknights), night city",
                    "Comment": json.dumps(
                        {
                            "seed": 123,
                            "uc": "lowres, bad anatomy",
                            "steps": 28,
                        }
                    ),
                },
            )
            result = parse_nai_image(path)

        self.assertTrue(result.accepted)
        self.assertEqual(result.parser_version, PARSER_VERSION)
        self.assertEqual(result.metadata_source, "embedded")
        self.assertIn("texas", result.prompt)
        self.assertEqual(result.negative_prompt, "lowres, bad anatomy")
        self.assertEqual(result.seed, 123)
        self.assertEqual(result.model, "NovelAI Diffusion V4.5")

    def test_official_stealth_pngcomp_metadata_is_accepted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stealth.png"
            _write_stealth_nai_png(path)
            result = parse_nai_image(path)

        self.assertTrue(result.accepted)
        self.assertEqual(result.metadata_source, "stealth_pngcomp")
        self.assertIn("surtr", result.prompt)
        self.assertEqual(result.seed, 42)

    def test_comfy_metadata_wins_over_spoofed_novelai_fields(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "comfy.png"
            _write_png(
                path,
                {
                    "Software": "NovelAI",
                    "Source": "NovelAI Diffusion V4.5",
                    "Description": "spoofed prompt",
                    "prompt": json.dumps({"1": {"class_type": "KSampler"}}),
                    "workflow": json.dumps({"nodes": [{"type": "KSampler"}]}),
                },
            )
            result = parse_nai_image(path)

        self.assertFalse(result.accepted)
        self.assertEqual(result.reason, "comfy_metadata")

    def test_plain_or_corrupt_image_is_rejected_without_throwing(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / "plain.png"
            broken = Path(tmp) / "broken.png"
            _write_png(plain)
            broken.write_bytes(b"not an image")

            plain_result = parse_nai_image(plain)
            broken_result = parse_nai_image(broken)

        self.assertFalse(plain_result.accepted)
        self.assertEqual(plain_result.reason, "nai_metadata_missing")
        self.assertFalse(broken_result.accepted)
        self.assertEqual(broken_result.reason, "unreadable_image")

    def test_group_account_layout_and_legacy_account_layout_are_stable(self) -> None:
        structured = resolve_qq_identity(
            Path("明日方舟群/10001_小明/image.png"),
            layout="group_account",
            default_group_key="legacy",
            default_group_label="历史未分组",
        )
        self.assertEqual(structured.group_key, "明日方舟群")
        self.assertEqual(structured.account_key, "10001_小明")

        legacy = resolve_qq_identity(
            Path("ailunpo/10_photo_focus/image.png"),
            layout="account",
            default_group_key="legacy",
            default_group_label="历史未分组",
        )
        self.assertEqual(legacy.group_key, "legacy")
        self.assertEqual(legacy.group_label, "历史未分组")
        self.assertEqual(legacy.account_key, "ailunpo")

    def test_import_persists_prompt_model_metadata_and_nested_identity(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "source.png"
            images = root / "images"
            _write_png(
                src,
                {
                    "Software": "NovelAI",
                    "Source": "NovelAI Diffusion V4.5",
                    "Description": "1girl, lappland (arknights)",
                    "Comment": json.dumps({"seed": 99, "uc": "lowres"}),
                },
            )
            parsed = parse_nai_image(src)
            identity = QQIdentity("group-1", "测试群", "10001", "小明")
            with patch("qq_gallery_ingest.upsert_local_work") as upsert:
                work_id = import_parsed_nai(
                    src=src,
                    identity=identity,
                    parsed=parsed,
                    images_root=images,
                    hardlink=False,
                )

            kwargs = upsert.call_args.kwargs
            self.assertGreater(work_id, 0)
            self.assertEqual(kwargs["prompt_text"], parsed.prompt)
            self.assertEqual(kwargs["model"], "NovelAI Diffusion V4.5")
            self.assertEqual(kwargs["extra"]["group_key"], "group-1")
            self.assertEqual(kwargs["account_key"], "10001")
            self.assertEqual(json.loads(kwargs["ai_json"])["seed"], 99)
            self.assertTrue(
                (
                    images
                    / "group-1"
                    / "10001"
                    / f"{work_id}_p0.png"
                ).is_file()
            )

    def test_existing_identity_index_reuses_unambiguous_legacy_work_id(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "gallery.db")
            try:
                item = {
                    "id": 987654321,
                    "AI_type": "NAI",
                    "title": "same-file",
                    "group_key": "legacy",
                    "group_label": "历史未分组",
                    "account_key": "10001",
                    "account_label": "小明",
                }
                db.upsert_list_item(item, "2026-07-27T00:00:00")
                db.conn.commit()
                index = load_existing_work_id_index(db)
                key = ("legacy", "10001", "same-file")
                self.assertEqual(index[key], 987654321)
            finally:
                db.close()

    def test_interrupted_upgrade_repair_requires_matching_parsed_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "gallery.db")
            try:
                db.conn.execute(
                    """
                    CREATE TABLE qq_ingest_files(
                        source_id TEXT PRIMARY KEY,
                        source_name TEXT NOT NULL,
                        file_size INTEGER NOT NULL,
                        mtime_ns INTEGER NOT NULL,
                        parser_version TEXT NOT NULL,
                        status TEXT NOT NULL,
                        reason TEXT NOT NULL,
                        group_key TEXT NOT NULL,
                        group_label TEXT NOT NULL,
                        account_key TEXT NOT NULL,
                        account_label TEXT NOT NULL,
                        work_id INTEGER,
                        updated_at TEXT NOT NULL
                    )
                    """
                )
                for work_id, preview, source_file in (
                    (10, "10001/10_p0.png", None),
                    (20, "legacy/10001/20_p0.png", "same-file.png"),
                ):
                    item = {
                        "id": work_id,
                        "AI_type": "NAI",
                        "title": "same-file",
                        "group_key": "legacy",
                        "group_label": "历史未分组",
                        "account_key": "10001",
                        "account_label": "小明",
                    }
                    if source_file:
                        item["source_file"] = source_file
                    db.upsert_list_item(item, "2026-07-27T00:00:00")
                    db.conn.commit()
                    db.save_detail(
                        work_id,
                        {
                            "work": item,
                            "images": [
                                {
                                    "id": work_id,
                                    "file_name": Path(preview).name,
                                    "prompt_text": "1girl, amiya",
                                    "model": "NovelAI Diffusion V4.5",
                                }
                            ],
                        },
                        preview,
                        True,
                        "2026-07-27T00:00:01",
                    )
                db.conn.execute(
                    """
                    INSERT INTO qq_ingest_files VALUES(
                        'source', 'same-file.png', 1, 1, ?, 'accepted',
                        'accepted', 'legacy', '历史未分组', '10001',
                        '小明', 20, '2026-07-27T00:00:02Z'
                    )
                    """,
                    (PARSER_VERSION,),
                )
                db.conn.commit()

                result = repair_interrupted_upgrade_duplicates(db)

                self.assertEqual(result, {"detected": 1, "removed": 1})
                self.assertEqual(
                    db.conn.execute("SELECT id FROM works").fetchall()[0][0],
                    10,
                )
                self.assertEqual(
                    db.conn.execute(
                        "SELECT work_id FROM qq_ingest_files"
                    ).fetchone()[0],
                    10,
                )
            finally:
                db.close()

    def test_group_and_account_filters_do_not_mix_same_account_across_groups(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "gallery.db")
            try:
                fixtures = [
                    (1, "g1", "群一", "same", "账号甲"),
                    (2, "g1", "群一", "other", "账号乙"),
                    (3, "g2", "群二", "same", "账号甲"),
                ]
                for work_id, group_key, group_label, account_key, account_label in fixtures:
                    db.upsert_list_item(
                        {
                            "id": work_id,
                            "AI_type": "NAI",
                            "title": f"work-{work_id}",
                            "group_key": group_key,
                            "group_label": group_label,
                            "account_key": account_key,
                            "account_label": account_label,
                        },
                        "2026-07-27T00:00:00",
                    )
                db.conn.commit()

                group_result = db.search_works(group="group:g1", page_size=10)
                account_result = db.search_works(
                    group="account:g1:same",
                    page_size=10,
                )
                index = rebuild_group_index(db)

                self.assertEqual(len(group_result["items"]), 2)
                self.assertEqual(len(account_result["items"]), 1)
                self.assertEqual(account_result["items"][0]["id"], 1)
                self.assertEqual(
                    [item["kind"] for item in index],
                    ["group", "account", "account", "group", "account"],
                )
            finally:
                db.close()

    def test_crawler_rejects_plain_and_comfy_then_skips_unchanged_receipts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            watch = root / "watch"
            account = watch / "10001"
            comfy_dir = account / "ComfyUI"
            comfy_dir.mkdir(parents=True)
            accepted = account / "accepted.png"
            plain = account / "plain.png"
            comfy = comfy_dir / "comfy.png"
            _write_png(
                accepted,
                {
                    "Software": "NovelAI",
                    "Source": "NovelAI Diffusion V4.5",
                    "Description": "1girl, amiya (arknights)",
                    "Comment": json.dumps({"seed": 7}),
                },
            )
            _write_png(plain)
            _write_png(
                comfy,
                {
                    "prompt": json.dumps(
                        {"1": {"class_type": "KSampler"}}
                    ),
                    "workflow": json.dumps({"nodes": []}),
                },
            )
            db = Database(root / "qq.db")
            spec = SimpleNamespace(images_dir=root / "images")
            config = {
                "crawlers": {
                    "qqgroup": {
                        "enabled": True,
                        "watch_dirs": [str(watch)],
                        "layout": "account",
                        "default_group_key": "legacy",
                        "default_group_label": "历史未分组",
                        "hardlink": False,
                    }
                }
            }
            try:
                with (
                    patch.object(crawler_qq, "get_db", return_value=db),
                    patch.object(
                        crawler_qq,
                        "ensure_gallery_dirs",
                        return_value=spec,
                    ),
                    patch.object(
                        crawler_qq,
                        "import_one",
                        return_value=123,
                    ) as importer,
                ):
                    first = crawler_qq.crawl_once(config, root=root)
                    second = crawler_qq.crawl_once(config, root=root)

                self.assertEqual(first["scanned"], 3)
                self.assertEqual(first["imported"], 1)
                self.assertEqual(first["rejected"], 2)
                self.assertEqual(
                    first["rejected_by_reason"],
                    {"comfy_path": 1, "nai_metadata_missing": 1},
                )
                self.assertEqual(importer.call_count, 1)
                self.assertEqual(second["skipped_unchanged"], 3)
                self.assertEqual(second["imported"], 0)
                self.assertTrue(
                    (root / "logs" / "qq-rejections-latest.json").is_file()
                )
            finally:
                db.close()

    def test_legacy_catalog_revalidation_hides_invalid_but_keeps_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            images = root / "images"
            account = images / "legacy-account"
            account.mkdir(parents=True)
            valid = account / "1.png"
            plain = account / "2.png"
            _write_png(
                valid,
                {
                    "Software": "NovelAI",
                    "Source": "Stable Diffusion XL C1E1DE52",
                    "Description": "1girl, skadi (arknights)",
                    "Comment": json.dumps({"seed": 88}),
                },
            )
            _write_png(plain)
            db = Database(root / "gallery.db")
            try:
                for work_id, rel in (
                    (1, "legacy-account/1.png"),
                    (2, "legacy-account/2.png"),
                ):
                    item = {
                        "id": work_id,
                        "AI_type": "NAI",
                        "title": f"legacy-{work_id}",
                        "account_key": "legacy-account",
                        "account_label": "旧账号",
                    }
                    db.upsert_list_item(item, "2026-07-27T00:00:00")
                    db.conn.commit()
                    db.save_detail(
                        work_id,
                        {
                            "work": item,
                            "images": [
                                {
                                    "id": work_id,
                                    "file_name": Path(rel).name,
                                    "prompt_text": "",
                                }
                            ],
                        },
                        rel,
                        True,
                        "2026-07-27T00:00:01",
                    )

                result = revalidate_existing_batch(
                    db,
                    images,
                    limit=10,
                    apply=True,
                )

                self.assertEqual(result["accepted"], 1)
                self.assertEqual(result["rejected"], 1)
                self.assertEqual(result["remaining"], 0)
                self.assertEqual(db.count_works(), 1)
                detail = db.get_work_detail(1)
                self.assertIn(
                    "skadi",
                    detail["images"][0]["prompt_text"],
                )
                self.assertEqual(
                    detail["work"]["group_label"],
                    "历史未分组",
                )
                self.assertTrue(plain.is_file())
                self.assertEqual(result["source_files_deleted"], 0)
            finally:
                db.close()


if __name__ == "__main__":
    unittest.main()
