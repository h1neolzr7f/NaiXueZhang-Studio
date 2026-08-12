"""One-shot strict NovelAI import for local QQ image exports."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from crawler_qq import crawl_once  # noqa: E402
from qq_gallery_ingest import looks_like_comfy  # noqa: E402,F401


def import_qq(
    root: Path,
    *,
    limit: int = 0,
    copy_files: bool = True,
    layout: str = "account",
    default_group_key: str = "legacy",
    default_group_label: str = "历史未分组",
) -> dict:
    config = {
        "crawlers": {
            "qqgroup": {
                "enabled": True,
                "watch_dirs": [str(root)],
                "layout": layout,
                "default_group_key": default_group_key,
                "default_group_label": default_group_label,
                "hardlink": not copy_files,
                "max_files_per_run": max(0, int(limit)),
            }
        }
    }
    result = crawl_once(config, root=ROOT)
    result["source"] = str(root)
    return result


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import only metadata-verified NovelAI images from QQ folders"
    )
    parser.add_argument(
        "--source",
        default=r"E:\图片",
        help="QQ export root",
    )
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--hardlink", action="store_true")
    parser.add_argument(
        "--layout",
        choices=["account", "group_account"],
        default="account",
        help="account: <account>/image; group_account: <group>/<account>/image",
    )
    parser.add_argument("--default-group-key", default="legacy")
    parser.add_argument("--default-group-label", default="历史未分组")
    args = parser.parse_args()
    source = Path(args.source)
    if not source.is_dir():
        print(f"ERROR: source not found: {source}")
        return 2
    result = import_qq(
        source,
        limit=args.limit,
        copy_files=not args.hardlink,
        layout=args.layout,
        default_group_key=args.default_group_key,
        default_group_label=args.default_group_label,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
