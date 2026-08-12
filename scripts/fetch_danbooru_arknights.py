#!/usr/bin/env python3
"""从 Danbooru 镜像拉取明日方舟角色 tag，缓存到本地（无需直连 d 站 API）。"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data"
OUT_PATH = DATA_DIR / "danbooru_arknights.json"
MIRROR_SMALL = (
    "https://raw.githubusercontent.com/nichind/Danbooru-Tags/main/tags.json"
)
MIRROR_FULL = (
    "https://huggingface.co/datasets/deepghs/site_tags/resolve/main/"
    "danbooru.donmai.us/tags.json"
)

ARK_RE = re.compile(r"arknights|アークナイツ|明日方舟", re.IGNORECASE)
CHAR_SUFFIX_RE = re.compile(r"\(arknights\)|_\(arknights\)", re.IGNORECASE)
# HuggingFace 大文件流式匹配：category=4 角色且 name 含 arknights
HF_CHAR_RE = re.compile(
    r'\{"id":\s*\d+,\s*"name":\s*"([^"]*arknights[^"]*)"\s*,\s*"post_count":\s*(\d+)'
    r',\s*"category":\s*4\b',
    re.IGNORECASE,
)
HF_COPY_RE = re.compile(
    r'\{"id":\s*\d+,\s*"name":\s*"([^"]*arknights[^"]*)"\s*,\s*"post_count":\s*(\d+)'
    r',\s*"category":\s*3\b',
    re.IGNORECASE,
)


def _is_ark_character(name: str) -> bool:
    low = name.strip().lower()
    if not low or not ARK_RE.search(low):
        return False
    if CHAR_SUFFIX_RE.search(low):
        return True
    if low in {"arknights", "arknights:_endfield"}:
        return False
    return True


def fetch_from_small_mirror() -> dict:
    import httpx

    print(f"下载轻量镜像: {MIRROR_SMALL}")
    resp = httpx.get(MIRROR_SMALL, timeout=120.0, follow_redirects=True)
    resp.raise_for_status()
    data = resp.json()
    characters: dict[str, int] = {}
    copyrights: dict[str, int] = {}
    if isinstance(data, dict):
        for name, count in data.items():
            low = str(name).strip().lower()
            if not ARK_RE.search(low):
                continue
            posts = int(count) if str(count).isdigit() else 0
            if _is_ark_character(low):
                characters[low] = max(characters.get(low, 0), posts)
            else:
                copyrights[low] = max(copyrights.get(low, 0), posts)
    return {
        "source": "nichind_github",
        "characters": characters,
        "copyrights": copyrights,
    }


def fetch_from_hf_stream(*, chunk_size: int = 1024 * 1024) -> dict:
    import httpx

    print(f"流式扫描完整镜像: {MIRROR_FULL}")
    characters: dict[str, int] = {}
    copyrights: dict[str, int] = {}
    tail = ""
    downloaded = 0
    with httpx.Client(timeout=600.0, follow_redirects=True) as client:
        with client.stream("GET", MIRROR_FULL) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            for chunk in resp.iter_bytes(chunk_size=chunk_size):
                downloaded += len(chunk)
                text = tail + chunk.decode("utf-8", errors="ignore")
                for m in HF_CHAR_RE.finditer(text):
                    name = m.group(1).strip().lower()
                    posts = int(m.group(2))
                    if _is_ark_character(name):
                        characters[name] = max(characters.get(name, 0), posts)
                for m in HF_COPY_RE.finditer(text):
                    name = m.group(1).strip().lower()
                    posts = int(m.group(2))
                    copyrights[name] = max(copyrights.get(name, 0), posts)
                tail = text[-512:]
                if total:
                    pct = downloaded * 100 // total
                    if downloaded % (chunk_size * 8) < chunk_size:
                        print(f"  …已下载 {downloaded // (1024*1024)}MB / {total // (1024*1024)}MB ({pct}%)")
    return {
        "source": "deepghs_huggingface_stream",
        "characters": characters,
        "copyrights": copyrights,
    }


def merge_results(*parts: dict) -> dict:
    characters: dict[str, int] = {}
    copyrights: dict[str, int] = {}
    sources: list[str] = []
    for part in parts:
        sources.append(str(part.get("source") or ""))
        for name, posts in (part.get("characters") or {}).items():
            characters[name] = max(characters.get(name, 0), int(posts or 0))
        for name, posts in (part.get("copyrights") or {}).items():
            copyrights[name] = max(copyrights.get(name, 0), int(posts or 0))
    return {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "sources": [s for s in sources if s],
        "character_count": len(characters),
        "copyright_count": len(copyrights),
        "characters": dict(sorted(characters.items(), key=lambda x: (-x[1], x[0]))),
        "copyrights": dict(sorted(copyrights.items(), key=lambda x: (-x[1], x[0]))),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="拉取 Danbooru 明日方舟 tag 到本地")
    parser.add_argument(
        "--full",
        action="store_true",
        help="额外流式扫描 HuggingFace 完整 Danbooru 库（约 360MB，更全）",
    )
    args = parser.parse_args()

    t0 = time.time()
    parts = [fetch_from_small_mirror()]
    if args.full:
        parts.append(fetch_from_hf_stream())

    payload = merge_results(*parts)
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    elapsed = round(time.time() - t0, 1)
    print(f"已写入 {OUT_PATH}")
    print(
        f"明日方舟角色 tag: {payload['character_count']} · "
        f"版权/系列: {payload['copyright_count']} · 耗时 {elapsed}s"
    )


if __name__ == "__main__":
    main()