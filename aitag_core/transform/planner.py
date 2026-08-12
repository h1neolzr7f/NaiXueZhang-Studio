from __future__ import annotations

import copy
from dataclasses import asdict, dataclass, field
from typing import Any

from aitag_core.prompt import tokenize_prompt
from aitag_core.recognition import SlotAnalysis, analyze_slot_caption


@dataclass(frozen=True)
class ReplacementPlan:
    slot_index: int
    role: str
    remove_tokens: list[str] = field(default_factory=list)
    preserve_tokens: list[str] = field(default_factory=list)
    inject_tokens: list[str] = field(default_factory=list)
    output_caption: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def plan_replacement(
    target_slot: SlotAnalysis | dict,
    source_preset: dict[str, Any],
    *,
    slot_index: int = 0,
    preserve_action: bool = True,
    force_gender: str | None = None,
) -> ReplacementPlan:
    slot = (
        target_slot
        if isinstance(target_slot, SlotAnalysis)
        else analyze_slot_caption(str(target_slot.get("char_caption") or ""))
    )
    role = force_gender or str(source_preset.get("gender") or slot.role or "unknown")
    source_tokens = _source_tokens(source_preset, role)
    preserve = list(slot.token_groups.get("action") or []) if preserve_action else []
    remove = [
        t.raw
        for t in tokenize_prompt(slot.caption)
        if t.raw not in preserve and t.normalized not in {p.lower() for p in preserve}
    ]
    output = _join_unique(source_tokens + preserve)
    return ReplacementPlan(
        slot_index=int(slot_index),
        role=role,
        remove_tokens=remove,
        preserve_tokens=preserve,
        inject_tokens=source_tokens,
        output_caption=output,
    )


def apply_replacement_plan(comment: dict[str, Any], plan: ReplacementPlan) -> dict[str, Any]:
    patched = copy.deepcopy(comment)
    v4 = patched.setdefault("v4_prompt", {})
    cap = v4.setdefault("caption", {})
    chars = cap.setdefault("char_captions", [])
    if plan.slot_index >= len(chars):
        raise IndexError("target character slot does not exist")
    item = chars[plan.slot_index]
    if not isinstance(item, dict):
        raise TypeError("target character slot is not an object")
    item["char_caption"] = plan.output_caption
    return patched


def _source_tokens(source: dict[str, Any], role: str) -> list[str]:
    gender_tags: list[str] = []
    if role == "male":
        gender_tags = ["1boy", "male_focus"]
    elif role == "female":
        gender_tags = ["1girl", "female_focus"]
    direct = str(source.get("char_caption") or "").strip()
    if direct:
        identity_tags = [
            token
            for token in list(source.get("identity") or [])
            if str(token or "").strip() and str(token or "").strip() not in gender_tags
        ]
        extra_tags = list(source.get("body") or []) + list(source.get("appearance") or [])
        return identity_tags + gender_tags + extra_tags + [direct]
    return (
        gender_tags
        + list(source.get("identity") or [])
        + list(source.get("body") or [])
        + list(source.get("appearance") or [])
    )


def _join_unique(tokens: list[str]) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        text = str(token or "").strip()
        key = text.lower()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return ", ".join(out)
