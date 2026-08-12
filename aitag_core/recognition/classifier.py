from __future__ import annotations

import re

from aitag_core.prompt import PromptToken

from .contract import TokenAnalysis


_BODY_HINT_RE = re.compile(
    r"\b("
    r"breast|penis|pussy|vagina|anus|ass|butt|nipple|stomach|belly|back|"
    r"thigh|hip|leg|arm|hand|foot|feet"
    r")s?\b",
    re.IGNORECASE,
)
_ACTION_HINT_RE = re.compile(
    r"\b("
    r"sex|standing|sitting|lying|looking|holding|grabbing|spreading|open mouth|"
    r"from side|from above|from below|disembodied hand|fingering|strangling|"
    r"orgasm|ahegao|trembling|bent over|choke|sucking|pussy juice"
    r")\b",
    re.IGNORECASE,
)
_MALE_GENDER_TOKENS = {"1boy", "2boys", "3boys", "boy", "boys", "male", "male focus", "male_focus"}
_FEMALE_GENDER_TOKENS = {
    "1girl",
    "2girls",
    "3girls",
    "girl",
    "girls",
    "female",
    "female focus",
    "female_focus",
}
_APPEARANCE_TOKENS = {
    "no panties",
    "no_panties",
}
_ACTION_OVERRIDE_TOKENS = {
    "disembodied hand",
    "disembodied_hand",
}
_INTERACTION_VERBS = (
    "grabbing",
    "spreading",
    "sucking",
    "licking",
    "holding",
    "touching",
    "pulling",
    "fingering",
)


def _display(token: PromptToken) -> str:
    from char_tag_db import identity_tag_display, weighted_tag_inner

    return identity_tag_display(token.raw) if weighted_tag_inner(token.raw) else token.raw


def _is_body_like(low: str) -> bool:
    return bool(_BODY_HINT_RE.search(low.replace("_", " ")))


def _is_action_like(low: str) -> bool:
    text = low.replace("_", " ")
    if any(v in text for v in _INTERACTION_VERBS):
        return True
    return bool(_ACTION_HINT_RE.search(text))


def _is_strong_identity(token: PromptToken) -> bool:
    from char_tag_db import (
        ARKNIGHTS_RE,
        CHAR_SUFFIX_RE,
        is_character_tag,
        is_identity_noise_tag,
        weighted_tag_inner,
    )

    text = token.text.strip()
    low = text.lower()
    underscored = low.replace(" ", "_")
    if not low or is_identity_noise_tag(low) or _is_body_like(low) or _is_action_like(low):
        return False
    if is_character_tag(low) or is_character_tag(underscored):
        return True
    if CHAR_SUFFIX_RE.match(low) or CHAR_SUFFIX_RE.match(underscored):
        return True
    if ARKNIGHTS_RE.search(low) and ("(" in low or "_(" in low):
        return True
    inner = weighted_tag_inner(token.raw)
    if inner and not _is_action_like(inner.lower()) and not _is_body_like(inner.lower()):
        return bool(CHAR_SUFFIX_RE.match(inner.strip()) or is_character_tag(inner))
    return False


def classify_token(token: PromptToken) -> TokenAnalysis:
    from char_tag_db import (
        classify_single_tag,
        is_action_phrase,
        is_appearance_tag,
        is_appearance_weight_block,
        is_body_tag,
        is_creature_tag,
        is_framing_tag,
        is_gender_tag,
        weighted_tag_inner,
    )

    display = _display(token)
    low = token.text.strip().lower()
    raw_low = token.raw.strip().lower()
    class_target = weighted_tag_inner(token.raw) or token.raw

    if not low:
        return TokenAnalysis(token, "meta", 1.0, "empty", display)
    if low in _MALE_GENDER_TOKENS or raw_low in _MALE_GENDER_TOKENS:
        return TokenAnalysis(token, "gender", 0.99, "male-token", display)
    if low in _FEMALE_GENDER_TOKENS or raw_low in _FEMALE_GENDER_TOKENS:
        return TokenAnalysis(token, "gender", 0.99, "female-token", display)
    if is_gender_tag(low) or is_gender_tag(raw_low):
        return TokenAnalysis(token, "gender", 0.99, "gender-tag", display)
    if low in _ACTION_OVERRIDE_TOKENS or raw_low in _ACTION_OVERRIDE_TOKENS:
        return TokenAnalysis(token, "action", 0.98, "action-override", display, True)
    if low in _APPEARANCE_TOKENS or raw_low in _APPEARANCE_TOKENS:
        return TokenAnalysis(token, "appearance", 0.96, "appearance-token", display)
    if _is_action_like(low):
        return TokenAnalysis(token, "action", 0.96, "action-hint", display, True)
    if _is_strong_identity(token):
        return TokenAnalysis(token, "identity", 0.97, "strong-identity", display)
    if _is_body_like(low) or is_body_tag(low):
        return TokenAnalysis(token, "body", 0.96, "body-hint", display)
    if is_appearance_weight_block(token.raw) or is_appearance_tag(low):
        return TokenAnalysis(token, "appearance", 0.92, "appearance-tag", display)
    if is_creature_tag(low):
        return TokenAnalysis(token, "creature", 0.95, "creature-tag", display)
    if is_action_phrase(token.raw) or is_framing_tag(low):
        return TokenAnalysis(token, "action", 0.96, "action-hint", display, True)
    legacy = classify_single_tag(class_target)
    if legacy in {"body", "appearance", "creature", "gender"}:
        return TokenAnalysis(token, legacy, 0.82, f"legacy-{legacy}", display)
    if legacy == "identity" and _is_strong_identity(token):
        return TokenAnalysis(token, "identity", 0.86, "legacy-confirmed-identity", display)
    return TokenAnalysis(token, "action", 0.65, "default-action", display, True)
