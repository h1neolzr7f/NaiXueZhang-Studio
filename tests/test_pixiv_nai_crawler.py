from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

from PIL import Image, PngImagePlugin

from pixiv_nai_crawler import crawl_once, get_report, load_state, save_task
from pixiv_nai_intake import IntakeReceipt, PageReceipt, PixivPage, PixivWork
from pixiv_nai_source import PixivAPIError, PixivDownloadFailure, PixivSourcePage


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
    )


class _Source:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def fetch_page(self, scope: dict, cursor: str = "") -> PixivSourcePage:
        self.calls.append(cursor)
        return PixivSourcePage(works=(_work(1), _work(2)), next_cursor="next")

    def download_original(self, url: str, destination: Path) -> None:
        raise AssertionError("fake intake owns this seam")


class _Intake:
    def __init__(self) -> None:
        self.work_ids: list[int] = []

    def ingest_work(self, work: PixivWork, download) -> IntakeReceipt:
        self.work_ids.append(work.work_id)
        accepted = work.work_id == 1
        return IntakeReceipt(
            work_id=work.work_id,
            status="accepted" if accepted else "rejected",
            accepted_pages=1 if accepted else 0,
            rejected_pages=0 if accepted else 1,
            pages=(
                PageReceipt(
                    0,
                    work.pages[0].original_url,
                    "accepted" if accepted else "rejected",
                    "accepted" if accepted else "nai_metadata_missing",
                ),
            ),
        )


def test_crawler_checkpoints_inside_page_and_report_never_leaks_urls(
    tmp_path: Path,
) -> None:
    save_task(
        {
            "enabled": True,
            "account_id": "account-1",
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "max_pages_per_run": 2,
            "max_works_per_run": 1,
            "request_delay_sec": 0,
            "retry_max": 1,
        },
        root=tmp_path,
    )
    source = _Source()
    intake = _Intake()

    first = crawl_once(root=tmp_path, source=source, intake=intake)
    first_state = load_state(root=tmp_path)
    second = crawl_once(root=tmp_path, source=source, intake=intake)
    second_state = load_state(root=tmp_path)
    public_report = get_report(root=tmp_path)

    assert first["works_seen"] == 1
    assert first_state["scopes"]["nai"] == {"cursor": "", "offset": 1}
    assert second["works_seen"] == 1
    assert intake.work_ids == [1, 2]
    assert second_state["scopes"]["nai"] == {"cursor": "next", "offset": 0}
    serialized = json.dumps(public_report, ensure_ascii=False)
    assert "pximg" not in serialized
    assert "source_url" not in serialized
    assert public_report["accepted_pages"] == 0
    assert public_report["rejection_reasons"] == {"nai_metadata_missing": 1}


def test_disabled_crawler_performs_no_source_requests(tmp_path: Path) -> None:
    save_task({"enabled": False}, root=tmp_path)
    source = _Source()
    intake = _Intake()

    report = crawl_once(root=tmp_path, source=source, intake=intake)

    assert report["status"] == "disabled"
    assert source.calls == []
    assert intake.work_ids == []


def test_failed_work_keeps_cursor_on_same_item_for_retry(tmp_path: Path) -> None:
    save_task(
        {
            "enabled": True,
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "max_pages_per_run": 1,
            "max_works_per_run": 10,
            "request_delay_sec": 0,
            "retry_max": 1,
        },
        root=tmp_path,
    )

    class FailedIntake:
        def ingest_work(self, work: PixivWork, download) -> IntakeReceipt:
            return IntakeReceipt(
                work_id=work.work_id,
                status="failed",
                accepted_pages=0,
                rejected_pages=1,
                pages=(PageReceipt(0, work.pages[0].original_url, "failed", "download_error"),),
            )

    report = crawl_once(root=tmp_path, source=_Source(), intake=FailedIntake())
    state = load_state(root=tmp_path)

    assert report["status"] == "failed"
    assert state["scopes"]["nai"] == {"cursor": "", "offset": 0}


