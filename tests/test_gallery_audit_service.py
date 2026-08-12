from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

import gallery_audit_service as audit


class GalleryAuditServiceTests(unittest.TestCase):
    def _cover(self, root: str) -> Path:
        path = Path(root) / "cover.webp"
        Image.new("RGB", (768, 1024), (120, 90, 150)).save(path, "WEBP")
        return path

    def test_audit_sends_bounded_thumbnails_and_returns_locatable_findings(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cover = self._cover(temp)
            rows = {"items": [{"id": 7, "title": "样例", "image_count": 2, "thumb_path": "cover"}]}
            lite = {
                "images": [
                    {"page_index": 0, "local_path": "cover"},
                    {"page_index": 1, "local_path": None},
                ]
            }
            response = {
                "summary": "发现一处需要复核的问题。",
                "findings": [
                    {
                        "image_ref": "image_1",
                        "severity": "high",
                        "category": "anatomy",
                        "issue": "手指数目异常",
                        "evidence": "右手出现多余手指",
                        "suggestion": "局部重绘右手",
                        "confidence": 0.9,
                    }
                ],
            }
            with patch.object(audit.DB, "search_works", return_value=rows), patch.object(
                audit.DB, "get_work_lite", return_value=lite
            ), patch.object(
                audit.DB,
                "get_work_prompt_snippet",
                return_value={"snippet": "1girl, portrait"},
            ), patch.object(
                audit,
                "_safe_local_path",
                side_effect=lambda value: cover if value == "cover" else None,
            ), patch.object(audit, "_cache_get", return_value=None), patch.object(
                audit, "_cache_put"
            ) as cache_put, patch.object(
                audit, "chat_json", return_value=response
            ) as chat, patch.object(
                audit, "ai_status", return_value={"model": "vision-model"}
            ):
                result = audit.run_gallery_audit(
                    {"limit": 6, "sort": "new", "time_range": "month", "use_vision": True}
                )

        self.assertEqual(result["stats"]["scanned"], 1)
        self.assertEqual(result["stats"]["vision_checked"], 1)
        self.assertEqual(result["stats"]["high"], 1)
        self.assertEqual(result["items"][0]["work_id"], 7)
        self.assertEqual(result["items"][0]["findings"][0]["issue"], "手指数目异常")
        self.assertNotIn("source_path", result["items"][0])
        self.assertNotIn("prompt_excerpt", result["items"][0])
        self.assertEqual(len(chat.call_args.kwargs["image_data_urls"]), 1)
        self.assertEqual(chat.call_args.kwargs["image_detail"], "low")
        self.assertEqual(chat.call_args.kwargs["max_tokens"], 900)
        cache_put.assert_called_once()

    def test_status_anomalies_are_reported_without_vision_when_cover_is_missing(self) -> None:
        rows = {"items": [{"id": 8, "title": "缺图", "image_count": 3, "thumb_path": ""}]}
        lite = {"images": [{"page_index": 1, "local_path": None}]}
        with patch.object(audit.DB, "search_works", return_value=rows), patch.object(
            audit.DB, "get_work_lite", return_value=lite
        ), patch.object(
            audit.DB, "get_work_prompt_snippet", return_value={"snippet": ""}
        ), patch.object(audit, "_cache_get", return_value=None), patch.object(
            audit, "_cache_put"
        ), patch.object(audit, "chat_json") as chat, patch.object(
            audit, "ai_status", return_value={"model": "vision-model"}
        ):
            result = audit.run_gallery_audit({"limit": 1})

        issues = [row["issue"] for row in result["items"][0]["findings"]]
        self.assertIn("图片索引数量不足", issues)
        self.assertIn("封面未缓存或本地文件缺失", issues)
        self.assertEqual(result["stats"]["vision_checked"], 0)
        chat.assert_not_called()

    def test_cache_hit_skips_image_decode_and_model_call(self) -> None:
        candidate = {
            "candidate_id": "gallery:site:7:p0",
            "work_id": 9,
            "status": {"cover_cached": True},
            "source_size": 100,
            "source_mtime": 1,
        }
        cached = {
            "ok": True,
            "tool": "audit_gallery",
            "stats": {"scanned": 1, "vision_checked": 1, "issues": 0, "cache_hit": False},
            "items": [],
        }
        with patch.object(audit, "_collect", return_value=([candidate], [])), patch.object(
            audit, "_cache_get", return_value=cached
        ), patch.object(audit, "_prepare_visuals") as prepare, patch.object(
            audit, "chat_json"
        ) as chat, patch.object(audit, "ai_status", return_value={"model": "vision-model"}):
            result = audit.run_gallery_audit({"limit": 1})

        self.assertTrue(result["stats"]["cache_hit"])
        prepare.assert_not_called()
        chat.assert_not_called()

    def test_default_audit_does_not_call_vision_or_locally_filter_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cover = self._cover(temp)
            rows = {
                "items": [
                    {
                        "id": 10,
                        "title": "private sample",
                        "tags": "rating:explicit",
                        "image_count": 1,
                        "thumb_path": "cover",
                    }
                ]
            }
            lite = {"images": [{"page_index": 0, "local_path": "cover"}]}
            with patch.object(audit.DB, "search_works", return_value=rows), patch.object(
                audit.DB, "get_work_lite", return_value=lite
            ), patch.object(
                audit.DB, "get_work_prompt_snippet", return_value={"snippet": ""}
            ), patch.object(
                audit,
                "_safe_local_path",
                side_effect=lambda value: cover if value == "cover" else None,
            ), patch.object(audit, "_cache_get", return_value=None), patch.object(
                audit, "_cache_put"
            ), patch.object(audit, "chat_json") as chat, patch.object(
                audit, "ai_status", return_value={"model": "vision-model"}
            ):
                result = audit.run_gallery_audit({"limit": 1})

        self.assertEqual(result["stats"]["vision_checked"], 0)
        self.assertEqual(result["stats"]["vision_skipped_safety"], 0)
        self.assertFalse(result["stats"]["vision_requested"])
        chat.assert_not_called()

    def test_upstream_refusal_keeps_the_local_report(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            cover = self._cover(temp)
            rows = {"items": [{"id": 12, "title": "样例", "image_count": 1, "thumb_path": "cover"}]}
            lite = {"images": [{"page_index": 0, "local_path": "cover"}]}
            with patch.object(audit.DB, "search_works", return_value=rows), patch.object(
                audit.DB, "get_work_lite", return_value=lite
            ), patch.object(
                audit.DB, "get_work_prompt_snippet", return_value={"snippet": ""}
            ), patch.object(
                audit, "_safe_local_path", side_effect=lambda value: cover if value == "cover" else None
            ), patch.object(audit, "_cache_get", return_value=None), patch.object(
                audit, "_cache_put"
            ), patch.object(audit, "chat_json", side_effect=RuntimeError("403 safety refusal")), patch.object(
                audit, "ai_status", return_value={"model": "vision-model"}
            ):
                result = audit.run_gallery_audit({"limit": 1, "use_vision": True})

        self.assertTrue(result["ok"])
        self.assertTrue(result["stats"]["vision_refused"])
        self.assertIn("拒绝", result["summary"])

    def test_default_local_audit_detects_duplicate_images_without_spending_tokens(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.png"
            second = Path(temp) / "second.png"
            image = Image.new("RGB", (768, 1024), (30, 60, 90))
            image.save(first)
            image.save(second)
            rows = {
                "items": [
                    {"id": 20, "title": "第一张", "image_count": 1, "thumb_path": "first"},
                    {"id": 21, "title": "第二张", "image_count": 1, "thumb_path": "second"},
                ]
            }
            lite = {
                20: {"images": [{"page_index": 0, "local_path": "first"}]},
                21: {"images": [{"page_index": 0, "local_path": "second"}]},
            }
            paths = {"first": first, "second": second}
            with patch.object(audit.DB, "search_works", return_value=rows), patch.object(
                audit.DB, "get_work_lite", side_effect=lambda work_id: lite[work_id]
            ), patch.object(
                audit.DB, "get_work_prompt_snippet", return_value={"snippet": ""}
            ), patch.object(
                audit, "_safe_local_path", side_effect=lambda value: paths.get(value)
            ), patch.object(audit, "_cache_get", return_value=None), patch.object(
                audit, "_cache_put"
            ), patch.object(audit, "chat_json") as chat, patch.object(
                audit, "ai_status", return_value={"model": "vision-model"}
            ):
                result = audit.run_gallery_audit({"limit": 2})

        issues = [finding["issue"] for item in result["items"] for finding in item["findings"]]
        self.assertIn("发现重复图片", issues)
        self.assertEqual(result["stats"]["local_images_checked"], 2)
        self.assertEqual(result["stats"]["vision_checked"], 0)
        chat.assert_not_called()

    def test_fixed_candidate_comparison_uses_exact_pages_and_one_low_detail_call(self) -> None:
        with tempfile.TemporaryDirectory() as temp:
            first = Path(temp) / "first.png"
            second = Path(temp) / "second.png"
            Image.new("RGB", (768, 1024), (40, 80, 120)).save(first)
            Image.new("RGB", (768, 1024), (120, 80, 40)).save(second)
            rows = {
                20: {
                    "work": {"id": 20, "title": "左侧候选"},
                    "images": [{"page_index": 1, "local_path": "first"}],
                },
                21: {
                    "work": {"id": 21, "title": "右侧候选"},
                    "images": [{"page_index": 2, "local_path": "second"}],
                },
            }
            db = audit.DB
            response = {
                "summary": "右侧候选的构图更稳。",
                "winner_image_ref": "image_2",
                "ranking": [
                    {"image_ref": "image_2", "rank": 1, "strengths": "构图稳定", "weaknesses": "", "reason": "主体更集中"},
                    {"image_ref": "image_1", "rank": 2, "strengths": "色彩清楚", "weaknesses": "留白偏多", "reason": "视觉重心较散"},
                ],
            }
            with patch.object(db, "get_work_lite", side_effect=lambda work_id: rows[work_id]), patch.object(
                audit, "_gallery_db", return_value=db
            ), patch.object(
                audit,
                "_safe_local_path",
                side_effect=lambda value, gallery_id="site": {"first": first, "second": second}.get(value),
            ), patch.object(audit, "chat_json", return_value=response) as chat:
                result = audit.run_gallery_comparison(
                    {
                        "question": "这两张哪个更好看？",
                        "candidates": [
                            {"gallery_id": "site", "work_id": 20, "page_index": 1},
                            {"gallery_id": "site", "work_id": 21, "page_index": 2},
                        ],
                    }
                )

        self.assertTrue(result["ok"])
        self.assertEqual(result["winner"]["work_id"], 21)
        self.assertEqual([(item["work_id"], item["page_index"]) for item in result["items"]], [(20, 1), (21, 2)])
        self.assertEqual(result["stats"]["vision_checked"], 2)
        self.assertEqual(result["stats"]["model_calls"], 1)
        self.assertEqual(chat.call_args.kwargs["image_detail"], "low")
        self.assertLessEqual(len(chat.call_args.kwargs["image_data_urls"]), 4)

    def test_comparison_refusal_keeps_candidates_and_reports_upstream_boundary(self) -> None:
        candidate = {
            "candidate_id": "gallery:site:7:p0",
            "gallery_id": "site",
            "work_id": 7,
            "page_index": 0,
            "title": "候选",
            "url": "/i/7?gallery=site",
            "thumb": "/data/images/7.webp",
            "source_path": "x",
        }
        with patch.object(audit, "_comparison_candidates", return_value=[candidate, {**candidate, "candidate_id": "gallery:site:8:p0", "work_id": 8}]), patch.object(
            audit, "_encode_thumbnail", return_value=("data:image/jpeg;base64,eA==", {"width": 1, "height": 1})
        ), patch.object(audit, "chat_json", side_effect=RuntimeError("403 safety refusal")):
            result = audit.run_gallery_comparison({"question": "哪个好看", "candidates": [{}, {}]})

        self.assertTrue(result["ok"])
        self.assertTrue(result["stats"]["vision_refused"])
        self.assertEqual(len(result["items"]), 2)
        self.assertIn("拒绝", result["summary"])


if __name__ == "__main__":
    unittest.main()
