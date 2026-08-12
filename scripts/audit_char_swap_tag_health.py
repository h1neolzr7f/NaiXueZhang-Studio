from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aitag_core.prompt import tokenize_prompt  # noqa: E402
from char_tag_db import is_action_phrase, is_generic_character_tag  # noqa: E402
from nai_char import extract_chars  # noqa: E402
from paths import data_dir  # noqa: E402


IDENTITY_NOISE = {
    "1girl",
    "1boy",
    "female_focus",
    "male_focus",
    "girl",
    "boy",
    "girls",
    "boys",
    "standing",
    "sitting",
    "lying",
    "looking_at_viewer",
    "open_mouth",
    "solo",
}


def _db_path() -> Path:
    return data_dir() / "aitag.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _sample_targets(sample_rate: float, limit: int, offset: int) -> list[tuple[int, int]]:
    rate = max(0.000001, min(1.0, float(sample_rate or 0.1)))
    step = max(1, round(1.0 / rate))
    params: list[Any] = [int(offset or 0), step]
    sql = """
        SELECT work_id, page_index
        FROM work_images
        WHERE ai_json IS NOT NULL
          AND ai_json <> ''
          AND ((rowid + ?) % ?) = 0
        ORDER BY rowid
    """
    if limit > 0:
        sql += " LIMIT ?"
        params.append(int(limit))
    with _connect() as conn:
        return [(int(r["work_id"]), int(r["page_index"] or 0)) for r in conn.execute(sql, params)]


def _norm(value: Any) -> str:
    return str(value or "").strip().lower().replace(" ", "_")


def _tokens(text: str) -> set[str]:
    return {_norm(t.text) for t in tokenize_prompt(text or "")}


def _slot_issues(slot: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    caption = str(slot.get("char_caption") or "")
    identity = [_norm(x) for x in slot.get("identity_tags") or [] if _norm(x)]
    role = str(slot.get("role") or "")
    tagset = _tokens(caption)
    if not caption.strip():
        issues.append({"code": "empty_slot_caption", "message": "Character slot has no caption."})
    if role in {"male", "female"} and not slot.get("replaceable"):
        issues.append({"code": "gender_slot_not_replaceable", "message": "Gendered slot is not replaceable."})
    noisy = [
        tag
        for tag in identity
        if tag in IDENTITY_NOISE
        or is_action_phrase(tag.replace("_", " "))
        or is_generic_character_tag(tag)
    ]
    if noisy:
        issues.append({"code": "identity_noise", "message": "Identity tags contain generic/action noise.", "tags": noisy[:10]})
    strong = [
        _norm(slot.get("ark_library_tag")),
        _norm(slot.get("oc_label")),
        *identity,
        *[_norm(x) for x in (slot.get("bundle") or {}).get("identity") or []],
    ]
    strong = [x for x in strong if x and x not in IDENTITY_NOISE]
    if role in {"male", "female"} and not strong and len(tagset) > 4:
        issues.append({"code": "missing_identity_for_rich_slot", "message": "Rich gendered slot has no stable identity key."})
    return issues


def audit_target(work_id: int, page_index: int) -> dict[str, Any]:
    base = {"work_id": work_id, "page_index": page_index, "issues": []}
    try:
        data = extract_chars(work_id, page_index)
    except Exception as exc:
        return {**base, "status": "extract_error", "error": str(exc)}
    chars = data.get("chars") or []
    for i, slot in enumerate(chars):
        for issue in _slot_issues(slot):
            base["issues"].append({"slot": i, **issue})
    base["status"] = "ok"
    base["prompt_layout"] = data.get("prompt_layout") or ""
    base["slot_count"] = len(chars)
    return base


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit recognition/tag health for char-swap inputs.")
    parser.add_argument("--sample-rate", type=float, default=0.1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--max-examples", type=int, default=50)
    parser.add_argument("--report", type=Path, default=data_dir() / "char_swap_audit" / "tag_health_report.json")
    args = parser.parse_args()

    targets = _sample_targets(args.sample_rate, args.limit, args.offset)
    status_counts: Counter[str] = Counter()
    issue_counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    for n, (work_id, page_index) in enumerate(targets, 1):
        rec = audit_target(work_id, page_index)
        status_counts[str(rec.get("status") or "unknown")] += 1
        for issue in rec.get("issues") or []:
            issue_counts[str(issue.get("code") or "unknown")] += 1
        if rec.get("issues") and len(examples) < args.max_examples:
            examples.append(rec)
        if n % 500 == 0:
            print(f"audited {n}/{len(targets)}", flush=True)

    report = {
        "ok": not issue_counts,
        "targets": len(targets),
        "status_counts": dict(status_counts),
        "issue_counts": dict(issue_counts),
        "examples": examples,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(text + "\n", encoding="utf-8")
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