def test_repeated_failed_work_is_quarantined_and_next_work_continues(
    tmp_path: Path,
) -> None:
    save_task(
        {
            "enabled": True,
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "max_pages_per_run": 1,
            "max_works_per_run": 10,
            "request_delay_sec": 0,
            "retry_max": 1,
            "work_failure_threshold": 2,
        },
        root=tmp_path,
    )

    class RecoveringIntake:
        def __init__(self) -> None:
            self.work_ids: list[int] = []

        def ingest_work(self, work: PixivWork, download) -> IntakeReceipt:
            self.work_ids.append(work.work_id)
            if work.work_id == 1:
                return IntakeReceipt(
                    work_id=work.work_id,
                    status="failed",
                    accepted_pages=0,
                    rejected_pages=1,
                    pages=(
                        PageReceipt(
                            0,
                            work.pages[0].original_url,
                            "failed",
                            "download_error",
                        ),
                    ),
                )
            return IntakeReceipt(
                work_id=work.work_id,
                status="accepted",
                accepted_pages=1,
                rejected_pages=0,
                pages=(
                    PageReceipt(
                        0,
                        work.pages[0].original_url,
                        "accepted",
                        "accepted",
                    ),
                ),
            )

    intake = RecoveringIntake()
    first = crawl_once(root=tmp_path, source=_Source(), intake=intake)
    second = crawl_once(root=tmp_path, source=_Source(), intake=intake)
    state = load_state(root=tmp_path)

    assert first["status"] == "failed"
    assert second["status"] == "completed"
    assert second["works_quarantined"] == 1
    assert intake.work_ids == [1, 1, 2]
    assert state["scopes"]["nai"] == {"cursor": "next", "offset": 0}
    assert state["quarantine"]["nai:1"]["failure_count"] == 2


def test_quarantine_keeps_safe_retryable_or_permanent_failure_kind(
    tmp_path: Path,
) -> None:
    save_task(
        {
            "enabled": True,
            "account_id": "secret-account-slot",
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "max_pages_per_run": 1,
            "max_works_per_run": 1,
            "request_delay_sec": 0,
            "retry_max": 1,
            "work_failure_threshold": 5,
        },
        root=tmp_path,
    )

    class ClassifiedSource(_Source):
        def consume_download_failure(self, _url: str) -> PixivDownloadFailure:
            return PixivDownloadFailure("permanent", "http_403")

    class FailedIntake:
        def ingest_work(self, work: PixivWork, download) -> IntakeReceipt:
            return IntakeReceipt(
                work_id=work.work_id,
                status="failed",
                accepted_pages=0,
                rejected_pages=1,
                pages=(
                    PageReceipt(
                        0,
                        work.pages[0].original_url,
                        "failed",
                        "download_error",
                    ),
                ),
            )

    report = crawl_once(
        root=tmp_path,
        source=ClassifiedSource(),
        intake=FailedIntake(),
    )
    state = load_state(root=tmp_path)
    serialized_report = json.dumps(report, ensure_ascii=False)

    assert report["failure_kinds"] == {"permanent": 1}
    assert report["status"] == "budget_reached"
    assert report["works_quarantined"] == 1
    assert state["quarantine"]["nai:1"]["failure_kind"] == "permanent"
    assert state["quarantine"]["nai:1"]["reason"] == "http_403"
    assert "pximg" not in serialized_report
    assert "secret-account-slot" not in serialized_report


def test_synthetic_incomplete_page_does_not_dilute_permanent_download_failure(
    tmp_path: Path,
) -> None:
    save_task(
        {
            "enabled": True,
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "max_pages_per_run": 1,
            "max_works_per_run": 1,
            "request_delay_sec": 0,
            "retry_max": 1,
            "work_failure_threshold": 1,
        },
        root=tmp_path,
    )
    work = _work(1)
    work = replace(
        work,
        pages=(
            PixivPage(0, "https://i.pximg.net/1-p0.png"),
            PixivPage(1, "https://i.pximg.net/1-p1.png"),
        ),
    )

    class MultiPageSource(_Source):
        def fetch_page(self, scope: dict, cursor: str = "") -> PixivSourcePage:
            return PixivSourcePage(works=(work,), next_cursor="")

        def consume_download_failure(self, url: str) -> PixivDownloadFailure | None:
            if url.endswith("p0.png"):
                return PixivDownloadFailure("permanent", "http_403")
            return None

    class FailedIntake:
        def ingest_work(self, item: PixivWork, download) -> IntakeReceipt:
            return IntakeReceipt(
                work_id=item.work_id,
                status="failed",
                accepted_pages=0,
                rejected_pages=2,
                pages=(
                    PageReceipt(0, item.pages[0].original_url, "failed", "download_error"),
                    PageReceipt(1, item.pages[1].original_url, "failed", "work_incomplete"),
                ),
            )

    report = crawl_once(
        root=tmp_path,
        source=MultiPageSource(),
        intake=FailedIntake(),
    )
    state = load_state(root=tmp_path)

    assert report["failure_kinds"] == {"permanent": 1}
    assert state["quarantine"]["nai:1"]["failure_kind"] == "permanent"
    assert state["quarantine"]["nai:1"]["reason"] == "http_403"


