#!/usr/bin/env python3
"""从本地图库 + 可选 Danbooru 构建角色 tag 索引（离线优先）。"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = ROOT / "scripts"
for p in (str(ROOT), str(SCRIPTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

DATA_DIR = ROOT / "data"
DB_PATH = DATA_DIR / "aitag.db"
OUT_PATH = DATA_DIR / "char_tag_index.json"
GROUPS_PATH = DATA_DIR / "char_tag_groups.json"
DANBOORU_CACHE = DATA_DIR / "danbooru_characters.json"
DANBOORU_ARK_PATH = DATA_DIR / "danbooru_arknights.json"
DANBOORU_REC_PATH = DATA_DIR / "danbooru_recognition.json"

CHAR_RE = re.compile(
    r"^(.+?)(?:_\(([^)]+)\)|\s+\(([^)]+)\))$",
    re.IGNORECASE,
)
ARK_RE = re.compile(r"arknights|アークナイツ", re.IGNORECASE)
APPEAR_RE = re.compile(
    r"(?:_hair|_eyes|_skin|_ears|_tail| hair| eyes| skin| ears| tail|horns)$",
    re.IGNORECASE,
)


def load_groups() -> dict:
    return json.loads(GROUPS_PATH.read_text(encoding="utf-8"))


def mine_local_db() -> tuple[Counter, Counter, Counter]:
    """返回 (character_tags, appearance_tags, body_tags) 词频。"""
    chars: Counter = Counter()
    appear: Counter = Counter()
    body: Counter = Counter()
    groups = load_groups()
    body_known = {t.lower() for t in groups.get("body") or []}
    appear_known = {t.lower() for t in groups.get("appearance_exact") or []}

    conn = sqlite3.connect(DB_PATH)
    rows = conn.execute(
        "SELECT ai_json FROM work_images WHERE ai_json IS NOT NULL"
    ).fetchall()
    conn.close()

    for (raw,) in rows:
        try:
            ai = json.loads(raw)
            comment = ai.get("Comment") or ai
            if isinstance(comment, str):
                comment = json.loads(comment)
            cap = ((comment.get("v4_prompt") or {}).get("caption") or {})
            for item in cap.get("char_captions") or []:
                tags = [
                    t.strip()
                    for t in str(item.get("char_caption") or "").split(",")
                    if t.strip()
                ]
                for i, tag in enumerate(tags):
                    low = tag.lower()
                    if CHAR_RE.match(low) or (
                        ARK_RE.search(low) and ("(" in low or "_(" in low)
                    ):
                        chars[low] += 3 + max(0, 2 - i)
                    elif low in body_known:
                        body[low] += 1
                    elif low in appear_known or APPEAR_RE.search(low):
                        appear[low] += 1
                    elif i == 0 and len(low) > 2 and not low.startswith("source#"):
                        chars[low] += 1
        except Exception:
            continue
    return chars, appear, body


def fetch_danbooru_characters(*, max_pages: int = 30, delay: float = 1.2) -> list[str]:
    """可选：拉取 Danbooru 角色类 tag（category=4），结果缓存本地。"""
    if DANBOORU_CACHE.exists() and max_pages <= 0:
        data = json.loads(DANBOORU_CACHE.read_text(encoding="utf-8"))
        return list(data.get("tags") or [])

    try:
        import httpx
    except ImportError:
        return []

    tags: list[str] = []
    base = "https://danbooru.donmai.us/tags.json"
    with httpx.Client(timeout=30.0, headers={"User-Agent": "aitag-mirror/1.0"}) as client:
        for page in range(1, max_pages + 1):
            resp = client.get(
                base,
                params={
                    "search[category]": "4",
                    "search[order]": "count",
                    "limit": 1000,
                    "page": page,
                },
            )
            if resp.status_code != 200:
                break
            batch = resp.json()
            if not batch:
                break
            for item in batch:
                name = str(item.get("name") or "").strip().lower()
                if name:
                    tags.append(name)
            if len(batch) < 1000:
                break
            time.sleep(delay)

    if tags:
        DANBOORU_CACHE.write_text(
            json.dumps(
                {
                    "fetched_at": datetime.now().isoformat(timespec="seconds"),
                    "count": len(tags),
                    "tags": tags,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    return tags


def load_danbooru_arknights() -> dict:
    if not DANBOORU_ARK_PATH.exists():
        return {"characters": {}, "copyrights": {}}
    try:
        return json.loads(DANBOORU_ARK_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"characters": {}, "copyrights": {}}


def load_danbooru_recognition() -> dict:
    if not DANBOORU_REC_PATH.exists():
        return {}
    try:
        return json.loads(DANBOORU_REC_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def build_index(*, danbooru_pages: int = 0) -> dict:
    groups = load_groups()
    chars, appear, body = mine_local_db()

    characters = {t for t, c in chars.items() if c >= 2}
    # 高频单 tag 也纳入（避免漏角色）
    for t, c in chars.most_common(5000):
        if c >= 1 and (CHAR_RE.match(t) or ARK_RE.search(t)):
            characters.add(t)

    appearance = set(groups.get("appearance_exact") or [])
    for t, c in appear.items():
        if c >= 2:
            appearance.add(t)

    body_set = set(groups.get("body") or [])
    for t, c in body.items():
        if c >= 2:
            body_set.add(t)

    copyrights = {"arknights", "明日方舟", "アークナイツ", "arknights:_endfield"}
    for t in characters:
        m = CHAR_RE.match(t)
        if m:
            series = (m.group(2) or m.group(3) or "").strip().lower()
            if series:
                copyrights.add(series)

    ark_db = load_danbooru_arknights()
    for name in (ark_db.get("characters") or {}):
        low = str(name).strip().lower()
        if low:
            characters.add(low)
    for name in (ark_db.get("copyrights") or {}):
        low = str(name).strip().lower()
        if low:
            copyrights.add(low)

    rec_db = load_danbooru_recognition()
    for name in rec_db.get("characters") or []:
        low = str(name).strip().lower()
        if low:
            characters.add(low)
    for name in rec_db.get("copyrights") or []:
        low = str(name).strip().lower()
        if low:
            copyrights.add(low)
    for name in rec_db.get("appearance") or []:
        low = str(name).strip().lower()
        if low:
            appearance.add(low)
    for name in rec_db.get("body_extra") or []:
        low = str(name).strip().lower()
        if low:
            body_set.add(low)

    danbooru_tags: list[str] = []
    if danbooru_pages > 0:
        danbooru_tags = fetch_danbooru_characters(max_pages=danbooru_pages)
        for t in danbooru_tags:
            if ARK_RE.search(t) or t.endswith("_(arknights)"):
                characters.add(t)
    elif DANBOORU_CACHE.exists():
        danbooru_tags = json.loads(DANBOORU_CACHE.read_text(encoding="utf-8")).get(
            "tags"
        ) or []

    payload = {
        "version": 2,
        "built_at": datetime.now().isoformat(timespec="seconds"),
        "sources": {
            "local_db": str(DB_PATH),
            "danbooru_arknights": str(DANBOORU_ARK_PATH) if DANBOORU_ARK_PATH.exists() else "",
            "danbooru_arknights_count": int(ark_db.get("character_count") or len(ark_db.get("characters") or {})),
            "danbooru_recognition": str(DANBOORU_REC_PATH) if DANBOORU_REC_PATH.exists() else "",
            "danbooru_recognition_characters": int(rec_db.get("character_count") or len(rec_db.get("characters") or [])),
            "danbooru_recognition_copyrights": int(rec_db.get("copyright_count") or len(rec_db.get("copyrights") or [])),
            "danbooru_recognition_appearance": int(rec_db.get("appearance_count") or len(rec_db.get("appearance") or [])),
            "danbooru_cache": str(DANBOORU_CACHE) if DANBOORU_CACHE.exists() else "",
            "danbooru_fetched": len(danbooru_tags),
        },
        "characters": sorted(characters),
        "copyrights": sorted(copyrights),
        "gender_male": groups.get("gender_male") or [],
        "gender_female": groups.get("gender_female") or [],
        "body": sorted(body_set),
        "appearance": sorted(appearance),
        "meta": ["best quality", "absurdres", "highres", "masterpiece"],
    }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="构建本地角色 tag 索引")
    parser.add_argument(
        "--danbooru",
        type=int,
        default=0,
        metavar="PAGES",
        help="可选：直连 Danbooru API 页数（常被 CF 拦截，不推荐）",
    )
    parser.add_argument(
        "--fetch-arknights",
        action="store_true",
        help="先联网从镜像拉取明日方舟 Danbooru tag，再构建索引",
    )
    parser.add_argument(
        "--fetch-arknights-full",
        action="store_true",
        help="拉取时额外扫描 HuggingFace 完整库（更全，约 360MB）",
    )
    parser.add_argument(
        "--fetch-recognition",
        action="store_true",
        help="从 HF 镜像拉取全站角色+版权+外貌 tag（约 360MB，排除画师）",
    )
    parser.add_argument(
        "--min-appearance-posts",
        type=int,
        default=10,
        metavar="N",
        help="配合 --fetch-recognition：general 外貌 tag 最低 post 数",
    )
    args = parser.parse_args()

    if args.fetch_recognition:
        from fetch_danbooru_recognition import fetch_from_hf_stream

        print("联网拉取 D 站识别用 tag（角色/版权/外貌，无画师）…")
        part = fetch_from_hf_stream(
            min_appearance_posts=max(1, args.min_appearance_posts)
        )
        rec_payload = {
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
        DANBOORU_REC_PATH.write_text(
            json.dumps(rec_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"已缓存 {DANBOORU_REC_PATH} · 角色 {rec_payload['character_count']} · "
            f"版权 {rec_payload['copyright_count']} · 外貌 {rec_payload['appearance_count']}"
        )

    if args.fetch_arknights or args.fetch_arknights_full:
        from fetch_danbooru_arknights import fetch_from_hf_stream, fetch_from_small_mirror, merge_results

        print("联网拉取 Danbooru 明日方舟 tag…")
        parts = [fetch_from_small_mirror()]
        if args.fetch_arknights_full:
            parts.append(fetch_from_hf_stream())
        ark_payload = merge_results(*parts)
        DANBOORU_ARK_PATH.write_text(
            json.dumps(ark_payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(
            f"已缓存 {DANBOORU_ARK_PATH} · 角色 {ark_payload['character_count']}"
        )

    print("扫描本地图库…")
    payload = build_index(danbooru_pages=max(0, args.danbooru))
    OUT_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"已写入 {OUT_PATH}")
    print(
        f"角色 {len(payload['characters'])} · 特征 {len(payload['appearance'])} · "
        f"体型 {len(payload['body'])} · 版权 {len(payload['copyrights'])}"
    )


if __name__ == "__main__":
    main()