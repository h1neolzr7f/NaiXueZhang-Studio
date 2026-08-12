"""本地角色/特征 tag 库：离线索引 + 快速分类（无线上依赖）。"""

from __future__ import annotations

import json
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any

try:
    import orjson
except ImportError:  # pragma: no cover - locked desktop runtime includes orjson
    orjson = None

ROOT = Path(__file__).resolve().parent
from paths import data_dir as _config_data_dir

DATA_DIR = _config_data_dir()
INDEX_PATH = DATA_DIR / "char_tag_index.json"
GROUPS_PATH = DATA_DIR / "char_tag_groups.json"

# silverash_(arknights) / priestess (arknights)
CHAR_SUFFIX_RE = re.compile(
    r"^(.+?)(?:_\(([^)]+)\)|\s*\(([^)]+)\))$",
    re.IGNORECASE,
)
_WEIGHT_PREFIX_RE = re.compile(r"^-?\d+(?:\.\d+)?::")
_STRAY_COLON_AFTER_COMMA_RE = re.compile(r",(\s*):(?![:\d])")
_UNCLOSED_WEIGHT_BEFORE_COMMA_RE = re.compile(
    r"(\d+(?:\.\d+)?::)([^,]+?)(?=,)",
)


def repair_prompt_caption(text: str) -> str:
    """修复 NAI 导出常见损坏：未闭合权重、逗号后多余冒号等。"""
    raw = str(text or "").strip()
    if not raw:
        return raw
    raw = _STRAY_COLON_AFTER_COMMA_RE.sub(r",\1", raw)
    raw = re.sub(r"^:(?![:\d])", "", raw)

    def _close_weight(m: re.Match[str]) -> str:
        head = m.group(1)
        inner = str(m.group(2) or "").strip()
        if not inner or inner.endswith("::"):
            return m.group(0)
        return f"{head}{inner}::"

    return _UNCLOSED_WEIGHT_BEFORE_COMMA_RE.sub(_close_weight, raw)


def split_prompt_tags(text: str) -> list[str]:
    """按中英文逗号分词，保留 {{...}} 与 weight::...:: 内部的逗号。"""
    raw = repair_prompt_caption(text)
    if not raw:
        return []

    parts: list[str] = []
    buf: list[str] = []
    brace_depth = 0
    i = 0
    n = len(raw)

    def flush() -> None:
        part = "".join(buf).strip()
        buf.clear()
        if part:
            parts.append(part)

    while i < n:
        ch = raw[i]
        if ch == "{":
            brace_depth += 1
            buf.append(ch)
            i += 1
            continue
        if ch == "}":
            brace_depth = max(0, brace_depth - 1)
            buf.append(ch)
            i += 1
            continue
        if brace_depth == 0 and not buf:
            m = _WEIGHT_PREFIX_RE.match(raw[i:])
            if m:
                close = raw.find("::", i + m.end())
                if close != -1:
                    inner = raw[i + m.end() : close]
                    if "," not in inner:
                        parts.append(raw[i : close + 2].strip())
                        i = close + 2
                        while i < n and raw[i] in " \t":
                            i += 1
                        if i < n and raw[i] in {",", "，"}:
                            i += 1
                        while i < n and raw[i] in " \t":
                            i += 1
                        continue
        if ch in {",", "，"} and brace_depth == 0:
            flush()
            i += 1
            continue
        buf.append(ch)
        i += 1
    flush()
    return parts
# 明日方舟 operator 常见写法
ARKNIGHTS_RE = re.compile(r"arknights|アークナイツ", re.IGNORECASE)

_APPEARANCE_SPACE_SUFFIXES = (
    " hair",
    " eyes",
    " skin",
    " ears",
    " tail",
    " horns",
)
_CLOTHING_HINTS = frozenset(
    {
        "dress",
        "skirt",
        "shirt",
        "panties",
        "bra",
        "bikini",
        "swimsuit",
        "uniform",
        "jacket",
        "coat",
        "apron",
        "kimono",
        "armor",
        "cape",
        "hood",
        "hat",
        "helmet",
        "gloves",
        "boots",
        "thighhighs",
        "stockings",
        "pantyhose",
        "shorts",
        "pants",
        "leotard",
        "sweater",
        "school_uniform",
    }
)

