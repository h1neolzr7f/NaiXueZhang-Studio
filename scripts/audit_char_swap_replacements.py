from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from aitag_core.prompt import tokenize_prompt  # noqa: E402


_IDENTITY_SUFFIX_RE = re.compile(r"_(?:\(arknights\)|\(oc\))$", re.I)


def _norm(text: Any) -> str:
    return str(text or "").strip().lower().replace("_", " ")


def _compact(text: Any) -> str:
    return _norm(text).replace(" ", "_")


def _tag_set(text: str) -> set[str]:
    out: set[str] = set()
    for token in tokenize_prompt(text):
        out.add(_norm(token.text))
        out.add(_compact(token.text))
    return out


def _caption_blob(result: dict[str, Any]) -> str:
    patched = result.get("patched") or {}
    parts: list[str] = [
        str(result.get("base_caption") or ""),
        str(patched.get("prompt") or ""),
        str(patched.get("v4_base_caption") or ""),
        str(patched.get("v4_negative_base_caption") or ""),
    ]
    for item in patched.get("v4_char_captions") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("char_caption") or ""))
    for item in patched.get("v4_negative_char_captions") or []:
        if isinstance(item, dict):
            parts.append(str(item.get("char_caption") or ""))
    for ch in result.get("chars") or []:
        parts.append(str(ch.get("char_caption") or ""))
        parts.append(str(ch.get("uc_caption") or ""))
    return ", ".join(parts)


def _base_blob(result: dict[str, Any]) -> str:
    patched = result.get("patched") or {}
    return ", ".join(
        [
            str(result.get("base_caption") or ""),
            str(patched.get("prompt") or ""),
            str(patched.get("v4_base_caption") or ""),
            str(patched.get("v4_negative_base_caption") or ""),
        ]
    )


