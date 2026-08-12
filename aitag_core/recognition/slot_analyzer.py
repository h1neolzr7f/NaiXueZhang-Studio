from __future__ import annotations

from aitag_core.prompt import tokenize_prompt

from .classifier import classify_token
from .contract import OcMatch, SlotAnalysis
from .oc_matcher import match_oc_preset


_GROUPS = ("identity", "gender", "body", "appearance", "creature", "action", "meta")


def analyze_slot_caption(
    caption: str,
    *,
    gender_hint: str = "",
    oc_presets: list[dict] | None = None,
) -> SlotAnalysis:
    from char_tag_db import pick_character_summary

    token_analyses = tuple(classify_token(t) for t in tokenize_prompt(caption))
    groups = {name: [] for name in _GROUPS}
    for item in token_analyses:
        groups.setdefault(item.category, []).append(item.display.strip())

    role = _role_from_groups(groups, gender_hint)
    identity_name = pick_character_summary(caption, groups.get("identity") or []) or None
    oc = _match_oc(caption, oc_presets or [])
    display_name = identity_name or _unknown_display(role)
    replaceable = bool(role in {"male", "female"} or identity_name)
    return SlotAnalysis(
        caption=str(caption or ""),
        role=role,
        identity_name=identity_name,
        display_name=display_name,
        replaceable=replaceable,
        tokens=token_analyses,
        token_groups={k: v for k, v in groups.items() if v},
        oc=oc,
    )


def _match_oc(caption: str, presets: list[dict]) -> OcMatch:
    for preset in presets:
        if str((preset or {}).get("kind") or "").lower() != "oc":
            continue
        match = match_oc_preset(caption, preset)
        if match.matched:
            return match
    return OcMatch()


def _role_from_groups(groups: dict[str, list[str]], gender_hint: str = "") -> str:
    hint = str(gender_hint or "").strip().lower()
    if hint in {"male", "female"}:
        return hint
    normalized = {
        str(t or "").strip().lower().replace("_", " ")
        for t in (groups.get("gender") or [])
    }
    joined = " ".join(normalized)
    if normalized & {"1boy", "boy", "boys", "male", "male focus"}:
        return "male"
    if normalized & {"1girl", "girl", "girls", "female", "female focus"}:
        return "female"
    if any(t in joined for t in ("1boy", " boy", "boys", "male")):
        return "male"
    if any(t in joined for t in ("1girl", " girl", "girls", "female")):
        return "female"
    return "unknown"


def _unknown_display(role: str) -> str:
    if role == "male":
        return "未知男角色"
    if role == "female":
        return "未知女角色"
    return "未知角色"