_APPEARANCE_SUBSTRINGS = (
    "knee socks",
    "thighhighs",
    "stockings",
    "pantyhose",
    "headband",
    "ear headband",
    "cat ear",
    "fox ears",
    "rabbit ears",
    "symbols",
    "ribbon",
    "necklace",
    "earring",
    "gloves",
    "boots",
    "socks",
    "jewelry",
    "oiled skin",
    "wet skin",
    "shiny skin",
    "glossy",
)

_index_cache: dict[str, Any] | None = None
_index_mtime: float = 0.0
_index_last_checked_at: float = 0.0
_INDEX_STAT_INTERVAL_SECONDS = 1.0


@lru_cache(maxsize=1)
def _load_groups() -> dict:
    if GROUPS_PATH.exists():
        return json.loads(GROUPS_PATH.read_text(encoding="utf-8"))
    return {}


def _creature_substrings() -> tuple[str, ...]:
    groups = _load_groups()
    return tuple(str(s).lower() for s in (groups.get("creature_substrings") or []) if s)


def _empty_index() -> dict[str, set[str]]:
    groups = _load_groups()
    return {
        "characters": set(),
        "copyrights": set(),
        "gender_male": set(groups.get("gender_male") or []),
        "gender_female": set(groups.get("gender_female") or []),
        "body": set(groups.get("body") or []),
        "appearance": set(groups.get("appearance_exact") or []),
        "creature": set(groups.get("creature") or []),
        "meta": set(),
        "action_hints": set(groups.get("action_prefixes") or []),
    }


@lru_cache(maxsize=65536)
def _normalize_tag(tag: str) -> str:
    return str(tag or "").strip().lower()


def is_identity_noise_tag(tag: str) -> bool:
    """Tags that may appear in noisy character indexes but are not identities."""
    low = _normalize_tag(tag)
    if not low:
        return False
    underscored = low.replace(" ", "_")
    spaced = low.replace("_", " ")
    if is_generic_character_tag(low):
        return True
    if is_identity_meta_noise(low):
        return True
    if is_face_tag(low) or is_face_tag(underscored):
        return True
    if is_framing_tag(low) or is_framing_tag(underscored):
        return True
    if is_action_phrase(low) or is_action_phrase(underscored) or is_action_phrase(spaced):
        return True
    if low in _IDENTITY_POSE_FALSE_POSITIVES or underscored in _IDENTITY_POSE_FALSE_POSITIVES:
        return True
    return False


def load_index(*, force: bool = False) -> dict[str, set[str]]:
    global _index_cache, _index_mtime, _index_last_checked_at
    now = time.monotonic()
    if (
        not force
        and _index_cache is not None
        and now - _index_last_checked_at < _INDEX_STAT_INTERVAL_SECONDS
    ):
        return _index_cache

    mtime = INDEX_PATH.stat().st_mtime if INDEX_PATH.exists() else 0.0
    _index_last_checked_at = now
    if not force and _index_cache is not None and mtime == _index_mtime:
        return _index_cache

    buckets = _empty_index()
    if INDEX_PATH.exists():
        try:
            raw = (
                orjson.loads(INDEX_PATH.read_bytes())
                if orjson is not None
                else json.loads(INDEX_PATH.read_text(encoding="utf-8"))
            )
            normalized_schema = int(raw.get("version") or 0) >= 2
            for key in (
                "characters",
                "copyrights",
                "gender_male",
                "gender_female",
                "body",
                "appearance",
                "creature",
                "meta",
            ):
                tags = raw.get(key) or []
                if normalized_schema:
                    # Version 2 is emitted by build_char_tag_db with normalized,
                    # sorted strings.  Bulk update avoids ~400k redundant
                    # strip/lower calls on the first replacement after startup.
                    buckets[key].update(tags)
                else:
                    buckets[key].update(_normalize_tag(tag) for tag in tags)
        except Exception:
            pass

    groups = _load_groups()
    for tag in groups.get("gender_male") or []:
        buckets["gender_male"].add(_normalize_tag(tag))
    for tag in groups.get("gender_female") or []:
        buckets["gender_female"].add(_normalize_tag(tag))
    for tag in groups.get("body") or []:
        buckets["body"].add(_normalize_tag(tag))
    for tag in groups.get("appearance_exact") or []:
        buckets["appearance"].add(_normalize_tag(tag))
    for tag in groups.get("creature") or []:
        buckets["creature"].add(_normalize_tag(tag))

    creature_cache = DATA_DIR / "danbooru_creature.json"
    if creature_cache.exists():
        try:
            raw_creature = json.loads(creature_cache.read_text(encoding="utf-8"))
            for tag in raw_creature.get("tags") or []:
                buckets["creature"].add(_normalize_tag(tag))
        except Exception:
            pass

    _index_cache = buckets
    _index_mtime = mtime
    return buckets


