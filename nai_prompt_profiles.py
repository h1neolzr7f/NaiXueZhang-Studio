"""Model-aware prompt profile transforms for NovelAI generation.

Older releases called these profiles ``Anima V1/V2``.  The stored ids remain
accepted as migration aliases, but all new output is explicitly compiled for
NovelAI.  This avoids leaking Stable Diffusion weight syntax or generic
AnimaDex metadata into NAI requests.
"""

from __future__ import annotations

import copy
import json
import re
from functools import lru_cache
from typing import Any

from char_tag_db import classify_caption_buckets, split_prompt_tags
from paths import data_dir

PROFILE_NATIVE = "native"
PROFILE_NAI_FAITHFUL = "nai_faithful"
PROFILE_NAI_EXPRESSIVE = "nai_expressive"
PROFILE_DEEPSEEK_NAI_FAITHFUL = "deepseek_nai_faithful"
PROFILE_DEEPSEEK_NAI_EXPRESSIVE = "deepseek_nai_expressive"

# Compatibility exports for saved recipes and third-party callers.  These
# names intentionally resolve to the new NAI-native ids.
PROFILE_ANIMA_FAITHFUL = PROFILE_NAI_FAITHFUL
PROFILE_ANIMA_EPIC = PROFILE_NAI_EXPRESSIVE
PROFILE_DEEPSEEK_ANIMA_FAITHFUL = PROFILE_DEEPSEEK_NAI_FAITHFUL
PROFILE_DEEPSEEK_ANIMA_EPIC = PROFILE_DEEPSEEK_NAI_EXPRESSIVE

PROMPT_PROFILE_CHOICES: list[dict[str, str]] = [
    {
        "id": PROFILE_NATIVE,
        "label": "Original NAI",
        "description": "Use the prompt exactly as stored in the draft.",
    },
    {
        "id": PROFILE_NAI_FAITHFUL,
        "label": "NAI 还原优先",
        "description": "保留原构图和角色，只清理冲突并编译成当前 NAI 模型语法。",
    },
    {
        "id": PROFILE_NAI_EXPRESSIVE,
        "label": "NAI 表现力增强",
        "description": "使用 NAI 原生权重、质量词和简短自然语言增强画面表现。",
    },
]

_PROFILE_ALIASES = {
    "": PROFILE_NATIVE,
    "none": PROFILE_NATIVE,
    "off": PROFILE_NATIVE,
    "original": PROFILE_NATIVE,
    "raw": PROFILE_NATIVE,
    "nai": PROFILE_NATIVE,
    "nai_faithful": PROFILE_NAI_FAITHFUL,
    "nai_expressive": PROFILE_NAI_EXPRESSIVE,
    "anima": PROFILE_NAI_EXPRESSIVE,
    "anima_v1": PROFILE_NAI_FAITHFUL,
    "anima_faithful": PROFILE_NAI_FAITHFUL,
    "faithful": PROFILE_NAI_FAITHFUL,
    "v1": PROFILE_NAI_FAITHFUL,
    "anima_v2": PROFILE_NAI_EXPRESSIVE,
    "anima_epic": PROFILE_NAI_EXPRESSIVE,
    "epic": PROFILE_NAI_EXPRESSIVE,
    "v2": PROFILE_NAI_EXPRESSIVE,
    "deepseek": PROFILE_DEEPSEEK_NAI_EXPRESSIVE,
    "deepseek_nai": PROFILE_DEEPSEEK_NAI_EXPRESSIVE,
    "deepseek_nai_faithful": PROFILE_DEEPSEEK_NAI_FAITHFUL,
    "deepseek_nai_expressive": PROFILE_DEEPSEEK_NAI_EXPRESSIVE,
    "deepseek_anima": PROFILE_DEEPSEEK_NAI_EXPRESSIVE,
    "deepseek_v1": PROFILE_DEEPSEEK_NAI_FAITHFUL,
    "deepseek_anima_v1": PROFILE_DEEPSEEK_NAI_FAITHFUL,
    "deepseek_anima_faithful": PROFILE_DEEPSEEK_NAI_FAITHFUL,
    "deepseek_faithful": PROFILE_DEEPSEEK_NAI_FAITHFUL,
    "deepseek_v2": PROFILE_DEEPSEEK_NAI_EXPRESSIVE,
    "deepseek_anima_v2": PROFILE_DEEPSEEK_NAI_EXPRESSIVE,
    "deepseek_anima_epic": PROFILE_DEEPSEEK_NAI_EXPRESSIVE,
    "deepseek_epic": PROFILE_DEEPSEEK_NAI_EXPRESSIVE,
}