def test_later_success_for_same_work_resolves_active_quarantine(tmp_path: Path) -> None:
    save_task(
        {
            "enabled": True,
            "scopes": [
                {"id": "first", "type": "search", "query": "NovelAI", "enabled": True},
                {"id": "second", "type": "search", "query": "AIイラスト", "enabled": True},
            ],
            "max_pages_per_run": 1,
            "max_works_per_run": 10,
            "request_delay_sec": 0,
            "retry_max": 1,
            "work_failure_threshold": 1,
        },
        root=tmp_path,
    )

    class OneWorkSource(_Source):
        def fetch_page(self, scope: dict, cursor: str = "") -> PixivSourcePage:
            return PixivSourcePage(works=(_work(1),), next_cursor="")

    class ThenSuccessfulIntake:
        def __init__(self) -> None:
            self.calls = 0

        def ingest_work(self, work: PixivWork, download) -> IntakeReceipt:
            self.calls += 1
            failed = self.calls == 1
            return IntakeReceipt(
                work_id=work.work_id,
                status="failed" if failed else "accepted",
                accepted_pages=0 if failed else 1,
                rejected_pages=1 if failed else 0,
                pages=(
                    PageReceipt(
                        0,
                        work.pages[0].original_url,
                        "failed" if failed else "accepted",
                        "download_error" if failed else "accepted",
                    ),
                ),
            )

    report = crawl_once(
        root=tmp_path,
        source=OneWorkSource(),
        intake=ThenSuccessfulIntake(),
    )
    state = load_state(root=tmp_path)

    assert report["status"] == "completed"
    assert report["works_recovered"] == 1
    assert state["quarantine"] == {}


def test_active_quarantine_failure_in_overlapping_scope_never_blocks_again(
    tmp_path: Path,
) -> None:
    save_task(
        {
            "enabled": True,
            "scopes": [
                {"id": "first", "type": "search", "query": "NovelAI", "enabled": True},
                {"id": "second", "type": "search", "query": "AI illustration", "enabled": True},
            ],
            "max_pages_per_run": 1,
            "max_works_per_run": 10,
            "request_delay_sec": 0,
            "retry_max": 1,
            "work_failure_threshold": 2,
        },
        root=tmp_path,
    )

    class OneWorkSource(_Source):
        def fetch_page(self, scope: dict, cursor: str = "") -> PixivSourcePage:
            return PixivSourcePage(works=(_work(1),), next_cursor="")

    class FailedIntake:
        def ingest_work(self, work: PixivWork, download) -> IntakeReceipt:
            return IntakeReceipt(
                work_id=work.work_id,
                status="failed",
                accepted_pages=0,
                rejected_pages=1,
                pages=(
                    PageReceipt(
                        0,
                        work.pages[0].original_url,
                        "failed",
                        "download_error",
                    ),
                ),
            )

    first = crawl_once(
        root=tmp_path,
        source=OneWorkSource(),
        intake=FailedIntake(),
    )
    second = crawl_once(
        root=tmp_path,
        source=OneWorkSource(),
        intake=FailedIntake(),
    )
    state = load_state(root=tmp_path)

    assert first["status"] == "failed"
    assert second["status"] == "completed"
    assert second["works_seen"] == 2
    assert set(state["quarantine"]) == {"first:1"}
    assert state["quarantine"]["first:1"]["failure_count"] == 3
    assert state["scopes"]["second"] == {"cursor": "", "offset": 0}


def test_public_report_keeps_only_recent_bounded_run_history(tmp_path: Path) -> None:
    save_task({"enabled": False}, root=tmp_path)

    for _ in range(25):
        crawl_once(root=tmp_path, source=_Source(), intake=_Intake())

    report = get_report(root=tmp_path)

    assert len(report["history"]) == 20
    assert {entry["status"] for entry in report["history"]} == {"disabled"}
    assert "account_id" not in json.dumps(report, ensure_ascii=False)


