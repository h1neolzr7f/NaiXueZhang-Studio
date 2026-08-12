#!/usr/bin/env python3
"""从 HuggingFace Danbooru 镜像流式提取识别用 tag（角色/版权/外貌），排除画师。"""

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
GROUPS_PATH = DATA_DIR / "char_tag_groups.json"
OUT_PATH = DATA_DIR / "danbooru_recognition.json"

MIRROR_FULL = (
    "https://huggingface.co/datasets/deepghs/site_tags/resolve/main/"
    "danbooru.donmai.us/tags.json"
)

# category: 0=general 1=artist 3=copyright 4=character 5=meta
HF_ENTRY_RE = re.compile(
    r'\{"id":\s*\d+,\s*"name":\s*"([^"\\]+)"\s*,\s*"post_count":\s*(\d+)'
    r',\s*"category":\s*(\d+)\b'
)
APPEAR_RE = re.compile(
    r"(?:_hair|_eyes|_skin|_ears|_tail| hair| eyes| skin| ears| tail|horns)$",
    re.IGNORECASE,
)
from char_tag_db import _CLOTHING_HINTS as CLOTHING_HINTS


def _load_groups() -> dict:
    if GROUPS_PATH.exists():
        return json.loads(GROUPS_PATH.read_text(encoding="utf-8"))
    return {}


def _is_appearance_general(name: str, *, groups: dict, body_known: set[str]) -> bool:
    low = name.strip().lower()
    if not low:
        return False
    if low in body_known:
        return True
    appear_known = {t.lower() for t in groups.get("appearance_exact") or []}
    if low in appear_known:
        return True
    if APPEAR_RE.search(low):
        return True
    for suffix in groups.get("appearance_suffixes") or []:
        if low.endswith(str(suffix).lower()):
            return True
    if low in CLOTHING_HINTS:
        return True
    if re.search(r"\b(hair|eyes|skin|ears|tail|horns)\b", low):
        return True
    return False


def fetch_from_hf_stream(
    *,
    chunk_size: int = 1024 * 1024,
    min_appearance_posts: int = 10,
) -> dict:
    import httpx

    groups = _load_groups()
    body_known = {t.lower() for t in groups.get("body") or []}

    characters: set[str] = set()
    copyrights: set[str] = set()
    appearance: set[str] = set()
    body_extra: set[str] = set()

    print(f"流式扫描完整镜像: {MIRROR_FULL}")
    print("提取 category 4 角色 · 3 版权 · 0 外貌相关（跳过画师 category 1）")

    tail = ""
    downloaded = 0
    with httpx.Client(timeout=600.0, follow_redirects=True) as client:
        with client.stream("GET", MIRROR_FULL) as resp:
            resp.raise_for_status()
            total = int(resp.headers.get("content-length") or 0)
            for chunk in resp.iter_bytes(chunk_size=chunk_size):
                downloaded += len(chunk)
                text = tail + chunk.decode("utf-8", errors="ignore")
                for m in HF_ENTRY_RE.finditer(text):
                    name = m.group(1).strip().lower()
                    posts = int(m.group(2))
                    cat = int(m.group(3))
                    if not name:
                        continue
                    if cat == 4:
                        characters.add(name)
                    elif cat == 3:
                        copyrights.add(name)
                    elif cat == 0:
                        if low := name:
                            if low in body_known:
                                body_extra.add(low)
                            elif posts >= min_appearance_posts and _is_appearance_general(
                                low, groups=groups, body_known=body_known
                            ):
                                appearance.add(low)
                    # cat 1 artist / cat 5 meta — 跳过
                tail = text[-1024:]
                if total and downloaded % (chunk_size * 4) < chunk_size:
                    pct = downloaded * 100 // total
                    mb_done = downloaded // (1024 * 1024)
                    mb_total = total // (1024 * 1024)
                    print(
                        f"  …{mb_done}MB/{mb_total}MB ({pct}%) · "
                        f"角色 {len(characters)} · 版权 {len(copyrights)} · "
                        f"外貌 {len(appearance)}"
                    )

    return {
        "source": "deepghs_huggingface_stream",
        "characters": sorted(characters),
        "copyrights": sorted(copyrights),
        "appearance": sorted(appearance),
        "body_extra": sorted(body_extra),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="从 HF Danbooru 镜像构建识别用 tag 缓存（无画师）"
    )
    parser.add_argument(
        "--min-appearance-posts",
        type=int,
        default=10,
        metavar="N",
        help="general 外貌 tag 最低 post 数（默认 10，适度过滤冷门 tag）",
    )
    args = parser.parse_args()

    t0 = time.time()
    part = fetch_from_hf_stream(min_appearance_posts=max(1, args.min_appearance_posts))
    payload = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "sources": [part["source"]],
        "character_count": len(part["characters"]),
        "copyright_count": len(part["copyrights"]),
        "appearance_count": len(part["appearance"]),
        "body_extra_count": len(part["body_extra"]),
        "min_appearance_posts": max(1, args.min_appearance_posts),
        "characters": part["characters"],
        "copyrights": part["copyrights"],
        "appearance": part["appearance"],
        "body_extra": part["body_extra"],
    }
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    elapsed = round(time.time() - t0, 1)
    size_mb = OUT_PATH.stat().st_size / (1024 * 1024)
    print(f"已写入 {OUT_PATH} ({size_mb:.1f} MB)")
    print(
        f"角色 {payload['character_count']} · 版权 {payload['copyright_count']} · "
        f"外貌 {payload['appearance_count']} · 体型补充 {payload['body_extra_count']} · "
        f"耗时 {elapsed}s"
    )


if __name__ == "__main__":
    main()