_QUALITY_META_TAGS = {
    "masterpiece",
    "best quality",
    "good quality",
    "high quality",
    "normal quality",
    "low quality",
    "worst quality",
    "amazing quality",
    "very aesthetic",
    "aesthetic",
    "absurdres",
    "highres",
    "newest",
    "safe",
    "sensitive",
    "nsfw",
    "explicit",
    "score_7",
    "score_8",
    "score_9",
    "rating:general",
    "rating:sensitive",
    "rating:questionable",
    "rating:explicit",
}
_IDENTITY_NOISE = {
    "solo",
    "pov",
    "from above",
    "from below",
    "from behind",
    "looking at viewer",
    "looking up",
    "looking down",
    "looking away",
    "girl",
    "boy",
    "1girl",
    "1boy",
    "female focus",
    "male focus",
}
_EXPLICIT_MARKERS = (
    "rating:explicit",
    "explicit",
    "sex",
    "penetration",
    "penis",
    "vagina",
    "pussy",
    "cum",
    "ejaculation",
)
_NSFW_MARKERS = (
    "r-18",
    "nsfw",
    "nude",
    "naked",
    "nipples",
    "breasts",
    "underwear",
    "panties",
)
_SENSITIVE_MARKERS = ("swimsuit", "bikini", "cleavage", "suggestive")
_WEIGHT_RE = re.compile(r"^(-?\d+(?:\.\d+)?)::(.+?)(?:::)??$", re.IGNORECASE)
_SD_WEIGHT_RE = re.compile(r"^\((.+?):(-?\d+(?:\.\d+)?)\)$", re.IGNORECASE)


def normalize_prompt_profile(value: Any) -> str:
    raw = str(value or "").strip().lower().replace("-", "_").replace(" ", "_")
    return _PROFILE_ALIASES.get(raw, PROFILE_NATIVE)


def is_deepseek_prompt_profile(value: Any) -> bool:
    return normalize_prompt_profile(value) in {
        PROFILE_DEEPSEEK_NAI_FAITHFUL,
        PROFILE_DEEPSEEK_NAI_EXPRESSIVE,
    }


def deterministic_profile_for(value: Any) -> str:
    profile = normalize_prompt_profile(value)
    if profile == PROFILE_DEEPSEEK_NAI_FAITHFUL:
        return PROFILE_NAI_FAITHFUL
    if profile == PROFILE_DEEPSEEK_NAI_EXPRESSIVE:
        return PROFILE_NAI_EXPRESSIVE
    return profile