def test_terminal_source_failure_is_recorded_as_one_safe_run(tmp_path: Path) -> None:
    save_task(
        {
            "enabled": True,
            "account_id": "secret-account-slot",
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "max_pages_per_run": 1,
            "max_works_per_run": 1,
            "request_delay_sec": 0,
            "retry_max": 1,
        },
        root=tmp_path,
    )

    class FailedSource(_Source):
        def fetch_page(self, scope: dict, cursor: str = "") -> PixivSourcePage:
            raise PixivAPIError(
                "token and https://app-api.pixiv.net/private",
                status_code=403,
                retryable=False,
            )

    report = crawl_once(root=tmp_path, source=FailedSource(), intake=_Intake())
    persisted = get_report(root=tmp_path)

    assert report["status"] == "failed"
    assert report["last_error"] == "PixivAPIError"
    assert report["failure_kinds"] == {"permanent": 1}
    assert len(report["history"]) == 1
    assert report["history"][0]["status"] == "failed"
    assert report["history"][0]["last_error"] == "PixivAPIError"
    assert report["history"][0]["failure_kinds"] == {"permanent": 1}
    assert persisted == report
    serialized = json.dumps(report, ensure_ascii=False)
    assert "private" not in serialized
    assert "secret-account-slot" not in serialized


def test_source_cursor_loop_remains_visible_in_report_and_history(tmp_path: Path) -> None:
    save_task(
        {
            "enabled": True,
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "max_pages_per_run": 3,
            "max_works_per_run": 10,
            "request_delay_sec": 0,
            "retry_max": 1,
        },
        root=tmp_path,
    )
    repeated_cursor = "https://app-api.pixiv.net/v1/search/illust?offset=30"

    class LoopingSource(_Source):
        def fetch_page(self, scope: dict, cursor: str = "") -> PixivSourcePage:
            return PixivSourcePage(works=(), next_cursor=repeated_cursor)

    report = crawl_once(
        root=tmp_path,
        source=LoopingSource(),
        intake=_Intake(),
    )
    persisted = get_report(root=tmp_path)

    assert report["status"] == "source_loop"
    assert persisted["status"] == "source_loop"
    assert report["history"][-1]["status"] == "source_loop"
    assert repeated_cursor not in json.dumps(report, ensure_ascii=False)


def test_storage_quota_failure_is_permanent_quarantined_and_checkpointed(
    tmp_path: Path,
) -> None:
    saved = save_task(
        {
            "enabled": True,
            "scopes": [
                {"id": "nai", "type": "search", "query": "NovelAI", "enabled": True}
            ],
            "max_pages_per_run": 1,
            "max_works_per_run": 10,
            "request_delay_sec": 0,
            "retry_max": 1,
            "work_failure_threshold": 5,
            "storage_quota_bytes": 1,
        },
        root=tmp_path,
    )

    class QuotaSource(_Source):
        def fetch_page(self, scope: dict, cursor: str = "") -> PixivSourcePage:
            return PixivSourcePage(works=(_work(1),), next_cursor="")

        def download_original(self, url: str, destination: Path) -> None:
            metadata = PngImagePlugin.PngInfo()
            metadata.add_text("Software", "NovelAI")
            metadata.add_text("Source", "NovelAI Diffusion V4.5")
            metadata.add_text("Description", "1girl")
            metadata.add_text("Comment", json.dumps({"prompt": "1girl"}))
            Image.new("RGB", (8, 8)).save(destination, pnginfo=metadata)

    report = crawl_once(root=tmp_path, source=QuotaSource())
    state = load_state(root=tmp_path)

    assert saved["storage_quota_bytes"] == 1
    assert report["status"] == "completed"
    assert report["works_failed"] == 1
    assert report["works_quarantined"] == 1
    assert report["failure_kinds"] == {"permanent": 1}
    assert report["rejection_reasons"] == {"storage_quota_exceeded": 1}
    assert state["quarantine"]["nai:1"]["failure_kind"] == "permanent"
    assert state["quarantine"]["nai:1"]["reason"] == "storage_quota_exceeded"
    assert state["scopes"]["nai"] == {"cursor": "", "offset": 0}
    assert list((tmp_path / "data" / "images").rglob("*.png")) == []


from unittest import mock