def _identity_candidates(slot: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    strong_values: list[str] = []
    for key in ("ark_library_tag", "oc_label"):
        value = str(slot.get(key) or "").strip()
        if value:
            strong_values.append(value)
    for tag in (slot.get("bundle") or {}).get("identity") or []:
        text = str(tag or "").strip()
        compact = _compact(text)
        if _IDENTITY_SUFFIX_RE.search(compact) or compact.endswith("(arknights)") or compact.endswith("(oc)"):
            strong_values.append(text)
    caption = str(slot.get("char_caption") or "")
    for token in tokenize_prompt(caption):
        raw = _compact(token.text)
        if _IDENTITY_SUFFIX_RE.search(raw) or raw.endswith("(arknights)") or raw.endswith("(oc)"):
            strong_values.append(token.text)
    for value in strong_values:
        out.add(_norm(value))
        out.add(_compact(value))
    return {x for x in out if x and x not in {"1girl", "1boy", "female focus", "male focus"}}


def _target_tokens(replaced_slot: dict[str, Any]) -> set[str]:
    caption = str(replaced_slot.get("char_caption") or "")
    tags = _tag_set(caption)
    for tag in replaced_slot.get("identity_tags") or []:
        tags.add(_norm(tag))
        tags.add(_compact(tag))
    for tag in (replaced_slot.get("bundle") or {}).get("identity") or []:
        tags.add(_norm(tag))
        tags.add(_compact(tag))
    return tags


def _find_slots(record: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int | None]:
    original = ((record.get("original") or {}).get("chars") or [])
    result = ((record.get("result") or {}).get("chars") or [])
    case = record.get("case") or {}
    target_index = case.get("target_index")
    try:
        target_index = int(target_index)
    except (TypeError, ValueError):
        target_index = None
    return original, result, target_index


def _add_issue(issues: list[dict[str, Any]], code: str, message: str, *, detail: dict[str, Any] | None = None) -> None:
    issues.append({"code": code, "message": message, "detail": detail or {}})


def audit_record(record: dict[str, Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    status = str(record.get("status") or "")
    if status in {"skipped"}:
        return issues
    if status != "ok":
        _add_issue(issues, "record_not_ok", str(record.get("error") or record.get("reason") or status))
        return issues

    original, result, target_index = _find_slots(record)
    if target_index is None or target_index < 0 or target_index >= len(original):
        _add_issue(issues, "bad_target_index", "Target index is missing or outside original slots.")
        return issues
    if len(result) != len(original):
        _add_issue(
            issues,
            "slot_count_changed",
            "Simple replacement changed the number of character slots.",
            detail={"original": len(original), "result": len(result)},
        )
    if target_index >= len(result):
        _add_issue(issues, "target_missing_after_replace", "Target slot disappeared after replacement.")
        return issues

    old_slot = original[target_index]
    new_slot = result[target_index]
    old_ids = _identity_candidates(old_slot)
    new_tokens = _target_tokens(new_slot)
    blob_tags = _tag_set(_caption_blob(record.get("result") or {}))
    base_tags = _tag_set(_base_blob(record.get("result") or {}))
    protected_other_ids: set[str] = set()
    for i, slot in enumerate(result):
        if i != target_index:
            protected_other_ids.update(_identity_candidates(slot))

    stale = sorted(
        t
        for t in old_ids
        if t in blob_tags
        and t not in new_tokens
        and (t in base_tags or t not in protected_other_ids)
    )
    if stale:
        _add_issue(
            issues,
            "stale_replaced_identity",
            "Old target identity is still present after replacement.",
            detail={"stale": stale[:12], "target_index": target_index},
        )

    if not new_tokens.intersection({"12gg (oc)", "12gg_(oc)", "ding (oc)", "ding_(oc)", "original character", "original_character"}):
        caption = str(new_slot.get("char_caption") or "")
        if "(oc)" not in caption.lower() and "original_character" not in caption.lower():
            _add_issue(
                issues,
                "target_identity_missing",
                "Replacement target slot does not contain the configured OC identity.",
                detail={"target_caption": caption[:240]},
            )

    for i, (before, after) in enumerate(zip(original, result)):
        if i == target_index:
            continue
        before_ids = _identity_candidates(before)
        after_ids = _identity_candidates(after)
        if before_ids and not before_ids.intersection(after_ids):
            _add_issue(
                issues,
                "untargeted_slot_identity_changed",
                "A non-target slot lost its original identity.",
                detail={
                    "slot": i,
                    "before": sorted(before_ids)[:12],
                    "after": sorted(after_ids)[:12],
                },
            )

    patched = (record.get("result") or {}).get("patched") or {}
    char_caps = patched.get("v4_char_captions") or []
    neg_char_caps = patched.get("v4_negative_char_captions") or []
    payload = (record.get("result") or {}).get("payload") or {}
    prompt_text = str(patched.get("prompt") or patched.get("v4_base_caption") or "")
    is_char_marker_layout = (
        ((record.get("original") or {}).get("prompt_layout") == "char_markers")
        and re.search(r"char[1-6]\s*[:：]", prompt_text, re.I)
    )
    if not payload.get("has_v4_prompt"):
        _add_issue(issues, "missing_v4_payload", "Generate payload does not include v4_prompt.")
    if not payload.get("has_v4_negative_prompt"):
        _add_issue(issues, "missing_v4_negative_payload", "Generate payload does not include v4_negative_prompt.")
    if len(char_caps) != len(result) and not is_char_marker_layout:
        _add_issue(
            issues,
            "patched_char_count_mismatch",
            "Patched v4 char_captions count differs from parsed result slots.",
            detail={"patched": len(char_caps), "result": len(result)},
        )
    if is_char_marker_layout and neg_char_caps:
        _add_issue(
            issues,
            "char_marker_has_negative_slots",
            "Char-marker prompt should not keep separate negative char_captions.",
            detail={"negative": len(neg_char_caps)},
        )
    elif neg_char_caps and len(neg_char_caps) != len(char_caps):
        _add_issue(
            issues,
            "negative_char_count_mismatch",
            "Negative char_captions count differs from positive char_captions.",
            detail={"negative": len(neg_char_caps), "positive": len(char_caps)},
        )
    return issues


def _iter_jsonl(path: Path):
    with path.open("r", encoding="utf-8") as fh:
        for line_no, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                yield line_no, json.loads(line)
            except json.JSONDecodeError as exc:
                yield line_no, {"status": "json_error", "error": str(exc)}


def main() -> int:
    parser = argparse.ArgumentParser(description="Audit character replacement probe JSONL.")
    parser.add_argument("probe_jsonl", type=Path)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--max-examples", type=int, default=30)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args()

    issue_counts: Counter[str] = Counter()
    status_counts: Counter[str] = Counter()
    examples: list[dict[str, Any]] = []
    total = 0
    failed_records = 0

    for line_no, record in _iter_jsonl(args.probe_jsonl):
        total += 1
        status_counts[str(record.get("status") or "unknown")] += 1
        issues = audit_record(record)
        if issues:
            failed_records += 1
            for issue in issues:
                issue_counts[issue["code"]] += 1
            if len(examples) < args.max_examples:
                examples.append(
                    {
                        "line": line_no,
                        "work_id": record.get("work_id"),
                        "page_index": record.get("page_index"),
                        "case_id": record.get("case_id"),
                        "issues": issues,
                    }
                )
            if args.fail_fast:
                break

    report = {
        "ok": failed_records == 0,
        "records": total,
        "failed_records": failed_records,
        "status_counts": dict(status_counts),
        "issue_counts": dict(issue_counts),
        "examples": examples,
    }
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    return 0 if failed_records == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
