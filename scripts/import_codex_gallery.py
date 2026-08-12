"""Import ANR 所长法典 (codex_director_refs) into the codex gallery store.

Only NAI prompt assets. No Comfy workflows.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

from gallery_catalog import GALLERY_CODEX, ensure_gallery_dirs, get_db  # noqa: E402
from gallery_import_common import (  # noqa: E402
    sanitize_filename,
    save_group_index,
    stable_work_id,
    upsert_local_work,
    write_preview_card,
)


def find_default_codex_root() -> Path | None:
    for child in Path("E:/").iterdir():
        if not child.is_dir():
            continue
        candidate = child / "Auto-NovelAI-Refactor" / "codex_director_refs"
        if candidate.is_dir() and (candidate / "index.json").exists():
            return candidate
        # also one level deeper common layout
        for nested in child.iterdir() if child.is_dir() else []:
            try:
                if not nested.is_dir():
                    continue
            except OSError:
                continue
            cand = nested / "Auto-NovelAI-Refactor" / "codex_director_refs"
            if cand.is_dir() and (cand / "index.json").exists():
                return cand
    # direct known garbled path scan
    for path in Path("E:/").glob("*/Auto-NovelAI-Refactor/codex_director_refs"):
        if (path / "index.json").exists():
            return path
    return None


def iter_jsonl_entries(codex_root: Path):
    for rating_dir in ("sfw", "nsfw"):
        base = codex_root / rating_dir
        if not base.is_dir():
            continue
        for jsonl in sorted(base.glob("*.jsonl")):
            category_file = jsonl.stem
            with jsonl.open("r", encoding="utf-8", errors="ignore") as fh:
                for line_no, line in enumerate(fh, 1):
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(obj, dict):
                        continue
                    yield rating_dir, category_file, line_no, obj


def entry_to_prompt(obj: dict) -> str:
    pos = obj.get("positive_tags") or []
    if isinstance(pos, list) and pos:
        return ", ".join(str(x) for x in pos if x)
    source = str(obj.get("source_text") or obj.get("search_text") or "").strip()
    return source


def import_codex(codex_root: Path, *, limit: int = 0, force_preview: bool = False) -> dict:
    ensure_gallery_dirs(GALLERY_CODEX)
    spec_images = ensure_gallery_dirs(GALLERY_CODEX).images_dir
    imported = 0
    skipped = 0
    counts: Counter[str] = Counter()

    for rating, category_file, line_no, obj in iter_jsonl_entries(codex_root):
        name = str(obj.get("name") or f"{category_file}_{line_no}").strip()
        category = str(obj.get("category") or category_file).strip() or category_file
        rating_val = str(obj.get("rating") or rating).strip().lower()
        prompt = entry_to_prompt(obj)
        if not prompt and not name:
            skipped += 1
            continue

        # NAI-only product: codex entries are NAI director prompts by design.
        entry_id = str(obj.get("id") or f"{rating}:{category_file}:{line_no}:{name}")
        work_id = stable_work_id("codex", entry_id)
        rel_dir = f"{rating_val}/{sanitize_filename(category)}"
        preview_name = f"{sanitize_filename(name)}_{work_id % 100000}_p0.webp"
        preview_rel = f"{rel_dir}/{preview_name}".replace("\\", "/")
        preview_abs = spec_images / preview_rel

        if force_preview or not preview_abs.exists() or preview_abs.stat().st_size < 3000:
            subtitle = ", ".join(
                str(x) for x in (obj.get("positive_tags") or [])[:10] if x
            ) or prompt[:160]
            accent = (232, 121, 249) if rating_val == "nsfw" else (56, 189, 248)
            # category color accents
            cat_colors = {
                "动作": (52, 211, 153),
                "服饰": (251, 146, 60),
                "场景": (96, 165, 250),
                "构图": (167, 139, 250),
                "表情": (251, 113, 133),
                "镜头": (45, 212, 191),
                "人物互动": (250, 204, 21),
                "污渍": (248, 113, 113),
            }
            if category in cat_colors and rating_val != "nsfw":
                accent = cat_colors[category]
            write_preview_card(
                preview_abs,
                title=name[:48],
                subtitle=subtitle,
                footer=f"所长法典 · {rating_val.upper()} · {category} · NAI",
                accent=accent,
                badge=f"{rating_val.upper()} · {category}",
                category=category,
            )

        bucket_key = f"{rating_val}::{category}"
        tags = ",".join(
            [
                "codex",
                "NAI",
                f"rating:{rating_val}",
                f"cat:{category}",
                category,
                rating_val,
                bucket_key,
                *[str(x) for x in (obj.get("positive_tags") or [])[:20] if x],
            ]
        )
        caption_parts = []
        for sent in obj.get("director_sentences") or []:
            if sent:
                caption_parts.append(str(sent))
        caption = " ".join(caption_parts) or prompt[:500]

        if force_preview or not preview_abs.exists() or preview_abs.stat().st_size < 3000:
            # already written above when force/missing; ensure rich card
            pass

        upsert_local_work(
            GALLERY_CODEX,
            work_id=work_id,
            title=name,
            caption=caption,
            tags=tags,
            prompt_text=prompt,
            preview_rel=preview_rel,
            category=category,
            rating=rating_val,
            source=f"anr-codex:{rating_val}/{category_file}",
            extra={
                "codex_id": entry_id,
                "category_file": category_file,
                "bucket": bucket_key,
                "keywords_zh": obj.get("keywords_zh") or [],
            },
        )
        counts[bucket_key] += 1
        imported += 1
        if limit and imported >= limit:
            break

    # hierarchical groups matching ANR index: rating::category
    groups: list[dict] = []
    # also expose pure rating totals
    rating_totals: Counter = Counter()
    cat_totals: Counter = Counter()
    for key, c in counts.items():
        if "::" in key:
            rating, cat = key.split("::", 1)
            rating_totals[rating] += c
            cat_totals[cat] += c
            groups.append(
                {
                    "key": key,
                    "label": f"{rating.upper()} · {cat}",
                    "count": c,
                    "rating": rating,
                    "category": cat,
                    "kind": "bucket",
                }
            )
    for rating, c in sorted(rating_totals.items()):
        groups.insert(
            0,
            {
                "key": rating,
                "label": rating.upper(),
                "count": c,
                "rating": rating,
                "category": "",
                "kind": "rating",
            },
        )
    for cat, c in sorted(cat_totals.items(), key=lambda kv: (-kv[1], kv[0])):
        groups.append(
            {
                "key": cat,
                "label": cat,
                "count": c,
                "rating": "",
                "category": cat,
                "kind": "category",
            }
        )
    groups = [g for g in groups if g.get("count")]
    groups.sort(
        key=lambda g: (
            0 if g.get("kind") == "rating" else 1 if g.get("kind") == "category" else 2,
            -(g.get("count") or 0),
            str(g.get("label") or ""),
        )
    )
    save_group_index(GALLERY_CODEX, groups)
    db = get_db(GALLERY_CODEX)
    total = db.count_works()
    return {
        "gallery": GALLERY_CODEX,
        "source": str(codex_root),
        "imported": imported,
        "skipped": skipped,
        "total_works": total,
        "categories": len(groups),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Import ANR codex into codex gallery")
    parser.add_argument("--source", default="", help="Path to codex_director_refs")
    parser.add_argument("--limit", type=int, default=0, help="Import at most N entries")
    parser.add_argument("--force-preview", action="store_true")
    args = parser.parse_args()

    source = Path(args.source) if args.source else find_default_codex_root()
    if not source or not source.is_dir():
        print("ERROR: codex_director_refs not found. Pass --source PATH")
        return 2
    result = import_codex(source, limit=args.limit, force_preview=args.force_preview)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
