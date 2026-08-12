"""Read-only Pixiv sampling for measuring strict NovelAI intake yield."""

from __future__ import annotations

import argparse
import json
import random
import re
import tempfile
import time
from collections import Counter
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from nai_image_metadata import parse_nai_image
from pixiv_nai_crawler import (
    ROOT,
    _active_pixiv_account_id,
    load_task,
    normalize_task,
)
from pixiv_nai_source import (
    PixivAPIError,
    PixivDownloadFailure,
    PixivNAISource,
    PixivSourcePage,
)
from pixiv_browser_source import PixivBrowserSource
from pixiv_public_source import PixivPublicWebSource


def _bounded_sample_size(value: Any, *, default: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _fetch_page_with_retry(
    source: Any,
    scope: dict[str, Any],
    cursor: str,
    task: dict[str, Any],
    sleep_fn: Callable[[float], None],
) -> PixivSourcePage:
    retry_max = int(task["retry_max"])
    for attempt in range(retry_max):
        try:
            return source.fetch_page(scope, cursor)
        except PixivAPIError as exc:
            if not exc.retryable or attempt + 1 >= retry_max:
                raise
            delay = exc.retry_after
            if delay is None:
                base = float(task["backoff_base_sec"]) * (2**attempt)
                delay = base + random.uniform(0, min(1.0, base * 0.25))
            sleep_fn(min(300.0, max(0.0, delay)))
    raise RuntimeError("Pixiv preflight page retry loop exhausted")


def _download_failure(source: Any, url: str, exc: Exception) -> PixivDownloadFailure:
    consume = getattr(source, "consume_download_failure", None)
    failure = consume(url) if callable(consume) else None
    if isinstance(failure, PixivDownloadFailure):
        kind = "permanent" if failure.kind == "permanent" else "retryable"
        reason = str(failure.reason or "download_error")
    else:
        kind = "retryable" if bool(getattr(exc, "retryable", False)) else "permanent"
        reason = str(getattr(exc, "reason", "download_error") or "download_error")
    if not re.fullmatch(r"[a-z0-9_]{1,64}", reason):
        reason = "download_error"
    return PixivDownloadFailure(kind=kind, reason=reason)


def _rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 4)


