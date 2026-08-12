from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

from PIL import Image, PngImagePlugin

from pixiv_nai_intake import PixivPage, PixivWork
from pixiv_nai_preflight import run_preflight
from pixiv_nai_source import PixivAPIError, PixivDownloadError, PixivSourcePage


def _work(work_id: int) -> PixivWork:
    return PixivWork(
        work_id=work_id,
        user_id=9,
        user_name="Alice",
        title=f"work-{work_id}",
        caption="",
        tags=("NovelAI",),
        create_date="2026-08-02T00:00:00+00:00",
        total_view=1,
        total_bookmarks=1,
        pages=(PixivPage(0, f"https://i.pximg.net/{work_id}.png"),),
        pixiv_ai_type=2,
    )


class _Source:
    def __init__(self) -> None:
        self.download_destinations: list[Path] = []

    def fetch_page(self, _scope: dict, _cursor: str = "") -> PixivSourcePage:
        return PixivSourcePage(works=(_work(1), _work(2)), next_cursor="")

    def download_original(self, url: str, destination: Path) -> None:
        self.download_destinations.append(destination)
        if url.endswith("/1.png"):
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("Software", "NovelAI")
            metadata.add_text("Source", "NovelAI Diffusion V4.5")
            metadata.add_text("Description", "1girl, outdoors")
            metadata.add_text(
                "Comment",
                json.dumps({"prompt": "1girl, outdoors", "seed": 7}),
            )
            Image.new("RGB", (8, 8)).save(destination, pnginfo=metadata)
        else:
            Image.new("RGB", (8, 8)).save(destination)


def test_preflight_measures_real_download_and_nai_recognition_without_persistence(
    tmp_path: Path,
) -> None:
    source = _Source()

    report = run_preflight(
        task={
            "enabled": True,
            "account_id": "secret-account-slot",
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "retry_max": 1,
            "request_delay_sec": 0,
        },
        source=source,
        max_pages=1,
        max_works=2,
        temp_parent=tmp_path,
    )

    assert report["status"] == "completed"
    assert report["works_found"] == 2
    assert report["works_sampled"] == 2
    assert report["pages_sampled"] == 2
    assert report["downloads_succeeded"] == 2
    assert report["downloads_failed"] == 0
    assert report["nai_accepted"] == 1
    assert report["nai_rejected"] == 1
    assert report["download_success_rate"] == 1.0
    assert report["nai_recognition_rate"] == 0.5
    assert report["rejection_reasons"] == {"nai_metadata_missing": 1}
    assert all(path.exists() is False for path in source.download_destinations)
    assert list(tmp_path.iterdir()) == []
    serialized = json.dumps(report, ensure_ascii=False)
    assert "secret-account-slot" not in serialized
    assert "pximg" not in serialized
    assert "source_url" not in serialized


def test_preflight_reports_safe_retryable_download_failure_semantics(
    tmp_path: Path,
) -> None:
    class FailingSource(_Source):
        def fetch_page(self, _scope: dict, _cursor: str = "") -> PixivSourcePage:
            return PixivSourcePage(works=(_work(1),), next_cursor="")

        def download_original(self, _url: str, _destination: Path) -> None:
            raise PixivDownloadError(
                "credential=https://i.pximg.net/private.png",
                retryable=True,
                reason="network_error",
            )

    report = run_preflight(
        task={
            "enabled": True,
            "account_id": "secret-account-slot",
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "retry_max": 1,
            "request_delay_sec": 0,
        },
        source=FailingSource(),
        temp_parent=tmp_path,
    )

    assert report["status"] == "completed"
    assert report["downloads_succeeded"] == 0
    assert report["downloads_failed"] == 1
    assert report["failure_kinds"] == {"retryable": 1}
    assert report["rejection_reasons"] == {"network_error": 1}
    serialized = json.dumps(report, ensure_ascii=False)
    assert "private.png" not in serialized
    assert "secret-account-slot" not in serialized


def test_preflight_page_and_work_budgets_are_global_across_scopes(
    tmp_path: Path,
) -> None:
    class CountingSource(_Source):
        def __init__(self) -> None:
            super().__init__()
            self.fetches: list[str] = []

        def fetch_page(self, scope: dict, cursor: str = "") -> PixivSourcePage:
            self.fetches.append(str(scope["id"]))
            return PixivSourcePage(works=(_work(1), _work(2)), next_cursor="")

    source = CountingSource()
    report = run_preflight(
        task={
            "enabled": False,
            "scopes": [
                {"id": "secretacct123", "type": "search", "query": "NovelAI", "enabled": True},
                {"id": "second", "type": "search", "query": "AI illustration", "enabled": True},
            ],
            "retry_max": 1,
            "request_delay_sec": 0,
        },
        source=source,
        root=tmp_path,
        max_pages=1,
        max_works=200,
        temp_parent=tmp_path,
    )

    assert source.fetches == ["secretacct123"]
    assert report["pages_fetched"] == 1
    assert report["works_found"] == 2
    assert report["works_sampled"] == 2
    assert report["scopes_sampled"] == 1
    assert "secretacct123" not in json.dumps(report, ensure_ascii=False)


def test_preflight_contains_parser_exception_and_does_not_close_injected_source(
    tmp_path: Path,
) -> None:
    class ClosableSource(_Source):
        def __init__(self) -> None:
            super().__init__()
            self.closed = 0

        def close(self) -> None:
            self.closed += 1

    source = ClosableSource()
    with patch(
        "pixiv_nai_preflight.parse_nai_image",
        side_effect=RuntimeError("secret parser path and prompt"),
    ):
        report = run_preflight(
            task={
                "enabled": False,
                "scopes": [
                    {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
                ],
                "retry_max": 1,
                "request_delay_sec": 0,
            },
            source=source,
            root=tmp_path,
            max_pages=1,
            max_works=1,
            temp_parent=tmp_path,
        )

    assert report["status"] == "completed"
    assert report["nai_accepted"] == 0
    assert report["nai_rejected"] == 1
    assert report["rejection_reasons"] == {"metadata_parse_error": 1}
    assert source.closed == 0
    assert all(path.exists() is False for path in source.download_destinations)
    assert "secret parser" not in json.dumps(report, ensure_ascii=False)


def test_preflight_closes_owned_source_and_classifies_terminal_api_failure(
    tmp_path: Path,
) -> None:
    class FailingSource:
        def __init__(self) -> None:
            self.closed = 0

        def fetch_page(self, scope: dict, cursor: str = "") -> PixivSourcePage:
            raise PixivAPIError(
                "secret token and private endpoint",
                status_code=503,
                retryable=True,
            )

        def close(self) -> None:
            self.closed += 1

    source = FailingSource()
    with patch("pixiv_nai_preflight.PixivNAISource", return_value=source):
        report = run_preflight(
            task={
                "enabled": False,
                "account_id": "secret-account-slot",
                "scopes": [
                    {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
                ],
                "retry_max": 1,
                "request_delay_sec": 0,
            },
            root=tmp_path,
            max_pages=1,
            max_works=1,
            temp_parent=tmp_path,
        )

    assert report["status"] == "failed"
    assert report["last_error"] == "PixivAPIError"
    assert report["failure_kinds"] == {"retryable": 1}
    assert source.closed == 1
    assert list(tmp_path.iterdir()) == []
    serialized = json.dumps(report, ensure_ascii=False)
    assert "secret token" not in serialized
    assert "secret-account-slot" not in serialized
