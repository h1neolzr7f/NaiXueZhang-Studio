#!/usr/bin/env python3
"""从 HuggingFace Danbooru 镜像流式提取贵物/异种相关 tag，写入 danbooru_creature.json。"""

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
OUT_PATH = DATA_DIR / "danbooru_creature.json"

MIRROR_FULL = (
    "https://huggingface.co/datasets/deepghs/site_tags/resolve/main/"
    "danbooru.donmai.us/tags.json"
)

HF_ENTRY_RE = re.compile(
    r'\{"id":\s*\d+,\s*"name":\s*"([^"\\]+)"\s*,\s*"post_count":\s*(\d+)'
    r',\s*"category":\s*(\d+)\b'
)

# 跳过兽耳萌属性（kemonomimi），只抓真·贵物/异种
SKIP_EXACT = frozenset(
    {
        "animal_ears",
        "cat_ears",
        "fox_ears",
        "rabbit_ears",
        "dog_ears",
        "wolf_ears",
        "horse_ears",
        "cow_ears",
        "kemonomimi",
        "cat_girl",
        "fox_girl",
        "wolf_girl",
        "rabbit_girl",
    }
)


def _load_groups() -> dict:
    if GROUPS_PATH.exists():
        return json.loads(GROUPS_PATH.read_text(encoding="utf-8"))
    return {}


def _creature_needles(groups: dict) -> tuple[str, ...]:
    seeds = [str(s).lower() for s in (groups.get("creature_substrings") or []) if s]
    seeds.extend(str(s).lower() for s in (groups.get("creature") or []) if s)
    return tuple(dict.fromkeys(seeds))


def _is_creature_name(name: str, *, needles: tuple[str, ...]) -> bool:
    low = name.strip().lower()
    if not low or low in SKIP_EXACT:
        return False
    if low.endswith("_ears") or low.endswith(" ears"):
        return False
    if low.endswith("_tail") and "horse" not in low and "insect" not in low:
        return False
    for needle in needles:
        if needle and needle in low:
            return True
    return False


def fetch_creature_tags(*, min_posts: int = 5, chunk_size: int = 1024 * 1024) -> dict:
    import httpx

    groups = _load_groups()
    needles = _creature_needles(groups)
    seed = {str(t).lower() for t in (groups.get("creature") or []) if t}
    found: set[str] = set(seed)

    print(f"流式扫描: {MIRROR_FULL}")
    print(f"子串规则 {len(needles)} 条 · 种子 {len(seed)} 条 · 最低 post={min_posts}")

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
                    if posts < min_posts or cat not in {0, 4, 3}:
                        continue
                    if _is_creature_name(name, needles=needles):
                        found.add(name)
                tail = text[-1024:]
                if total and downloaded % (chunk_size * 8) < chunk_size:
                    pct = downloaded * 100 // total
                    print(f"  …{pct}% · 贵物 tag {len(found)}")

    return {
        "source": "deepghs_huggingface_creature_stream",
        "tags": sorted(found),
        "tag_count": len(found),
        "min_posts": min_posts,
        "needle_count": len(needles),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="从 Danbooru HF 镜像拉取贵物/异种 tag")
    parser.add_argument("--min-posts", type=int, default=5, help="最低 post 数（默认 5）")
    args = parser.parse_args()

    t0 = time.time()
    part = fetch_creature_tags(min_posts=max(1, args.min_posts))
    payload = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        **part,
    }
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    elapsed = round(time.time() - t0, 1)
    size_kb = OUT_PATH.stat().st_size / 1024
    print(f"已写入 {OUT_PATH} ({size_kb:.1f} KB) · {payload['tag_count']} tags · {elapsed}s")
    print("请运行 python scripts/build_char_tag_db.py 或重启图库以加载新索引。")


if __name__ == "__main__":
    main()