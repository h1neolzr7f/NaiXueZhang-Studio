from __future__ import annotations

import argparse
import copy
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nai_char as _nai_char  # noqa: E402
from nai_char import (  # noqa: E402
    build_generate_payload,
    extract_chars,
    resolve_all_female_indices,
    resolve_all_male_indices,
    transform,
)
from paths import data_dir  # noqa: E402


DEFAULT_OUT_DIR = data_dir() / "char_swap_audit"
_IMAGE_JSON_CACHE: dict[tuple[int, int], dict[str, Any]] = {}
_ORIGINAL_LOAD_IMAGE_JSON = _nai_char._load_image_json


def _install_fast_image_loader() -> None:
    def load_fast(work_id: int, page_index: int = 0) -> dict:
        key = (int(work_id), int(page_index or 0))
        if key in _IMAGE_JSON_CACHE:
            return copy.deepcopy(_IMAGE_JSON_CACHE[key])
        return _ORIGINAL_LOAD_IMAGE_JSON(work_id, page_index)

    _nai_char._load_image_json = load_fast


def _db_path() -> Path:
    return data_dir() / "aitag.db"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=30.0)
    conn.row_factory = sqlite3.Row
    return conn


def _sample_targets(conn: sqlite3.Connection, sample_rate: float, limit: int, offset: int) -> list[dict[str, Any]]:
    rate = max(0.000001, min(1.0, float(sample_rate or 0.1)))
    step = max(1, round(1.0 / rate))
    params: list[Any] = [int(offset or 0), step]
    sql = """
        SELECT work_id, page_index, ai_json
        FROM work_images
        WHERE ai_json IS NOT NULL
          AND ai_json <> ''
          AND ((rowid + ?) % ?) = 0
        ORDER BY rowid
    """
    if limit > 0:
        sql += " LIMIT ?"
        params.append(int(limit))
    return [
        {
            "work_id": int(row["work_id"]),
            "page_index": int(row["page_index"] or 0),
            "ai_json": row["ai_json"] or "",
        }
        for row in conn.execute(sql, params)
    ]


def _explicit_targets(args: argparse.Namespace) -> list[dict[str, Any]]:
    if not args.work_id:
        return []
    page_indices = args.page_index or [0]
    out: list[dict[str, int]] = []
    for wid in args.work_id:
        for pi in page_indices:
            out.append({"work_id": int(wid), "page_index": int(pi)})
    return out