def reload_index() -> dict[str, set[str]]:
    global _index_last_checked_at
    _load_groups.cache_clear()
    classify_caption_cached.cache_clear()
    classify_single_tag.cache_clear()
    _normalize_tag.cache_clear()
    _index_last_checked_at = 0.0
    return load_index(force=True)


def index_stats() -> dict[str, Any]:
    idx = load_index()
    return {
        "path": str(INDEX_PATH),
        "exists": INDEX_PATH.exists(),
        "characters": len(idx["characters"]),
        "copyrights": len(idx["copyrights"]),
        "appearance": len(idx["appearance"]),
        "body": len(idx["body"]),
    }


def is_character_tag(tag: str, idx: dict[str, set[str]] | None = None) -> bool:
    low = _normalize_tag(tag)
    if not low:
        return False
    if is_identity_noise_tag(low):
        return False
    idx = idx or load_index()
    if low in idx["characters"]:
        return True
    if " " in low:
        underscored = low.replace(" ", "_")
        if underscored in idx["characters"]:
            return True
        if CHAR_SUFFIX_RE.match(underscored):
            return True
    if CHAR_SUFFIX_RE.match(low):
        return True
    if ARKNIGHTS_RE.search(low) and ("(" in low or "_(" in low):
        return True
    return False


def is_copyright_tag(tag: str, idx: dict[str, set[str]] | None = None) -> bool:
    low = _normalize_tag(tag)
    idx = idx or load_index()
    return low in idx["copyrights"]


def is_gender_tag(tag: str, idx: dict[str, set[str]] | None = None) -> bool:
    low = _normalize_tag(tag)
    idx = idx or load_index()
    return low in idx["gender_male"] or low in idx["gender_female"]


def is_body_tag(tag: str, idx: dict[str, set[str]] | None = None) -> bool:
    low = _normalize_tag(tag)
    idx = idx or load_index()
    if low in idx["body"]:
        return True
    return low.replace(" ", "_") in idx["body"]


def is_face_tag(tag: str) -> bool:
    """脸部/五官/表情类 tag（兜帽角色合并时应剔除）。"""
    low = _normalize_tag(tag)
    if not low:
        return False
    groups = _load_groups()
    if low in {str(x).lower() for x in groups.get("face_keep_exact") or []}:
        return False
    exact = {str(x).lower() for x in groups.get("face_strip_exact") or []}
    if low in exact:
        return True
    for sub in groups.get("face_strip_substrings") or []:
        if str(sub).lower() in low:
            return True
    for suffix in groups.get("appearance_suffixes") or []:
        s = str(suffix).lower().replace(" ", "_").strip()
        if s and (low.endswith(s) or low.endswith(s.strip("_"))):
            if s in {"_hair", "_eyes", "_skin", "_ears", "_tail"}:
                return True
    if re.search(r"\b(hair|eyes|skin|ears|tail)\b$", low):
        return True
    return False


def is_appearance_tag(tag: str, idx: dict[str, set[str]] | None = None) -> bool:
    low = _normalize_tag(tag)
    if not low:
        return False
    if is_appearance_weight_block(tag):
        return True
    idx = idx or load_index()
    if low in idx["appearance"] or low.replace(" ", "_") in idx["appearance"]:
        return True
    groups = _load_groups()
    for suffix in groups.get("appearance_suffixes") or []:
        if low.endswith(suffix):
            return True
    for suffix in _APPEARANCE_SPACE_SUFFIXES:
        if low.endswith(suffix):
            return True
    if low in _CLOTHING_HINTS:
        return True
    for sub in _APPEARANCE_SUBSTRINGS:
        if sub in low:
            return True
    # NAI 常写 "grey hair" / "red eyes" 而非下划线
    if re.search(r"\b(hair|eyes|skin|ears|tail)\b$", low):
        return True
    return False