def run_preflight(
    *,
    task: dict[str, Any] | None = None,
    source: PixivNAISource | Any | None = None,
    root: Path = ROOT,
    max_pages: int = 1,
    max_works: int = 25,
    temp_parent: Path | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Sample the configured source without touching the gallery or crawler state."""

    root = Path(root).resolve()
    normalized = normalize_task(task) if task is not None else load_task(root=root)
    page_limit = _bounded_sample_size(max_pages, default=1, maximum=10)
    work_limit = _bounded_sample_size(max_works, default=25, maximum=200)
    own_source = source is None
    selected_source_mode = "injected"
    if source is None:
        mode = str(normalized.get("source_mode") or "auto").strip().lower()
        account_id = _active_pixiv_account_id(str(normalized.get("account_id") or ""))
        selected_source_mode = "public" if mode == "public" or (mode == "auto" and not account_id) else "api"
        if selected_source_mode == "public":
            public_kwargs = {
                "max_download_bytes": int(normalized["max_download_bytes"]),
                "ai_prefilter": bool(normalized["require_pixiv_ai_generated"]),
                "request_delay_sec": float(normalized["request_delay_sec"]),
                "sleep_fn": time.sleep,
                "proxy_url": str(normalized.get("proxy_url") or ""),
            }
            if bool(normalized.get("browser_mode")) and PixivBrowserSource.available():
                source = PixivBrowserSource(**public_kwargs)
                selected_source_mode = "public_browser"
            else:
                source = PixivPublicWebSource(**public_kwargs)
        else:
            # Conservative 0.5s floor mirrors the crawler's API-mode pacing.
            source = PixivNAISource(
                account_id=account_id or None,
                max_download_bytes=int(normalized["max_download_bytes"]),
                request_delay_sec=max(0.5, float(normalized["request_delay_sec"])),
            )

    counters: Counter[str] = Counter()
    rejection_reasons: Counter[str] = Counter()
    failure_kinds: Counter[str] = Counter()
    status = "completed"
    last_error = ""
    enabled_scopes = [
        scope for scope in normalized["scopes"] if scope.get("enabled", True)
    ]

    try:
        parent = Path(temp_parent).resolve() if temp_parent is not None else None
        with tempfile.TemporaryDirectory(
            prefix="pixiv-nai-preflight-",
            dir=parent,
        ) as temporary:
            temporary_dir = Path(temporary)
            stop = False
            for scope in enabled_scopes:
                if stop:
                    break
                cursor = ""
                scope_started = False
                for _ in range(page_limit):
                    if counters["pages_fetched"] >= page_limit:
                        stop = True
                        break
                    if not scope_started:
                        counters["scopes_sampled"] += 1
                        scope_started = True
                    try:
                        page = _fetch_page_with_retry(
                            source,
                            scope,
                            cursor,
                            normalized,
                            sleep_fn,
                        )
                    except Exception as exc:
                        status = "failed"
                        last_error = type(exc).__name__
                        if isinstance(exc, PixivAPIError):
                            failure_kinds[
                                "retryable" if exc.retryable else "permanent"
                            ] += 1
                        stop = True
                        break
                    counters["pages_fetched"] += 1
                    counters["works_found"] += len(page.works)
                    for work in page.works:
                        if counters["works_sampled"] >= work_limit:
                            stop = True
                            break
                        counters["works_sampled"] += 1
                        if (
                            normalized["require_pixiv_ai_generated"]
                            and work.pixiv_ai_type not in {None, 2}
                        ):
                            counters["works_pixiv_ai_rejected"] += 1
                            rejection_reasons["pixiv_not_marked_ai"] += len(work.pages)
                            continue
                        for page_item in work.pages:
                            counters["pages_sampled"] += 1
                            suffix = Path(urlparse(page_item.original_url).path).suffix.lower()
                            if suffix not in {".png", ".jpg", ".jpeg", ".webp"}:
                                suffix = ".img"
                            destination = temporary_dir / (
                                f"w{work.work_id}-p{page_item.source_page_index}{suffix}"
                            )
                            try:
                                source.download_original(
                                    page_item.original_url,
                                    destination,
                                )
                                if not destination.is_file():
                                    raise OSError("downloader did not create a file")
                            except Exception as exc:
                                counters["downloads_failed"] += 1
                                failure = _download_failure(
                                    source,
                                    page_item.original_url,
                                    exc,
                                )
                                failure_kinds[
                                    "retryable"
                                    if failure.kind == "retryable"
                                    else "permanent"
                                ] += 1
                                rejection_reasons[failure.reason] += 1
                                destination.unlink(missing_ok=True)
                                continue
                            counters["downloads_succeeded"] += 1
                            try:
                                parsed = parse_nai_image(destination)
                            except Exception:
                                counters["nai_rejected"] += 1
                                rejection_reasons["metadata_parse_error"] += 1
                                destination.unlink(missing_ok=True)
                                continue
                            if parsed.accepted:
                                counters["nai_accepted"] += 1
                            else:
                                counters["nai_rejected"] += 1
                                rejection_reasons[parsed.reason] += 1
                            destination.unlink(missing_ok=True)
                    if stop or not page.next_cursor:
                        break
                    if page.next_cursor == cursor:
                        status = "source_loop"
                        break
                    cursor = page.next_cursor
    finally:
        if own_source:
            source.close()

    downloads_attempted = counters["downloads_succeeded"] + counters["downloads_failed"]
    result = {
        "status": status,
        "source_mode": selected_source_mode,
        "scopes_sampled": counters["scopes_sampled"],
        "pages_fetched": counters["pages_fetched"],
        "works_found": counters["works_found"],
        "works_sampled": counters["works_sampled"],
        "works_pixiv_ai_rejected": counters["works_pixiv_ai_rejected"],
        "pages_sampled": counters["pages_sampled"],
        "downloads_succeeded": counters["downloads_succeeded"],
        "downloads_failed": counters["downloads_failed"],
        "nai_accepted": counters["nai_accepted"],
        "nai_rejected": counters["nai_rejected"],
        "download_success_rate": _rate(
            counters["downloads_succeeded"], downloads_attempted
        ),
        "nai_recognition_rate": _rate(
            counters["nai_accepted"], counters["downloads_succeeded"]
        ),
        "rejection_reasons": dict(rejection_reasons),
        "failure_kinds": dict(failure_kinds),
        "last_error": last_error,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Read-only Pixiv NovelAI recognition preflight"
    )
    parser.add_argument("--max-pages", type=int, default=1)
    parser.add_argument("--max-works", type=int, default=25)
    args = parser.parse_args()
    report = run_preflight(max_pages=args.max_pages, max_works=args.max_works)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] in {"completed", "source_loop"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