from pixiv_nai_crawler import _build_pixiv_source
from pixiv_nai_source import PixivNAISource
from pixiv_public_source import PixivPublicWebSource


class TestSourceModeSelection:
    """Channel-selection contract: auto -> public without an account, api with one."""

    @staticmethod
    def _task(**overrides):
        task = {
            "source_mode": "auto",
            "account_id": "",
            "max_download_bytes": 134217728,
            "require_pixiv_ai_generated": True,
            "request_delay_sec": 0.0,
        }
        task.update(overrides)
        return task

    def test_auto_without_account_selects_public(self) -> None:
        with mock.patch("pixiv_nai_crawler._active_pixiv_account_id", return_value=""):
            source, selected = _build_pixiv_source(self._task())
        assert selected == "public"
        assert isinstance(source, PixivPublicWebSource)

    def test_auto_with_account_selects_api(self) -> None:
        with mock.patch("pixiv_nai_crawler._active_pixiv_account_id", return_value="100487196"):
            source, selected = _build_pixiv_source(self._task())
        assert selected == "api"
        assert isinstance(source, PixivNAISource)

    def test_explicit_public_wins_over_configured_account(self) -> None:
        with mock.patch("pixiv_nai_crawler._active_pixiv_account_id", return_value="100487196"):
            source, selected = _build_pixiv_source(self._task(source_mode="public"))
        assert selected == "public"
        assert isinstance(source, PixivPublicWebSource)

    def test_explicit_api_without_account_stays_api(self) -> None:
        with mock.patch("pixiv_nai_crawler._active_pixiv_account_id", return_value=""):
            source, selected = _build_pixiv_source(self._task(source_mode="api"))
        assert selected == "api"
        assert isinstance(source, PixivNAISource)


    def test_auto_without_account_forwards_proxy_and_delay_to_public(self) -> None:
        with mock.patch("pixiv_nai_crawler._active_pixiv_account_id", return_value=""):
            source, selected = _build_pixiv_source(
                self._task(proxy_url="http://127.0.0.1:7897", request_delay_sec=3.0)
            )
        assert selected == "public"
        assert isinstance(source, PixivPublicWebSource)
        assert source.request_delay_sec == 3.0
        mounts = getattr(source.client, "_mounts", None)
        assert mounts is not None and any(key != "" for key in mounts)
        source.close()

    def test_normalize_task_rejects_invalid_proxy_url(self) -> None:
        from pixiv_nai_crawler import normalize_task

        with mock.patch("pixiv_nai_crawler._active_pixiv_account_id", return_value=""):
            try:
                normalize_task(self._task(proxy_url="not-a-proxy"))
            except ValueError as exc:
                assert "proxy_url" in str(exc)
            else:
                raise AssertionError("invalid proxy_url should be rejected by normalize_task")


    def test_api_mode_enforces_conservative_delay_floor(self) -> None:
        with mock.patch("pixiv_nai_crawler._active_pixiv_account_id", return_value="100487196"):
            source, selected = _build_pixiv_source(
                self._task(request_delay_sec=0.0)
            )
        assert selected == "api"
        assert isinstance(source, PixivNAISource)
        # API mode must never run unpaced, even when the task asks for none.
        assert source.request_delay_sec == 0.5
        source.close()

    def test_api_mode_honors_larger_configured_delay(self) -> None:
        with mock.patch("pixiv_nai_crawler._active_pixiv_account_id", return_value="100487196"):
            source, selected = _build_pixiv_source(
                self._task(request_delay_sec=4.0)
            )
        assert selected == "api"
        assert source.request_delay_sec == 4.0
        source.close()


    def test_normalize_task_rejects_unknown_search_sort(self) -> None:
        from pixiv_nai_crawler import normalize_task

        try:
            normalize_task(self._task(scopes=[{"id": "s1", "type": "search", "query": "x", "sort": "random_sort", "enabled": True}]))
        except ValueError as exc:
            assert "sort" in str(exc)
        else:
            raise AssertionError("unknown search sort should be rejected")

    def test_normalize_task_accepts_popular_sort(self) -> None:
        from pixiv_nai_crawler import normalize_task

        task = normalize_task(self._task(scopes=[{"id": "s1", "type": "search", "query": "x", "sort": "popular_desc", "enabled": True}]))
        assert task["scopes"][0]["sort"] == "popular_desc"