_CREATURE_FALSE_POSITIVES = frozenset(
    {
        "panties aside",
        "panties_aside",
        "skirt lifted",
        "skirt_lifted",
        "bra pulled down",
        "bra_pulled_down",
        "clothes aside",
        "clothes_aside",
    }
)

_ANIMAL_SLOT_ROOTS = frozenset(
    {
        "dog",
        "wolf",
        "horse",
        "pony",
        "cat",
        "pig",
        "cow",
        "bull",
        "goat",
        "sheep",
        "insect",
        "bug",
        "spider",
        "snake",
        "monster",
        "creature",
        "dragon",
        "slime",
        "tentacle",
        "alien",
        "feral",
        "beast",
        "animal",
    }
)

_CREATURE_PHRASE_WORDS = frozenset(
    {
        "dog",
        "dogs",
        "wolf",
        "wolves",
        "horse",
        "horses",
        "pony",
        "insect",
        "insects",
        "bug",
        "bugs",
        "monster",
        "monsters",
        "creature",
        "creatures",
        "tentacle",
        "tentacles",
        "slime",
        "snake",
        "serpent",
        "bestiality",
        "zoophilia",
        "interspecies",
        "feral",
        "knot",
        "knotted",
    }
)

_IDENTITY_META_NOISE = frozenset(
    {
        "nsfw",
        "sfw",
        "explicit",
        "questionable",
        "safe",
        "rating:explicit",
        "rating:questionable",
        "rating:safe",
        "masterpiece",
        "best quality",
        "best_quality",
        "high quality",
        "high_quality",
        "low quality",
        "low_quality",
        "worst quality",
        "worst_quality",
        "normal quality",
        "normal_quality",
        "very aesthetic",
        "very_aesthetic",
        "aesthetic",
        "absurdres",
        "highres",
        "extremely detailed",
        "extremely_detailed",
        "incredibly absurdres",
        "incredibly_absurdres",
        "new generation",
        "new_generation",
        "official style",
        "official_style",
        "official art",
        "official_art",
        "official color",
        "official_color",
        "official costume",
        "official_costume",
        "anime style",
        "anime_style",
        "game cg",
        "game_cg",
        "no text",
        "no_text",
    }
)

_CURE_CHAR_RE = re.compile(r"^cure[\s_]", re.IGNORECASE)

# Danbooru 角色索引里的泛用词（不是具体角色名）
_GENERIC_CHARACTER_TAGS = frozenset(
    {
        "girl",
        "boy",
        "girls",
        "boys",
        "naked",
        "nude",
        "faceless",
        "faceless male",
        "faceless_male",
        "faceless female",
        "faceless_female",
        "solo",
        "solo focus",
        "solo_focus",
        "huge penis",
        "huge_penis",
        "large penis",
        "large_penis",
        "big penis",
        "big_penis",
        "giant penis",
        "giant_penis",
        "penis",
        "pussy",
        "vagina",
        "anus",
        "ass",
        "breasts",
        "nipples",
    }
)

_ACTION_PHRASE_HINTS = (
    "sitting",
    "standing",
    "lying",
    "laying",
    "walking",
    "running",
    "kneeling",
    "squatting",
    "leaning",
    "bending",
    "stretching",
    "looking",
    "holding",
    "grabbing",
    "wearing",
    "expression",
    "anxious",
    "nervous",
    "fidget",
    "hunched",
    "detailed",
    "interior",
    "background",
    "backseat",
    "slightly",
    "visibly",
    "nervously",
    "restless",
    "worried",
    "twisting",
    "interlocking",
    "fiddling",
    "movement",
    "fingers",
    "arm",
    "arms",
    "hand",
    "hands",
    "legs",
    "behind",
    "taxi",
    "camera",
    "view",
    "pov",
    "from_",
    "sex",
    "penetration",
    "masturbat",
    "orgasm",
    "cum",
    "nude",
    "naked",
    "penis",
    "vagina",
    "pussy",
    "anal",
)

