#!/usr/bin/env python3
"""从 Danbooru tag 镜像下载画风识别用 tag，写入本地缓存。"""

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
OUT_PATH = DATA_DIR / "danbooru_style_tags.json"

MIRROR_FULL = (
    "https://huggingface.co/datasets/deepghs/site_tags/resolve/main/"
    "danbooru.donmai.us/tags.json"
)

# category: 0=general 1=artist 3=copyright 4=character 5=meta
HF_ENTRY_RE = re.compile(
    r'\{"id":\s*\d+,\s*"name":\s*"([^"\\]+)"\s*,\s*"post_count":\s*(\d+)'
    r',\s*"category":\s*(\d+)\b'
)

STYLE_HINT_RE = re.compile(
    r"("
    r"style|artstyle|official|artist|drawn|illustration|painting|watercolor|"
    r"oil_painting|sketch|lineart|monochrome|flat_color|cel_shading|"
    r"anime_coloring|retro|90s|1980s|1990s|2000s|toon|cartoon|comic|"
    r"manga|chibi|pixel_art|vector|cg|render|realistic|photorealistic|"
    r"impressionism|surrealism|expressionism|ukiyo|sumi|gouache|pastel"
    r")",
    re.IGNORECASE,
)

STYLE_SEEDS = {
    "official style",
    "official_style",
    "official art",
    "official_art",
    "official color",
    "official_color",
    "anime style",
    "anime_style",
    "manga style",
    "manga_style",
    "game cg",
    "game_cg",
    "visual novel cg",
    "visual_novel_cg",
    "watercolor",
    "sketch",
    "lineart",
    "cel shading",
    "cel_shading",
    "flat color",
    "flat_color",
    "pixel art",
    "pixel_art",
    "retro artstyle",
    "retro_artstyle",
    "chibi",
}


def _norm(name: str) -> str:
    return str(name or "").strip().lower()


def _is_style_general(name: str) -> bool:
    low = _norm(name)
    if not low:
        return False
    if low in STYLE_SEEDS:
        return True
    return bool(STYLE_HINT_RE.search(low))


def fetch_from_hf_stream(
    *,
    chunk_size: int = 1024 * 1024,
    min_artist_posts: int = 25,
    min_style_posts: int = 5,
) -> dict:
    import httpx

    artists: dict[str, int] = {}
    styles: dict[str, int] = {tag: 0 for tag in STYLE_SEEDS}
    meta: dict[str, int] = {}
    copyrights: dict[str, int] = {}

    print(f"流式扫描完整镜像: {MIRROR_FULL}")
    print(
        "提取 category 1 画师 · 0/5 画风/元标签 · 3 版权辅助；"
        "结果保存到本地 JSON"
    )

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
                    name = _norm(m.group(1))
                    posts = int(m.group(2))
                    cat = int(m.group(3))
                    if not name:
                        continue
                    if cat == 1 and posts >= min_artist_posts:
                        artists[name] = posts
                    elif cat == 3:
                        copyrights[name] = posts
                    elif cat == 5 and posts >= min_style_posts and _is_style_general(name):
                        meta[name] = posts
                    elif cat == 0 and posts >= min_style_posts and _is_style_general(name):
                        styles[name] = posts
                tail = text[-1024:]
                if total and downloaded % (chunk_size * 4) < chunk_size:
                    pct = downloaded * 100 // total
                    mb_done = downloaded // (1024 * 1024)
                    mb_total = total // (1024 * 1024)
                    print(
                        f"  …{mb_done}MB/{mb_total}MB ({pct}%) · "
                        f"画师 {len(artists)} · 画风 {len(styles)} · "
                        f"meta {len(meta)} · 版权 {len(copyrights)}"
                    )

    return {
        "source": "deepghs_huggingface_stream",
        "artists": artists,
        "styles": styles,
        "meta": meta,
        "copyrights": copyrights,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="下载 Danbooru 画风识别 tag 到本地")
    parser.add_argument("--min-artist-posts", type=int, default=25)
    parser.add_argument("--min-style-posts", type=int, default=5)
    args = parser.parse_args()

    t0 = time.time()
    part = fetch_from_hf_stream(
        min_artist_posts=max(1, args.min_artist_posts),
        min_style_posts=max(1, args.min_style_posts),
    )
    payload = {
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "sources": [part["source"]],
        "min_artist_posts": max(1, args.min_artist_posts),
        "min_style_posts": max(1, args.min_style_posts),
        "artist_count": len(part["artists"]),
        "style_count": len(part["styles"]),
        "meta_count": len(part["meta"]),
        "copyright_count": len(part["copyrights"]),
        "artists": dict(sorted(part["artists"].items())),
        "styles": dict(sorted(part["styles"].items())),
        "meta": dict(sorted(part["meta"].items())),
        "copyrights": dict(sorted(part["copyrights"].items())),
    }
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    elapsed = round(time.time() - t0, 1)
    size_mb = OUT_PATH.stat().st_size / (1024 * 1024)
    print(f"已写入 {OUT_PATH} ({size_mb:.1f} MB)")
    print(
        f"画师 {payload['artist_count']} · 画风 {payload['style_count']} · "
        f"meta {payload['meta_count']} · 版权 {payload['copyright_count']} · "
        f"耗时 {elapsed}s"
    )


if __name__ == "__main__":
    main()