def apply_prompt_profile_to_comment(
    comment: dict[str, Any],
    profile: Any,
    *,
    model: str = "",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return a profiled copy of a NovelAI Comment payload."""
    raw_profile = str(profile or "").strip().lower().replace("-", "_").replace(" ", "_")
    prompt_profile = deterministic_profile_for(profile)
    patched = copy.deepcopy(comment or {})
    if prompt_profile == PROFILE_NATIVE:
        return patched, {"id": PROFILE_NATIVE, "label": "Original NAI", "applied": False}

    safety = _safety_tag(_comment_text(patched))
    expressive = prompt_profile == PROFILE_NAI_EXPRESSIVE
    selected_model = model or _comment_model(patched)
    legacy_alias = not selected_model and raw_profile in {
        "anima",
        "anima_v1",
        "anima_faithful",
        "faithful",
        "v1",
        "anima_v2",
        "anima_epic",
        "epic",
        "v2",
    }
    dialect = "anima-legacy" if legacy_alias else nai_model_dialect(selected_model)
    if legacy_alias:
        safety = {
            "rating:general": "safe",
            "rating:sensitive": "sensitive",
            "rating:explicit": "nsfw",
        }.get(safety, "safe")
    v4 = patched.get("v4_prompt")
    if isinstance(v4, dict):
        cap = v4.get("caption")
        if isinstance(cap, dict):
            base = str(cap.get("base_caption") or patched.get("prompt") or "")
            cap["base_caption"] = _format_nai_prompt(
                base,
                safety=safety,
                expressive=expressive,
                is_base=True,
                dialect=dialect,
            )
            for item in cap.get("char_captions") or []:
                if isinstance(item, dict):
                    item["char_caption"] = _format_nai_prompt(
                        str(item.get("char_caption") or ""),
                        safety=safety,
                        expressive=expressive,
                        is_base=False,
                        dialect=dialect,
                    )
            v4["caption"] = cap
            patched["v4_prompt"] = v4
            patched["prompt"] = cap["base_caption"]
    else:
        patched["prompt"] = _format_nai_prompt(
            str(patched.get("prompt") or ""),
            safety=safety,
            expressive=expressive,
            is_base=True,
            dialect=dialect,
        )

    _clean_negative_payload(patched)
    # The profile writes explicit, model-specific quality tags.  Keeping NAI's
    # hidden quality preamble enabled would duplicate or conflict with them.
    patched["qualityToggle"] = False
    label = "NAI 表现力增强" if expressive else "NAI 还原优先"
    patched["_aitag_prompt_profile"] = {
        "id": prompt_profile,
        "label": label,
        "provider": "local",
        "dialect": dialect,
    }
    return patched, {
        "id": prompt_profile,
        "label": label,
        "applied": True,
        "safety": safety,
        "dialect": dialect,
    }


def _comment_model(comment: dict[str, Any]) -> str:
    return str(comment.get("model") or comment.get("Source") or comment.get("source") or "")


def nai_model_dialect(model: Any) -> str:
    """Return the prompt dialect used by the target NovelAI image model."""
    value = str(model or "").strip().lower().replace("_", "-")
    if "4-5" in value or "4.5" in value or "v4.5" in value:
        return "nai-v4.5"
    if "v3" in value or "diffusion-3" in value:
        return "nai-v3"
    return "nai-v4"


def _comment_text(comment: dict[str, Any]) -> str:
    # Undesired Content describes what the user does *not* want.  Including it
    # here would incorrectly turn prompts such as uc="nude" into sensitive
    # generations.
    parts = [str(comment.get("prompt") or "")]
    v4 = comment.get("v4_prompt")
    if isinstance(v4, dict):
        cap = v4.get("caption")
        if isinstance(cap, dict):
            parts.append(str(cap.get("base_caption") or ""))
            for item in cap.get("char_captions") or []:
                if isinstance(item, dict):
                    parts.append(str(item.get("char_caption") or ""))
    return "\n".join(parts).lower()


def _safety_tag(text: str) -> str:
    low = str(text or "").lower()
    if any(marker in low for marker in _EXPLICIT_MARKERS):
        return "rating:explicit"
    if any(marker in low for marker in _NSFW_MARKERS):
        return "rating:sensitive"
    if any(marker in low for marker in _SENSITIVE_MARKERS):
        return "rating:sensitive"
    return "rating:general"


def classify_prompt_safety(text: str) -> str:
    """Public metadata-only safety classifier for external vision routing."""
    return _safety_tag(text)


def _format_nai_prompt(
    text: str,
    *,
    safety: str,
    expressive: bool,
    is_base: bool,
    dialect: str,
) -> str:
    tags = _clean_positive_tags(text, skip_quality=is_base, dialect=dialect)
    prefix = _quality_prefix(safety, expressive, dialect) if is_base else []
    if not is_base:
        tags = [_character_slot_subject(tag) for tag in tags]
    if expressive and is_base:
        emphasis = [
            _nai_emphasis("dynamic angle", 1.5 if dialect == "anima-legacy" else 1.35, dialect),
            _nai_emphasis("intricate details", 1.2, dialect),
        ]
        tags = _prepend_unique(tags, emphasis)

    lines: list[str] = []
    first_line = _join_line(prefix + tags)
    if first_line:
        lines.append(first_line)

    if is_base and dialect == "anima-legacy":
        character = _character_sentence(text)
        if character:
            lines.append(character)
    if expressive and is_base and dialect in {"nai-v4", "nai-v4.5"}:
        lines.append(_style_sentence())
    elif expressive and is_base and dialect == "anima-legacy":
        lines.append(_style_sentence().replace("Cinematic", "cinematic", 1))
    return "\n".join(line for line in lines if line).strip()


def _quality_prefix(safety: str, expressive: bool, dialect: str) -> list[str]:
    if dialect == "anima-legacy":
        quality = (
            ["masterpiece", "best quality", "score_9"]
            if expressive
            else ["best quality", "good quality", "score_7"]
        )
    elif dialect == "nai-v4.5":
        quality = ["masterpiece", "best quality", "very aesthetic"]
    elif dialect == "nai-v4":
        quality = ["masterpiece", "best quality", "very aesthetic", "absurdres"]
    else:
        quality = ["best quality", "amazing quality", "very aesthetic", "absurdres"]
    if expressive:
        quality.append("highly detailed")
    return quality + [safety]


def _style_sentence() -> str:
    return (
        "Cinematic lighting and a controlled depth of field keep the subject clear "
        "while preserving the original composition."
    )


def _nai_emphasis(tag: str, weight: float, dialect: str) -> str:
    display = _display_tag(tag)
    if dialect == "anima-legacy":
        return f"({display}:{weight:g})"
    if dialect in {"nai-v4", "nai-v4.5"}:
        return f"{weight:g}::{display}::"
    return "{" + display + "}"


def _character_slot_subject(tag: str) -> str:
    low = tag.strip().lower()
    replacements = {"1girl": "girl", "1boy": "boy", "1other": "other"}
    return replacements.get(low, tag)


def _character_sentence(text: str) -> str:
    buckets = classify_caption_buckets(text)
    identity = _first_identity(buckets.get("identity") or [])
    if not identity:
        return ""
    appearance = [_display_tag(t) for t in (buckets.get("body") or []) + (buckets.get("appearance") or [])]
    appearance = [x for x in appearance if x and x.lower() not in _QUALITY_META_TAGS]
    name = _identity_phrase(identity)
    if appearance:
        return f"{name} is described with {', '.join(appearance[:8])}."
    return f"{name} is described with clear hair, eye, outfit, and silhouette details."


def _first_identity(tags: list[str]) -> str:
    for tag in tags:
        display = _display_tag(tag).lower()
        if not display or display in _IDENTITY_NOISE:
            continue
        if _is_artist_tag(tag):
            continue
        return tag
    return ""


def _clean_positive_tags(text: str, *, skip_quality: bool, dialect: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in split_prompt_tags(text):
        if _is_artist_tag(raw):
            continue
        display = _display_weighted_tag(raw, dialect=dialect)
        if not display:
            continue
        low = _unweighted_text(display).lower()
        if skip_quality and low in _QUALITY_META_TAGS:
            continue
        if low in seen:
            continue
        seen.add(low)
        out.append(display)
    return out


_FORBIDDEN_CHAR_UC_TAGS = frozenset({
    "head", "face", "body", "human", "person", "figure", "torso", "upper body", "lower body"
})


def _clean_negative_payload(comment: dict[str, Any]) -> None:
    if comment.get("uc"):
        comment["uc"] = _clean_negative_text(str(comment.get("uc") or ""))
    v4n = comment.get("v4_negative_prompt")
    if not isinstance(v4n, dict):
        return
    cap = v4n.get("caption")
    if not isinstance(cap, dict):
        return
    if cap.get("base_caption"):
        cap["base_caption"] = _clean_negative_text(str(cap.get("base_caption") or ""))
    for item in cap.get("char_captions") or []:
        if isinstance(item, dict) and item.get("char_caption"):
            item["char_caption"] = _clean_char_negative_text(str(item.get("char_caption") or ""))


def _clean_char_negative_text(text: str) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for raw in split_prompt_tags(text):
        if _is_artist_tag(raw):
            continue
        display = _display_tag(raw)
        if not display:
            continue
        low = display.lower()
        if low in _FORBIDDEN_CHAR_UC_TAGS or low in seen:
            continue
        seen.add(low)
        out.append(display)
    return ", ".join(out)


def _clean_negative_text(text: str) -> str:
    out: list[str] = []
    seen: set[str] = set()
    for raw in split_prompt_tags(text):
        if _is_artist_tag(raw):
            continue
        display = _display_tag(raw)
        if not display:
            continue
        low = display.lower()
        if low in seen:
            continue
        seen.add(low)
        out.append(display)
    return ", ".join(out)


def _display_weighted_tag(tag: str, *, dialect: str) -> str:
    weight, inner = _weight_and_inner(tag)
    if weight is None:
        sd = _SD_WEIGHT_RE.match(str(tag or "").strip())
        if sd:
            inner = str(sd.group(1) or "").strip()
            try:
                weight = float(sd.group(2))
            except ValueError:
                weight = None
    display = _display_tag(inner if inner else tag)
    if not display:
        return ""
    if weight is not None and weight != 1:
        return _nai_emphasis(display, min(max(weight, -6.0), 6.0), dialect)
    return display


def _unweighted_text(tag: str) -> str:
    weight, inner = _weight_and_inner(tag)
    if weight is not None:
        return _display_tag(inner)
    sd = _SD_WEIGHT_RE.match(str(tag or "").strip())
    if sd:
        return _display_tag(str(sd.group(1) or ""))
    return _display_tag(tag)


def _weight_and_inner(tag: str) -> tuple[float | None, str]:
    raw = str(tag or "").strip()
    m = _WEIGHT_RE.match(raw)
    if not m:
        return None, ""
    try:
        return float(m.group(1)), str(m.group(2) or "").strip().rstrip(":")
    except ValueError:
        return None, ""


def _display_tag(tag: str) -> str:
    raw = str(tag or "").strip()
    if not raw:
        return ""
    raw = re.sub(r"^::", "", raw)
    raw = re.sub(r"::$", "", raw)
    raw = raw.strip("{}[] ")
    raw = raw.replace("_", " ")
    raw = re.sub(r"\s+", " ", raw).strip()
    return raw


def _display_identity(tag: str) -> str:
    display = _display_tag(tag)
    display = re.sub(r"\s*\(([^)]+)\)\s*$", r", \1", display)
    parts = [p.strip() for p in display.split(",")]
    return ", ".join(p.title() if p else p for p in parts)


def _identity_phrase(tag: str) -> str:
    display = _display_tag(tag)
    m = re.match(r"^(.+?)\s*\(([^)]+)\)\s*$", display)
    if m:
        name = str(m.group(1) or "").strip().title()
        series = str(m.group(2) or "").strip().title()
        if name and series:
            return f"{name} from {series}"
    return _display_identity(tag)


def _join_line(parts: list[str]) -> str:
    cleaned = [str(p).strip() for p in parts if str(p or "").strip()]
    if not cleaned:
        return ""
    return ", ".join(cleaned) + ","


def _prepend_unique(tags: list[str], prefix: list[str]) -> list[str]:
    seen = {t.lower() for t in prefix}
    return prefix + [tag for tag in tags if tag.lower() not in seen]


def _lookup_tag(tag: str) -> str:
    low = _display_tag(tag).lower()
    if low.startswith("artist:"):
        low = low.split(":", 1)[1].strip()
    return low.replace(" ", "_")


def _is_artist_tag(tag: str) -> bool:
    raw_low = str(tag or "").strip().lower()
    if raw_low.startswith("::") and raw_low.endswith("::"):
        return True
    sd = _SD_WEIGHT_RE.match(str(tag or "").strip())
    if sd and _is_artist_tag(str(sd.group(1) or "")):
        return True
    _weight, inner = _weight_and_inner(tag)
    if inner:
        inner_low = inner.lower()
        if inner_low.startswith("artist:"):
            return True
        return _is_artist_tag(inner)
    display_low = _display_tag(tag).lower()
    if raw_low.startswith("artist:") or display_low.startswith("artist:"):
        return True
    if display_low in {"artist collaboration", "solo artist"}:
        return True
    idx = _style_index()
    key = _lookup_tag(tag)
    return key in (idx.get("artists") or {}) or display_low in (idx.get("artists") or {})


@lru_cache(maxsize=1)
def _style_index() -> dict[str, Any]:
    path = data_dir() / "danbooru_style_tags.json"
    if not path.exists():
        return {"artists": {}, "styles": {}, "meta": {}}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {"artists": {}, "styles": {}, "meta": {}}
    except Exception:
        return {"artists": {}, "styles": {}, "meta": {}}