_APPEARANCE_WEIGHT_HINTS = (
    "hair",
    "eyes",
    "eye",
    "skin",
    "slim",
    "youthful",
    "petite",
    "curvy",
    "breast",
    "shorts",
    "crop top",
    "years old",
    "year old",
    "ears",
    "ear",
    "tail",
    "horn",
    "uniform",
    "dress",
    "shirt",
    "jacket",
    "coat",
    "gloves",
    "boots",
    "socks",
    "stocking",
    "thighhigh",
    "pantyhose",
    "bikini",
    "armor",
    "cape",
    "hood",
    "hat",
    "helmet",
    "scarf",
    "ribbon",
    "bow",
    "necklace",
    "earring",
    "glasses",
    "blush",
    "freckle",
    "tattoo",
    "marking",
    "blonde",
    "silver",
    "white",
    "black",
    "brown",
    "red",
    "blue",
    "pink",
    "purple",
    "green",
    "grey",
    "gray",
    "tactical",
    "fox",
    "cat",
    "wolf",
    "rabbit",
)


def is_generic_character_tag(tag: str) -> bool:
    low = _normalize_tag(tag)
    if not low:
        return False
    return low in _GENERIC_CHARACTER_TAGS or low.replace(" ", "_") in _GENERIC_CHARACTER_TAGS


def is_action_phrase(tag: str) -> bool:
    low = _normalize_tag(tag)
    if not low:
        return False
    if "{{" in str(tag) or "::" in str(tag):
        if any(h in low for h in _APPEARANCE_WEIGHT_HINTS):
            return False
        return True
    return any(h in low for h in _ACTION_PHRASE_HINTS)


def is_appearance_weight_block(tag: str) -> bool:
    low = _normalize_tag(tag)
    if not low:
        return False
    # Match whole prompt tokens, not character-name substrings such as
    # ``hatsune_miku`` (which previously matched the hint ``hat``).
    spaced = re.sub(r"[^a-z0-9]+", " ", low).strip()
    padded = f" {spaced} "
    return any(f" {hint.replace('_', ' ')} " in padded for hint in _APPEARANCE_WEIGHT_HINTS)


_WEIGHTED_TAG_RE = re.compile(r"^-?\d+(?:\.\d+)?::(.+?)::$", re.IGNORECASE)


def weighted_tag_inner(tag: str) -> str:
    raw = str(tag or "").strip()
    m = _WEIGHTED_TAG_RE.match(raw)
    if m:
        return str(m.group(1) or "").strip()
    m2 = re.match(r"^-?\d+(?:\.\d+)?::(.+)$", raw, re.IGNORECASE)
    if m2:
        return str(m2.group(1) or "").strip().rstrip(":")
    return ""


def identity_tag_display(tag: str) -> str:
    """将权重角色 tag 转为可读角色名（如 2::firefly (hsr):: → firefly）。"""
    raw = weighted_tag_inner(tag) or str(tag or "").strip()
    if not raw:
        return ""
    m = CHAR_SUFFIX_RE.match(raw.strip())
    if m:
        name = (m.group(1) or "").strip()
        if name:
            return name.replace("_", " ")
    return raw


def _normalize_identity_candidate(tag: str) -> str:
    raw = weighted_tag_inner(tag) or str(tag or "").strip()
    raw = re.sub(r"^[{\[\s]+|[}\]\s]+$", "", raw).strip()
    wrapped = re.fullmatch(r"\((.+)\)", raw)
    if wrapped:
        raw = wrapped.group(1).strip()
    return raw


def _embedded_character_tag(tag: str, idx: dict[str, set[str]] | None = None) -> str:
    raw = _normalize_identity_candidate(tag)
    if not raw:
        return ""
    idx = idx or load_index()
    fragments = [raw]
    if "," in raw:
        fragments.extend(part.strip() for part in raw.split(",") if part.strip())
    for part in fragments:
        low = _normalize_tag(part)
        compact = low.replace(" ", "_")
        if is_identity_noise_tag(low) or is_generic_character_tag(low):
            continue
        if is_character_tag(low, idx):
            return low
        if is_character_tag(compact, idx):
            return compact
        m = CHAR_SUFFIX_RE.search(low) or CHAR_SUFFIX_RE.search(compact)
        if m:
            full = m.group(0).strip()
            if is_character_tag(full, idx):
                return full
            compact_full = full.replace(" ", "_")
            if is_character_tag(compact_full, idx):
                return compact_full
    return ""


_IDENTITY_POSE_FALSE_POSITIVES = frozenset(
    {
        "lying",
        "sitting",
        "standing",
        "walking",
        "running",
        "kneeling",
        "squatting",
        "crouching",
        "sleeping",
        "jumping",
        "dancing",
        "reading",
        "cooking",
        "eating",
        "drinking",
        "bathing",
        "swimming",
    }
)


