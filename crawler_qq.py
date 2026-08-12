"""Q群图库爬虫：严格解析 NAI 元数据后按群组/账号增量入库。

设计：
- 只有元数据可解析且明确来自 NovelAI 的图片才可见
- ComfyUI 元数据和路径始终拒绝
- 首选群组/账号清单；旧目录进入“历史未分组/账号”
- 独立 SQLite 解析账本保存接收/拒绝回执，支持断点续跑
- 不依赖在线协议（可后续扩展）
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

from atomic_io import atomic_write_json  # noqa: E402
from gallery_catalog import (  # noqa: E402
    GALLERY_QQ,
    ensure_gallery_dirs,
    get_db,
)
from gallery_import_common import (  # noqa: E402
    save_group_index,
)
from nai_image_metadata import PARSER_VERSION, parse_nai_image  # noqa: E402
from paths import normalize_config  # noqa: E402
from qq_gallery_ingest import (  # noqa: E402
    ensure_ingest_schema,
    import_parsed_nai,
    iter_qq_images,
    load_existing_work_id_index,
    load_ingest_cache,
    looks_like_comfy,
    rebuild_group_index as build_group_index,
    record_ingest_rows,
    repair_interrupted_upgrade_duplicates,
    revalidate_existing_batch,
    source_id,
    work_identity_key,
)


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_heartbeat(root: Path, status: str, message: str = "", **extra: Any) -> None:
    payload = {
        "updated_at": datetime.now().isoformat(timespec="seconds"),
        "phase": "qqgroup",
        "status": status,
        "message": message,
        "gallery": "qqgroup",
        "crawler": "qqgroup",
        **extra,
    }
    atomic_write_json(root / "logs" / "crawler-qq-heartbeat.json", payload)


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def qq_settings(config: dict) -> dict[str, Any]:
    block = config.get("crawlers") if isinstance(config.get("crawlers"), dict) else {}
    qq = block.get("qqgroup") if isinstance(block.get("qqgroup"), dict) else {}
    watch_dirs = qq.get("watch_dirs") or config.get("qq_watch_dirs") or [r"E:\图片"]
    if isinstance(watch_dirs, str):
        watch_dirs = [watch_dirs]
    layout = str(qq.get("layout") or "account").strip().lower()
    if layout not in {"account", "group_account"}:
        layout = "account"
    default_group_key = str(
        qq.get("default_group_key") or "legacy"
    ).strip()
    default_group_label = str(
        qq.get("default_group_label") or "历史未分组"
    ).strip()
    raw_sources = qq.get("sources")
    sources: list[dict[str, str]] = []
    if isinstance(raw_sources, list):
        for raw in raw_sources:
            if not isinstance(raw, dict) or not str(raw.get("path") or "").strip():
                continue
            source_layout = str(raw.get("layout") or layout).strip().lower()
            if source_layout not in {"account", "group_account"}:
                source_layout = layout
            sources.append(
                {
                    "path": str(raw["path"]),
                    "layout": source_layout,
                    "default_group_key": str(
                        raw.get("default_group_key") or default_group_key
                    ),
                    "default_group_label": str(
                        raw.get("default_group_label") or default_group_label
                    ),
                }
            )
    if not sources:
        sources = [
            {
                "path": str(path),
                "layout": layout,
                "default_group_key": default_group_key,
                "default_group_label": default_group_label,
            }
            for path in watch_dirs
        ]
    return {
        "enabled": bool(qq.get("enabled", True)),
        "watch_dirs": [source["path"] for source in sources],
        "sources": sources,
        "interval_sec": max(30, int(qq.get("interval_sec", 300) or 300)),
        "hardlink": bool(qq.get("hardlink", True)),
        "max_files_per_run": max(0, int(qq.get("max_files_per_run", 0) or 0)),
    }


def import_one(
    *,
    src: Path,
    identity,
    parsed,
    images_root: Path,
    hardlink: bool,
    work_id_override: int | None = None,
) -> int:
    return import_parsed_nai(
        src=src,
        identity=identity,
        parsed=parsed,
        images_root=images_root,
        hardlink=hardlink,
        work_id_override=work_id_override,
        commit=False,
    )


def rebuild_group_index() -> list[dict[str, Any]]:
    db = get_db(GALLERY_QQ)
    groups = build_group_index(db)
    save_group_index(GALLERY_QQ, groups)
    return groups


def _write_rejection_report(
    root: Path,
    *,
    run_id: str,
    reasons: Counter[str],
    examples: dict[str, list[dict[str, str]]],
) -> None:
    path = root / "logs" / "qq-rejections-latest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "run_id": run_id,
        "parser_version": PARSER_VERSION,
        "rejected": sum(reasons.values()),
        "by_reason": dict(reasons.most_common()),
        "examples": examples,
        "policy": "only parseable NovelAI metadata is visible; source files are preserved",
        "updated_at": now_iso(),
    }
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def crawl_once(config: dict, *, root: Path) -> dict[str, Any]:
    settings = qq_settings(config)
    ensure_gallery_dirs(GALLERY_QQ)
    spec_images = ensure_gallery_dirs(GALLERY_QQ).images_dir
    db = get_db(GALLERY_QQ)
    ensure_ingest_schema(db)
    duplicate_repair = repair_interrupted_upgrade_duplicates(db)
    ingest_cache = load_ingest_cache(db)
    existing_work_ids = load_existing_work_id_index(db)

    imported = 0
    parsed_nai = 0
    skipped_unchanged = 0
    skipped_missing = 0
    accounts_hit: set[tuple[str, str]] = set()
    max_files = settings["max_files_per_run"]
    run_id = uuid.uuid4().hex
    scanned = 0
    failed = 0
    rejection_reasons: Counter[str] = Counter()
    rejection_examples: dict[str, list[dict[str, str]]] = {}
    ledger_rows: list[tuple[Any, ...]] = []
    last_heartbeat = time.monotonic()

    write_heartbeat(
        root,
        "running",
        "qq NAI metadata scan started",
        run_id=run_id,
        parser_version=PARSER_VERSION,
        imported=0,
        scanned=0,
        failed=0,
        rejected=0,
    )

    for source in settings["sources"]:
        watch_path = Path(source["path"])
        if not watch_path.is_dir():
            print(f"[qq] skip missing watch dir: {watch_path}")
            skipped_missing += 1
            continue
        print(
            f"[qq] scanning {watch_path} layout={source['layout']} "
            f"parser={PARSER_VERSION}"
        )
        for candidate in iter_qq_images(
            watch_path,
            layout=source["layout"],
            default_group_key=source["default_group_key"],
            default_group_label=source["default_group_label"],
        ):
            src = candidate.source
            identity = candidate.identity
            scanned += 1
            src_id = source_id(src)
            try:
                stat = src.stat()
            except OSError as exc:
                failed += 1
                print(f"[qq] stat fail {src.name}: {exc}")
                continue
            cached = ingest_cache.get(src_id)
            if cached and cached[:3] == (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                PARSER_VERSION,
            ):
                skipped_unchanged += 1
                continue

            status = "rejected"
            reason = ""
            work_id: int | None = None
            if looks_like_comfy(src):
                reason = "comfy_path"
            elif not identity.group_key or not identity.account_key:
                reason = "identity_missing"
            else:
                parsed = parse_nai_image(src)
                reason = parsed.reason
                if parsed.accepted:
                    try:
                        work_id = import_one(
                            src=src,
                            identity=identity,
                            parsed=parsed,
                            images_root=spec_images,
                            hardlink=settings["hardlink"],
                            work_id_override=existing_work_ids.get(
                                work_identity_key(identity, src.stem)
                            ),
                        )
                        existing_work_ids[
                            work_identity_key(identity, src.stem)
                        ] = work_id
                        status = "accepted"
                        parsed_nai += 1
                        imported += 1
                        accounts_hit.add(
                            (identity.group_key, identity.account_key)
                        )
                        if imported % 50 == 0:
                            print(
                                f"[qq] imported={imported} "
                                f"last={identity.group_label}/"
                                f"{identity.account_label}/{src.name}"
                            )
                    except Exception as exc:
                        status = "error"
                        reason = "import_error"
                        failed += 1
                        print(f"[qq] import fail {src.name}: {exc}")

            if status == "rejected":
                rejection_reasons[reason or "rejected"] += 1
                bucket = rejection_examples.setdefault(
                    reason or "rejected",
                    [],
                )
                if len(bucket) < 10:
                    bucket.append(
                        {
                            "file": src.name,
                            "group": identity.group_label,
                            "account": identity.account_label,
                        }
                    )

            ledger_rows.append(
                (
                    src_id,
                    src.name,
                    int(stat.st_size),
                    int(stat.st_mtime_ns),
                    PARSER_VERSION,
                    status,
                    reason or status,
                    identity.group_key,
                    identity.group_label,
                    identity.account_key,
                    identity.account_label,
                    work_id,
                    now_iso(),
                )
            )
            ingest_cache[src_id] = (
                int(stat.st_size),
                int(stat.st_mtime_ns),
                PARSER_VERSION,
                status,
            )
            if len(ledger_rows) >= 100:
                record_ingest_rows(db, ledger_rows)
                ledger_rows.clear()

            now = time.monotonic()
            if scanned % 250 == 0 or now - last_heartbeat >= 3.0:
                write_heartbeat(
                    root,
                    "running",
                    f"scanned={scanned} accepted={imported}",
                    run_id=run_id,
                    parser_version=PARSER_VERSION,
                    scanned=scanned,
                    imported=imported,
                    rejected=sum(rejection_reasons.values()),
                    skipped_unchanged=skipped_unchanged,
                    failed=failed,
                    accounts=len(accounts_hit),
                )
                last_heartbeat = now
            if max_files and imported >= max_files:
                break
        if max_files and imported >= max_files:
            break

    record_ingest_rows(db, ledger_rows)
    groups = rebuild_group_index()
    _write_rejection_report(
        root,
        run_id=run_id,
        reasons=rejection_reasons,
        examples=rejection_examples,
    )
    total = db.count_works()
    result = {
        "ok": failed == 0,
        "run_id": run_id,
        "parser_version": PARSER_VERSION,
        "scanned": scanned,
        "failed": failed,
        "gallery": GALLERY_QQ,
        "imported": imported,
        "parsed_nai": parsed_nai,
        "rejected": sum(rejection_reasons.values()),
        "rejected_by_reason": dict(rejection_reasons.most_common()),
        "skipped_unchanged": skipped_unchanged,
        # Compatibility for existing status consumers.
        "skipped_seen": skipped_unchanged,
        "skipped_missing": skipped_missing,
        "watch_dirs": settings["watch_dirs"],
        "accounts_hit": len(accounts_hit),
        "groups_total": sum(item.get("kind") == "group" for item in groups),
        "accounts_total": sum(item.get("kind") == "account" for item in groups),
        "total_works": total,
        "duplicates_repaired": duplicate_repair["removed"],
        "finished_at": now_iso(),
    }
    terminal_status = "complete" if failed == 0 else "partial"
    terminal_message = (
        "qq strict NAI scan complete"
        if failed == 0
        else f"qq strict NAI scan partial: {failed} failed"
    )
    write_heartbeat(root, terminal_status, terminal_message, **result)
    print(json.dumps(result, ensure_ascii=False))
    return result


def crawl_watch(config: dict, *, root: Path) -> None:
    settings = qq_settings(config)
    interval = settings["interval_sec"]
    print(f"[qq] watch mode interval={interval}s dirs={settings['watch_dirs']}")
    while True:
        try:
            crawl_once(config, root=root)
        except Exception as exc:
            write_heartbeat(root, "error", str(exc))
            print(f"[qq] cycle error: {exc}")
        time.sleep(interval)


def revalidate_existing(
    *,
    root: Path,
    apply: bool,
    batch_size: int,
    all_batches: bool,
) -> dict[str, Any]:
    spec = ensure_gallery_dirs(GALLERY_QQ)
    db = get_db(GALLERY_QQ)
    totals: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    batches = 0
    last: dict[str, Any] = {}
    while True:
        last = revalidate_existing_batch(
            db,
            spec.images_dir,
            limit=max(1, int(batch_size)),
            apply=apply,
        )
        batches += 1
        totals["processed"] += int(last["processed"])
        totals["accepted"] += int(last["accepted"])
        totals["rejected"] += int(last["rejected"])
        reasons.update(last.get("rejected_by_reason") or {})
        write_heartbeat(
            root,
            "running",
            "qq legacy catalog revalidation",
            parser_version=PARSER_VERSION,
            apply=apply,
            batches=batches,
            processed=totals["processed"],
            accepted=totals["accepted"],
            rejected=totals["rejected"],
            remaining=last["remaining"],
        )
        print(
            "[qq-revalidate] "
            f"batch={batches} processed={totals['processed']} "
            f"accepted={totals['accepted']} rejected={totals['rejected']} "
            f"remaining={last['remaining']} apply={apply}"
        )
        if (
            not all_batches
            or not apply
            or int(last["remaining"]) <= 0
            or int(last["processed"]) <= 0
        ):
            break
    if apply:
        rebuild_group_index()
    result = {
        "ok": True,
        "apply": apply,
        "parser_version": PARSER_VERSION,
        "batches": batches,
        "processed": totals["processed"],
        "accepted": totals["accepted"],
        "rejected": totals["rejected"],
        "rejected_by_reason": dict(reasons.most_common()),
        "remaining": int(last.get("remaining") or 0),
        "total_works": db.count_works(),
        "source_files_deleted": 0,
        "finished_at": now_iso(),
    }
    report = root / "logs" / "qq-revalidation-latest.json"
    tmp = report.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(report)
    status = "complete" if result["remaining"] == 0 else "partial"
    write_heartbeat(
        root,
        status,
        "qq legacy catalog revalidation finished",
        **result,
    )
    print(json.dumps(result, ensure_ascii=False))
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description="QQ group gallery crawler")
    parser.add_argument(
        "--config",
        default=str(ROOT / "config.json"),
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="Single scan then exit (default)",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="Loop forever with interval",
    )
    parser.add_argument(
        "--revalidate-existing",
        action="store_true",
        help="Re-parse legacy visible works using the strict NAI policy",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Apply revalidation; rejected works leave the catalog but files remain",
    )
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument(
        "--all-batches",
        action="store_true",
        help="Continue revalidation until no legacy work remains",
    )
    args = parser.parse_args()
    root = Path(args.config).resolve().parent
    config = normalize_config(load_config(Path(args.config)), root)
    (root / "logs").mkdir(parents=True, exist_ok=True)

    settings = qq_settings(config)
    if not settings["enabled"]:
        print("[qq] disabled in config")
        return 0

    if args.revalidate_existing:
        result = revalidate_existing(
            root=root,
            apply=args.apply,
            batch_size=args.batch_size,
            all_batches=args.all_batches,
        )
        return 0 if result.get("ok") else 1
    if args.watch:
        crawl_watch(config, root=root)
        return 0
    crawl_once(config, root=root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