def _hydrate_explicit_targets(targets: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if not targets:
        return targets
    hydrated: list[dict[str, Any]] = []
    with _connect() as conn:
        for target in targets:
            row = conn.execute(
                """
                SELECT ai_json
                FROM work_images
                WHERE work_id = ? AND page_index = ?
                """,
                (int(target["work_id"]), int(target.get("page_index") or 0)),
            ).fetchone()
            if row and row["ai_json"]:
                target = {**target, "ai_json": row["ai_json"]}
            hydrated.append(target)
    return hydrated


def _prime_image_cache(targets: list[dict[str, Any]]) -> None:
    for target in targets:
        raw = str(target.get("ai_json") or "")
        if not raw:
            continue
        try:
            _IMAGE_JSON_CACHE[
                (int(target["work_id"]), int(target.get("page_index") or 0))
            ] = json.loads(raw)
        except json.JSONDecodeError:
            continue


def _slot_summary(ch: dict[str, Any]) -> dict[str, Any]:
    return {
        "index": int(ch.get("index") or 0),
        "role": ch.get("role") or "",
        "summary": ch.get("summary") or "",
        "display_name": ch.get("display_name") or "",
        "char_caption": ch.get("char_caption") or "",
        "uc_caption": ch.get("uc_caption") or "",
        "identity_tags": ch.get("identity_tags") or [],
        "token_groups": ch.get("token_groups") or {},
        "bundle": ch.get("bundle") or {},
        "ark_library_tag": ch.get("ark_library_tag") or "",
        "oc_label": ch.get("oc_label") or "",
        "is_oc": bool(ch.get("is_oc")),
    }


def _patched_summary(comment: dict[str, Any]) -> dict[str, Any]:
    v4 = comment.get("v4_prompt") if isinstance(comment, dict) else {}
    cap = (v4.get("caption") or {}) if isinstance(v4, dict) else {}
    v4n = comment.get("v4_negative_prompt") if isinstance(comment, dict) else {}
    capn = (v4n.get("caption") or {}) if isinstance(v4n, dict) else {}
    return {
        "prompt": comment.get("prompt") or "",
        "v4_base_caption": cap.get("base_caption") or "",
        "v4_char_captions": cap.get("char_captions") or [],
        "v4_negative_base_caption": capn.get("base_caption") or "",
        "v4_negative_char_captions": capn.get("char_captions") or [],
        "use_coords": v4.get("use_coords") if isinstance(v4, dict) else None,
    }


def _payload_summary(comment: dict[str, Any]) -> dict[str, Any]:
    payload = build_generate_payload(comment, force_free=True)
    params = payload.get("parameters") or {}
    v4 = params.get("v4_prompt") or {}
    cap = (v4.get("caption") or {}) if isinstance(v4, dict) else {}
    v4n = params.get("v4_negative_prompt") or {}
    capn = (v4n.get("caption") or {}) if isinstance(v4n, dict) else {}
    return {
        "model": payload.get("model"),
        "width": payload.get("width"),
        "height": payload.get("height"),
        "steps": payload.get("steps"),
        "has_v4_prompt": bool(params.get("v4_prompt")),
        "v4_base_len": len(str(cap.get("base_caption") or "")),
        "v4_char_count": len(cap.get("char_captions") or []),
        "has_v4_negative_prompt": bool(params.get("v4_negative_prompt")),
        "v4_negative_base_len": len(str(capn.get("base_caption") or "")),
        "v4_negative_char_count": len(capn.get("char_captions") or []),
        "use_coords": params.get("use_coords"),
    }


def _preset_for_gender(args: argparse.Namespace, gender: str) -> str:
    if gender == "male":
        return args.male_preset_id or args.preset_id
    return args.female_preset_id or args.preset_id


def _gender_cases(args: argparse.Namespace, data: dict[str, Any]) -> list[dict[str, Any]]:
    chars = data.get("chars") or []
    cases: list[dict[str, Any]] = []
    modes = [args.mode] if args.mode in {"replace_female", "replace_male"} else ["replace_female", "replace_male"]
    for mode in modes:
        gender = "female" if mode == "replace_female" else "male"
        pool = resolve_all_female_indices(chars) if gender == "female" else resolve_all_male_indices(chars)
        if not pool:
            continue
        max_ord = len(pool) if args.all_gender_slots else min(1, len(pool))
        for ord_i in range(max_ord):
            cases.append(
                {
                    "mode": mode,
                    "gender": gender,
                    "gender_slot_index": ord_i,
                    "target_index": int(pool[ord_i]),
                    "preset_id": _preset_for_gender(args, gender),
                }
            )
    return cases


def _identity_keys(slot: dict[str, Any]) -> list[str]:
    keys: list[str] = []

    def add(value: Any) -> None:
        text = str(value or "").strip().lower().replace(" ", "_")
        if text and text not in keys:
            keys.append(text)

    add(slot.get("ark_library_tag"))
    add(slot.get("oc_label"))
    add(slot.get("summary"))
    add(slot.get("display_name"))
    for tag in slot.get("identity_tags") or []:
        add(tag)
    for tag in (slot.get("bundle") or {}).get("identity") or []:
        add(tag)
    return keys


def _probe_one(args: argparse.Namespace, target: dict[str, int], seq: int) -> list[dict[str, Any]]:
    work_id = int(target["work_id"])
    page_index = int(target.get("page_index") or 0)
    base_record: dict[str, Any] = {
        "seq": seq,
        "work_id": work_id,
        "page_index": page_index,
        "probed_at": datetime.now().isoformat(timespec="seconds"),
    }
    try:
        data = extract_chars(work_id, page_index)
    except Exception as exc:
        return [{**base_record, "status": "extract_error", "error": str(exc)}]

    original_chars = [_slot_summary(ch) for ch in data.get("chars") or []]
    cases = _gender_cases(args, data)
    if not original_chars:
        return [
            {
                **base_record,
                "status": "skipped",
                "reason": "no_character_slots",
                "original": {
                    "prompt_layout": data.get("prompt_layout") or "",
                    "base_caption": data.get("base_caption") or "",
                    "chars": [],
                },
            }
        ]
    if not cases:
        return [
            {
                **base_record,
                "status": "skipped",
                "reason": "no_requested_gender_slot",
                "original": {
                    "prompt_layout": data.get("prompt_layout") or "",
                    "base_caption": data.get("base_caption") or "",
                    "chars": original_chars,
                },
            }
        ]

    records: list[dict[str, Any]] = []
    for case in cases:
        record = {
            **base_record,
            "case_id": f"{work_id}:p{page_index}:{case['mode']}:g{case['gender_slot_index']}",
            "status": "ok",
            "case": case,
            "original": {
                "prompt_layout": data.get("prompt_layout") or "",
                "base_caption": data.get("base_caption") or "",
                "chars": original_chars,
            },
        }
        payload = {
            "target_work_id": work_id,
            "target_page_index": page_index,
            "mode": case["mode"],
            "preset_id": case["preset_id"],
            "gender": case["gender"],
            "gender_slot_index": case["gender_slot_index"],
            "target_char_index": "auto_female" if case["gender"] == "female" else "auto_male",
            "replace_creature": False,
            "preserve_action": bool(args.preserve_action),
            "preserve_center": True,
        }
        if args.identity_guard:
            slot = original_chars[case["target_index"]]
            payload["target_char_index"] = "all_female" if case["gender"] == "female" else "all_male"
            payload["match_identity_keys"] = _identity_keys(slot)
            payload["skip_missing_slots"] = True
        try:
            result = transform(payload)
            if result.get("skipped"):
                record["status"] = "skipped"
                record["reason"] = "transform_skipped"
                record["message"] = result.get("message") or ""
            patched_comment = result.get("patched_comment") or {}
            record["result"] = {
                "mode": result.get("mode") or "",
                "base_caption": result.get("base_caption") or "",
                "chars": [_slot_summary(ch) for ch in result.get("chars") or []],
                "patched": _patched_summary(patched_comment),
                "payload": _payload_summary(patched_comment),
            }
        except Exception as exc:
            record["status"] = "transform_error"
            record["error"] = str(exc)
        records.append(record)
    return records


def _write_records(path: Path, records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for record in records:
            fh.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe character replacement over local image metadata.")
    parser.add_argument("--sample-rate", type=float, default=0.1, help="Fraction of local image rows to probe.")
    parser.add_argument("--limit", type=int, default=0, help="Maximum sampled image rows before per-slot expansion.")
    parser.add_argument("--offset", type=int, default=0, help="Modulo offset for deterministic sampling.")
    parser.add_argument("--work-id", type=int, action="append", help="Probe a specific work id. May be repeated.")
    parser.add_argument("--page-index", type=int, action="append", help="Page index for explicit work ids.")
    parser.add_argument("--mode", choices=["both", "replace_female", "replace_male"], default="replace_female")
    parser.add_argument("--preset-id", default="oc_12gg_f", help="Fallback preset id.")
    parser.add_argument("--female-preset-id", default="oc_12gg_f", help="Female replacement preset id.")
    parser.add_argument("--male-preset-id", default="oc_ding_m", help="Male replacement preset id.")
    parser.add_argument("--all-gender-slots", action="store_true", help="Probe every slot of the requested gender.")
    parser.add_argument("--identity-guard", action="store_true", help="Probe all-gender replacement with source identity guards.")
    parser.add_argument("--preserve-action", action="store_true", help="Preserve target action tags during replacement.")
    parser.add_argument("--out", type=Path, default=None, help="JSONL output path.")
    args = parser.parse_args()

    out = args.out
    if out is None:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = DEFAULT_OUT_DIR / f"char_swap_probe_{stamp}.jsonl"
    if out.exists():
        out.unlink()

    _install_fast_image_loader()
    targets = _hydrate_explicit_targets(_explicit_targets(args))
    if not targets:
        with _connect() as conn:
            targets = _sample_targets(conn, args.sample_rate, args.limit, args.offset)
    _prime_image_cache(targets)

    total_records = 0
    status_counts: dict[str, int] = {}
    for seq, target in enumerate(targets, 1):
        records = _probe_one(args, target, seq)
        _write_records(out, records)
        total_records += len(records)
        for record in records:
            status = str(record.get("status") or "unknown")
            status_counts[status] = status_counts.get(status, 0) + 1
        if seq % 500 == 0:
            print(f"probed {seq}/{len(targets)} image rows -> {total_records} records", flush=True)

    summary = {
        "ok": True,
        "targets": len(targets),
        "records": total_records,
        "status_counts": status_counts,
        "out": str(out),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