def is_weighted_identity_tag(tag: str) -> bool:
    """NAI 权重角色名，如 2::togawa sakiko::。"""
    inner = weighted_tag_inner(tag)
    if not inner or "{{" in inner:
        return False
    if _embedded_character_tag(tag):
        return True
    if is_appearance_weight_block(inner) or is_appearance_tag(inner):
        return False
    if is_action_phrase(inner):
        return False
    low = inner.lower().strip()
    underscored = low.replace(" ", "_")
    if re.search(r"\b(smile|grin|blush|laugh|expression|looking|closed eyes)\b", low):
        return False
    if CHAR_SUFFIX_RE.match(inner.strip()) or CHAR_SUFFIX_RE.match(low):
        return True
    if is_character_tag(low) or is_character_tag(underscored):
        return True
    if ARKNIGHTS_RE.search(low) or _CURE_CHAR_RE.match(low):
        return True
    if " " in inner:
        words = inner.split()
        if 2 <= len(words) <= 4 and not any(h in low for h in _ACTION_PHRASE_HINTS):
            if is_character_tag(underscored):
                return True
            if all(re.match(r"^[\w\-]+$", w) for w in words):
                return True
    return False


def is_identity_meta_noise(tag: str) -> bool:
    low = _normalize_tag(tag)
    if not low:
        return False
    if low in _IDENTITY_META_NOISE or low.replace(" ", "_") in _IDENTITY_META_NOISE:
        return True
    if re.search(r"\b(quality|aesthetic|highres|absurdres|masterpiece)\b", low):
        return True
    if re.search(r"\bofficial[\s_](style|art|color|costume)\b", low):
        return True
    return False


def _creature_substring_hit(text: str, needle: str) -> bool:
    if not needle:
        return False
    hay = text.replace("_", " ")
    return bool(re.search(rf"(?<![a-z]){re.escape(needle)}(?![a-z])", hay))


_HUMAN_SLOT_HINTS = frozenset(
    {
        "1girl",
        "1boy",
        "2girls",
        "2boys",
        "female_focus",
        "male_focus",
        "girl",
        "boy",
        "girls",
        "boys",
    }
)


def _creature_tag_normalized(tag: str) -> str:
    low = _normalize_tag(tag)
    normalized = re.sub(r"^\d+(?:\.\d+)?::", "", low)
    return re.sub(r"::$", "", normalized).strip("{} ").strip()


def is_creature_false_positive(tag: str) -> bool:
    low = _normalize_tag(tag)
    if not low:
        return False
    if low in _CREATURE_FALSE_POSITIVES:
        return True
    return low.replace(" ", "_") in _CREATURE_FALSE_POSITIVES


def is_framing_tag(tag: str) -> bool:
    """镜头/构图/景别类 tag，不算贵物（如 cowboy_shot、upper_body）。"""
    low = _normalize_tag(tag)
    if not low:
        return False
    underscored = low.replace(" ", "_")
    groups = _load_groups()
    exact = {
        _normalize_tag(str(x))
        for x in (groups.get("creature_exclude_exact") or [])
        if str(x).strip()
    }
    if low in exact or underscored in exact:
        return True
    for suffix in groups.get("framing_suffixes") or []:
        suf = str(suffix).lower()
        if suf and underscored.endswith(suf):
            return True
    return False


def is_creature_tag(tag: str, idx: dict[str, set[str]] | None = None) -> bool:
    low = _normalize_tag(tag)
    if not low or is_creature_false_positive(tag) or is_framing_tag(tag):
        return False
    idx = idx or load_index()
    if low in idx["creature"] or low.replace(" ", "_") in idx["creature"]:
        return True
    normalized = _creature_tag_normalized(tag)
    for needle in _creature_substrings():
        if _creature_substring_hit(normalized, needle):
            return True
    return False


def is_creature_phrase(text: str) -> bool:
    """判断整段短语（含逗号/换行分片）是否属于贵物/异种描述。"""
    raw = str(text or "").strip()
    if not raw or is_creature_false_positive(raw) or is_framing_tag(raw):
        return False
    low = raw.lower().replace("_", " ")
    if is_creature_tag(raw):
        return True
    if low in _CREATURE_PHRASE_WORDS:
        return True
    if "_(arknights)" in low or " (arknights)" in low:
        return False
    words = re.findall(r"[a-z]+", low)
    if not words:
        return False
    if words[0] in _ANIMAL_SLOT_ROOTS:
        return True
    if any(w in _CREATURE_PHRASE_WORDS for w in words):
        return True
    for needle in _creature_substrings():
        if _creature_substring_hit(low, needle):
            return True
    return False


def is_creature_slot(caption: str, *, summary: str = "") -> bool:
    """整槽是否为贵物/动物角色（如 dog 槽），而非仅混有几个贵物 tag。"""
    text = str(caption or "").strip()
    if not text:
        return False
    parts = [t.strip() for t in text.split(",") if t.strip()]
    if not parts:
        return False
    head = _normalize_tag(parts[0]).replace("_", " ")
    if head in _ANIMAL_SLOT_ROOTS:
        return True
    if re.match(r"^(black|white|brown|red|grey|gray)\s+(dog|wolf|horse|cat)\b", head):
        return True
    if re.match(r"^(pure|solid)\s+black\s+dog\b", head):
        return True
    summary_low = str(summary or "").lower().strip()
    if summary_low in _ANIMAL_SLOT_ROOTS or summary_low.endswith(" dog"):
        return True
    buckets = classify_caption_buckets(text)
    creature_n = len(buckets.get("creature") or [])
    if creature_n == 0:
        return False
    has_human = any(
        _normalize_tag(t) in _HUMAN_SLOT_HINTS
        or "_(arknights)" in t.lower()
        or " (arknights)" in t.lower()
        for t in parts[:8]
    )
    identity = buckets.get("identity") or []
    has_named_char = any(
        is_character_tag(t) or "_(arknights)" in t.lower() or " (arknights)" in t.lower()
        for t in identity
    )
    if has_human or has_named_char:
        return creature_n >= max(2, len(parts) // 2)
    return creature_n > 0


def resolve_creature_char_indices(chars: list[dict]) -> list[int]:
    """返回所有贵物/动物专用槽位索引（整槽是 dog/insect 等，不含仅混有贵物 tag 的人类槽）。"""
    indices: list[int] = []
    for i, ch in enumerate(chars):
        cap = str(ch.get("char_caption") or "")
        summary = str(ch.get("summary") or "")
        if is_creature_slot(cap, summary=summary):
            indices.append(i)
    return indices


def is_action_tag(tag: str, idx: dict[str, set[str]] | None = None) -> bool:
    low = _normalize_tag(tag)
    idx = idx or load_index()
    for prefix in idx.get("action_hints") or set():
        if low.startswith(prefix):
            return True
    return False


@lru_cache(maxsize=65536)
def classify_single_tag(tag: str) -> str:
    """返回 gender / identity / body / appearance / action。"""
    idx = load_index()
    low = _normalize_tag(tag)
    if not low or low in idx["meta"]:
        return "action"
    if weighted_tag_inner(tag):
        if is_weighted_identity_tag(tag):
            return "identity"
        if is_appearance_weight_block(tag):
            return "appearance"
        return "action"
    if is_weighted_identity_tag(tag):
        return "identity"
    if is_identity_meta_noise(low):
        return "action"
    if is_gender_tag(low, idx):
        return "gender"
    if is_creature_tag(low, idx):
        return "creature"
    if low in _IDENTITY_POSE_FALSE_POSITIVES:
        return "action"
    if is_character_tag(low, idx):
        return "identity"
    if " " in low and is_character_tag(low.replace(" ", "_"), idx):
        return "identity"
    if is_copyright_tag(low, idx):
        return "identity"
    if is_body_tag(low, idx):
        return "body"
    if is_appearance_tag(low, idx):
        return "appearance"
    if is_appearance_weight_block(tag):
        return "appearance"
    if is_action_tag(low, idx):
        return "action"
    return "action"


@lru_cache(maxsize=4096)
def classify_caption_cached(caption: str) -> tuple[tuple[str, str], ...]:
    parts = split_prompt_tags(caption)
    return tuple((tag, classify_single_tag(tag)) for tag in parts)


def classify_caption_buckets(caption: str) -> dict[str, list[str]]:
    buckets: dict[str, list[str]] = {
        "identity": [],
        "gender": [],
        "body": [],
        "appearance": [],
        "creature": [],
        "action": [],
    }
    for tag, cat in classify_caption_cached(caption):
        if cat == "gender":
            buckets["gender"].append(tag)
        elif cat in buckets:
            buckets[cat].append(tag)
        else:
            buckets["action"].append(tag)
    return buckets


def pick_character_summary(caption: str, identity_tags: list[str] | None = None) -> str:
    """从角色槽咒语中提取最可能的角色名。"""
    tags = (
        classify_caption_buckets(caption)["identity"]
        if identity_tags is None
        else list(identity_tags)
    )
    idx = load_index()
    candidates: list[tuple[int, str]] = []
    parts = split_prompt_tags(caption)
    positions = {_normalize_tag(t): i for i, t in enumerate(parts)}
    if identity_tags is not None and not tags:
        return ""

    for tag in tags:
        embedded = _embedded_character_tag(tag, idx)
        if embedded:
            display = identity_tag_display(embedded)
            candidates.append((180 + len(display), display or embedded))
            continue
        inner = weighted_tag_inner(tag)
        if inner:
            display = identity_tag_display(tag)
            low = display.lower()
            score = 95
            if is_character_tag(low) or is_character_tag(low.replace(" ", "_")):
                score = 120
            m = CHAR_SUFFIX_RE.match(display.strip())
            if m and (m.group(1) or "").strip():
                score += 40
            candidates.append((score, display))
            continue
        low = _normalize_tag(tag)
        if is_gender_tag(low, idx):
            continue
        if is_identity_meta_noise(low):
            continue
        if is_copyright_tag(low, idx) and not is_character_tag(low, idx):
            continue
        score = 0
        # copyright 和 character 双命中时仍参与排序，给予基础分
        if is_copyright_tag(low, idx) and is_character_tag(low, idx):
            score = max(score, 60)
        pos = positions.get(low, positions.get(low.replace(" ", "_"), 999))
        if pos <= 2:
            score += 40
        elif pos <= 5:
            score += 20
        if _CURE_CHAR_RE.match(low):
            score += 120
        if low in idx["characters"]:
            score += 100
        m = CHAR_SUFFIX_RE.match(low)
        if m:
            score += 80
            name = (m.group(1) or "").strip()
            if name:
                candidates.append((score + len(name), name.replace("_", " ")))
            continue
        if ARKNIGHTS_RE.search(low):
            score += 60
            candidates.append((score, low.split("(")[0].strip().replace("_", " ")))
            continue
        if low not in idx["gender_male"] and low not in idx["gender_female"]:
            if score > 0 or len(low) > 2:
                candidates.append((score + 10, tag))

    if not candidates:
        for i, part in enumerate(parts[:12]):
            low = _normalize_tag(part)
            if (
                is_identity_meta_noise(low)
                or is_identity_noise_tag(low)
                or is_gender_tag(low, idx)
                or is_generic_character_tag(low)
                or is_action_phrase(part)
            ):
                continue
            if is_creature_tag(low, idx) or is_framing_tag(low):
                continue
            if is_character_tag(low, idx) or _CURE_CHAR_RE.match(low):
                candidates.append((90 - i, part))
            elif is_copyright_tag(low, idx):
                continue

    if candidates:
        candidates.sort(key=lambda x: (-x[0], -len(x[1])))
        return candidates[0][1][:48]

    return ""


def _character_default_key(tag: str) -> str:
    raw = str(tag or "").strip()
    if not raw:
        return ""
    inner = re.sub(r"^\d+(?:\.\d+)?::", "", raw)
    inner = re.sub(r"::$", "", inner).strip("{} ").strip()
    low = inner.lower().replace("_", " ")
    low = re.sub(r"\s+\([^)]+\)\s*$", "", low).strip()
    return low


def default_appearance_for_identity(identity_tags: list[str] | None) -> list[str]:
    """已知角色缺发色/瞳色时，从本地 defaults 表补全。"""
    groups = _load_groups()
    table = groups.get("character_appearance_defaults") or {}
    if not isinstance(table, dict) or not table:
        return []
    for tag in identity_tags or []:
        key = _character_default_key(tag)
        if not key:
            continue
        defaults = table.get(key) or table.get(key.replace(" ", "_"))
        if defaults:
            return [str(x).strip() for x in defaults if str(x).strip()]
    return []
