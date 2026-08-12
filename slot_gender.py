"""角色槽性别识别：评分 + 多槽/底图上下文，供 extract/transform 与前端共用。"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from char_tag_db import (
    ARKNIGHTS_RE,
    _CURE_CHAR_RE,
    classify_caption_buckets,
    load_index,
    split_prompt_tags,
)

try:
    from ark_char_library import EXPLICIT_MALE as _ARK_EXPLICIT_MALE
except Exception:
    _ARK_EXPLICIT_MALE = frozenset()

_GENDER_MALE_EXACT = frozenset(
    {
        "1boy",
        "2boys",
        "3boys",
        "4boys",
        "5boys",
        "6+boys",
        "male_focus",
        "boys_only",
        "yaoi",
        "bara",
    }
)
_GENDER_FEMALE_EXACT = frozenset(
    {
        "1girl",
        "2girls",
        "3girls",
        "4girls",
        "5girls",
        "6+girls",
        "female_focus",
        "girls_only",
        "yuri",
        "yuri_focus",
    }
)
_IMPLICIT_FEMALE = frozenset(
    {
        "loli",
        "1woman",
        "woman",
        "female",
        "young girl",
        "teenage girl",
        "wife",
        "mother",
        "sister",
        "magical girl",
        "schoolgirl",
        "mature_female",
        "goddess",
        "princess",
        "idol",
        "maid",
        "nun",
        "witch",
        "catgirl",
        "fox_girl",
        "bunny_girl",
    }
)
_IMPLICIT_MALE = frozenset(
    {
        "shota",
        "1man",
        "man",
        "male",
        "husband",
        "father",
        "brother",
        "schoolboy",
        "mature_male",
        "old_man",
        "bara",
        "otoko",
    }
)
_FEMALE_BODY = (
    "pink panty",
    "panties",
    "panty",
    "bra",
    "breasts",
    "boobs",
    "vagina",
    "pussy",
    "womb",
    "menstruation",
    "cameltoe",
    "nipples",
    "areola",
    "uterus",
    "ovary",
    "clitoris",
    "labia",
    "thong",
    "lingerie",
    "crop top",
    "skirt",
    "dress",
)
_MALE_BODY = (
    "penis",
    "balls",
    "testicles",
    "bulge",
    "erection",
    "ejaculation",
    "cum on male",
    "male masturbation",
    "large penis",
    "huge penis",
    "foreskin",
    "phimosis",
)
_FRAMING_NOISE = frozenset(
    {
        "from above",
        "from below",
        "from side",
        "from behind",
        "wide shot",
        "close up",
        "close-up",
        "full body",
        "upper body",
        "cowboy shot",
        "portrait",
    }
)


@dataclass
class GenderScore:
    female: float = 0.0
    male: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def add(self, side: str, weight: float, reason: str) -> None:
        if side == "female":
            self.female += weight
        elif side == "male":
            self.male += weight
        if reason and reason not in self.reasons:
            self.reasons.append(reason)


def _normalize_token(tag: str) -> str:
    low = str(tag or "").strip().lower()
    low = re.sub(r"^\{+|\}+$", "", low)
    wrapped = re.fullmatch(r"\((.+)\)", low)
    if wrapped:
        low = wrapped.group(1).strip()
    low = re.sub(r"^\d+(?:\.\d+)?::", "", low)
    low = re.sub(r"::$", "", low).strip()
    return low


def _character_match_keys(tag: str) -> set[str]:
    """生成与 danbooru 角色索引匹配的候选键：空格/下划线、带/不带系列后缀。

    索引（char_tag_db load_index）用 danbooru 风格下划线键，如
    ``sangonomiya_kokomi`` / ``hilichurl_(genshin_impact)``，而槽位 caption
    常用空格形式 ``sangonomiya kokomi`` 或 ``hilichurl (genshin impact)``。
    """
    keys: set[str] = set()
    for base in (str(tag or "").strip().lower(), _normalize_token(tag)):
        if not base:
            continue
        keys.add(base)
        keys.add(base.replace(" ", "_"))
        stripped = re.sub(r"[\(\（].*?[\)\）]\s*$", "", base).strip()
        if stripped:
            keys.add(stripped)
            keys.add(stripped.replace(" ", "_"))
    return keys


def _token_gender(tag: str) -> str:
    raw_low = str(tag or "").strip().lower()
    low = _normalize_token(tag)
    if not low or low in _FRAMING_NOISE:
        return ""
    if raw_low in _ARK_EXPLICIT_MALE or low in _ARK_EXPLICIT_MALE:
        return "male"
    if low in _GENDER_MALE_EXACT or low in _IMPLICIT_MALE:
        return "male"
    if low in _GENDER_FEMALE_EXACT or low in _IMPLICIT_FEMALE:
        return "female"
    if "shota" in low:
        return "male"
    if "loli" in low:
        return "female"
    if low.endswith("_boy") or "(boy)" in low or low == "boy":
        return "male"
    if low.endswith("_girl") or "(girl)" in low or low == "girl":
        return "female"
    if ARKNIGHTS_RE.search(low) or low.endswith("_(arknights)") or raw_low.endswith("_(arknights)"):
        # 显式男性干员名单（来自 ark_char_library.EXPLICIT_MALE），防止被误判为 female
        male_arknights_hints = (
            "silverash", "silverash_(arknights)", "silverash (arknights)",
            "mountain_(arknights)", "mountain (arknights)",
            "thorns_(arknights)", "thorns (arknights)",
            "saria_(arknights)", "saria (arknights)",
            "phatom_(arknights)", "phatom (arknights)",
            "eunectes_(arknights)", "eunectes (arknights)",
            "bagpipe_(arknights)", "bagpipe (arknights)",
            "lee_(arknights)", "lee (arknights)",
            "mizuki_(arknights)", "mizuki (arknights)",
            "gnosis_(arknights)", "gnosis (arknights)",
            "passenger_(arknights)", "passenger (arknights)",
            "specter_(arknights)", "specter (arknights)",
            "hung_(arknights)", "hung (arknights)",
            "aak_(arknights)", "aak (arknights)",
            "waaifu_(arknights)", "waaifu (arknights)",
        )
        if low in male_arknights_hints or raw_low in male_arknights_hints:
            return "male"
        # 排除已知男性干员后，默认归 female（NAI 生成以女性角色为主）
        if raw_low in _ARK_EXPLICIT_MALE or low in _ARK_EXPLICIT_MALE:
            return "male"
        return "female"
    if _CURE_CHAR_RE.match(low) or _CURE_CHAR_RE.match(raw_low):
        return "female"
    return ""


def score_caption_gender(caption: str, *, uc_caption: str = "") -> GenderScore:
    """对单槽咒语打分。"""
    score = GenderScore()
    text = str(caption or "")
    low = text.lower()
    buckets = classify_caption_buckets(text)

    for tag in buckets.get("gender") or []:
        side = _token_gender(tag)
        if side == "male":
            score.add("male", 120, f"gender:{tag}")
        elif side == "female":
            score.add("female", 120, f"gender:{tag}")

    idx = load_index()
    for tag in (buckets.get("identity") or []) + (buckets.get("body") or []):
        raw_low = str(tag or "").strip().lower()
        low_tag = _normalize_token(tag)
        if not low_tag:
            continue
        if low_tag in {"loli", "mature_female"}:
            score.add("female", 70, f"body:{tag}")
        elif low_tag in {"shota", "mature_male"}:
            score.add("male", 70, f"body:{tag}")
        side = _token_gender(tag)
        if side:
            score.add(side, 85, f"identity:{tag}")
        elif _character_match_keys(tag) & idx.get("characters", set()):
            if raw_low in _ARK_EXPLICIT_MALE or low_tag in _ARK_EXPLICIT_MALE:
                score.add("male", 95, f"char:{tag}")
            else:
                score.add("female", 75, f"char:{tag}")

    for tag in split_prompt_tags(text):
        side = _token_gender(tag)
        if side:
            score.add(side, 55, f"tag:{tag[:32]}")

    for marker in _FEMALE_BODY:
        if marker in low:
            score.add("female", 35, f"body_hint:{marker}")
    for marker in _MALE_BODY:
        if marker in low:
            score.add("male", 45, f"body_hint:{marker}")

    if re.search(r"(?<![a-z])shota(?![a-z])", low):
        score.add("male", 65, "text:shota")
    if re.search(r"(?<![a-z])loli(?![a-z])", low):
        score.add("female", 65, "text:loli")

    uc_low = str(uc_caption or "").lower()
    if "penis" in uc_low or "male" in uc_low:
        score.add("male", 15, "uc_male")
    if "girl" in uc_low or "female" in uc_low:
        score.add("female", 15, "uc_female")

    return score


def score_base_caption(base_caption: str) -> GenderScore:
    """从底图咒语推断本图性别构成（1girl/1boy/2girls 等）。"""
    score = GenderScore()
    low = str(base_caption or "").lower()
    if not low:
        return score

    patterns = (
        (r"\b1boy\b", "male", 80, "base:1boy"),
        (r"\b2boys?\b", "male", 90, "base:2boys"),
        (r"\b3boys?\b", "male", 95, "base:3boys"),
        (r"\b1girl\b", "female", 80, "base:1girl"),
        (r"\b2girls?\b", "female", 90, "base:2girls"),
        (r"\b3girls?\b", "female", 95, "base:3girls"),
        (r"\bmale_focus\b", "male", 70, "base:male_focus"),
        (r"\bfemale_focus\b", "female", 70, "base:female_focus"),
        (r"\bgirls?_only\b", "female", 75, "base:girls_only"),
        (r"\bboys?_only\b", "male", 75, "base:boys_only"),
        (r"\byaoi\b", "male", 60, "base:yaoi"),
        (r"\byuri\b", "female", 60, "base:yuri"),
        (r"\bhetero\b", "female", 25, "base:hetero"),
        (r"\bhetero\b", "male", 25, "base:hetero"),
    )
    for pat, side, w, reason in patterns:
        if re.search(pat, low):
            score.add(side, w, reason)
    return score


def _decide_from_score(
    score: GenderScore,
    *,
    min_margin: float = 12.0,
) -> tuple[str, float]:
    diff = score.female - score.male
    total = score.female + score.male
    if diff >= min_margin:
        conf = min(0.99, 0.55 + min(diff, 120) / 160)
        return "female", conf
    if diff <= -min_margin:
        conf = min(0.99, 0.55 + min(-diff, 120) / 160)
        return "male", conf
    if total <= 0:
        return "unknown", 0.0
    if score.female > 0 and score.male > 0:
        return "unknown", 0.25
    return "unknown", 0.0


def resolve_slot_genders(
    chars: list[dict[str, Any]],
    *,
    base_caption: str = "",
) -> list[dict[str, str | float]]:
    """多槽 + 底图联合推断，返回每槽 gender/confidence/source。"""
    n = len(chars)
    if not n:
        return []

    base_score = score_base_caption(base_caption)
    slot_scores = [
        score_caption_gender(
            str(ch.get("char_caption") or ""),
            uc_caption=str(ch.get("uc_caption") or ""),
        )
        for ch in chars
    ]

    results: list[dict[str, str | float]] = []
    for i, sc in enumerate(slot_scores):
        merged = GenderScore(
            female=sc.female + base_score.female * (0.35 if n == 1 else 0.15),
            male=sc.male + base_score.male * (0.35 if n == 1 else 0.15),
            reasons=[*sc.reasons, *base_score.reasons],
        )
        gender, conf = _decide_from_score(merged)
        source = merged.reasons[0] if merged.reasons else "score"
        results.append({"gender": gender, "confidence": conf, "source": source})

    known_female = [i for i, r in enumerate(results) if r["gender"] == "female"]
    known_male = [i for i, r in enumerate(results) if r["gender"] == "male"]
    unknown = [i for i, r in enumerate(results) if r["gender"] == "unknown"]

    # 双槽互补：已明确一男一女时，另一未知槽归对立性别
    if n == 2 and len(unknown) == 1:
        u = unknown[0]
        if len(known_male) == 1 and not known_female:
            results[u] = {
                "gender": "female",
                "confidence": 0.72,
                "source": "pair_infer",
            }
        elif len(known_female) == 1 and not known_male:
            results[u] = {
                "gender": "male",
                "confidence": 0.72,
                "source": "pair_infer",
            }

    # 重新统计
    known_female = [i for i, r in enumerate(results) if r["gender"] == "female"]
    known_male = [i for i, r in enumerate(results) if r["gender"] == "male"]
    unknown = [i for i, r in enumerate(results) if r["gender"] == "unknown"]

    # 底图 1girl/1boy 仅单槽时加权
    if n == 1 and unknown:
        if base_score.female >= 80 and base_score.male < 40:
            results[0] = {
                "gender": "female",
                "confidence": 0.78,
                "source": "base_solo",
            }
        elif base_score.male >= 80 and base_score.female < 40:
            results[0] = {
                "gender": "male",
                "confidence": 0.78,
                "source": "base_solo",
            }

    unknown = [i for i, r in enumerate(results) if r["gender"] == "unknown"]

    # 多槽按底图 2girls / 2boys 分配
    if unknown and n >= 2:
        if base_score.female >= 90 and base_score.male < 30 and len(unknown) <= n:
            for u in unknown[: max(0, int(base_score.female // 45))]:
                if results[u]["gender"] == "unknown":
                    results[u] = {
                        "gender": "female",
                        "confidence": 0.65,
                        "source": "base_multi",
                    }
        if base_score.male >= 90 and base_score.female < 30:
            for u in unknown:
                if results[u]["gender"] == "unknown":
                    results[u] = {
                        "gender": "male",
                        "confidence": 0.65,
                        "source": "base_multi",
                    }

    unknown = [i for i, r in enumerate(results) if r["gender"] == "unknown"]

    # 单槽仍未知：NAI 单角色图常见无 1girl，按弱女向默认（仅无男向分时）
    if n == 1 and unknown:
        sc = slot_scores[0]
        if sc.male <= 0 and (sc.female > 0 or base_score.female > base_score.male):
            results[0] = {
                "gender": "female",
                "confidence": max(0.42, 0.35 + sc.female / 200),
                "source": "solo_female_default",
            }
        elif sc.female <= 0 and sc.male > 0:
            results[0] = {
                "gender": "male",
                "confidence": max(0.42, 0.35 + sc.male / 200),
                "source": "solo_male_default",
            }
        elif sc.male <= 0:
            results[0] = {
                "gender": "female",
                "confidence": 0.38,
                "source": "solo_ambiguous",
            }

    return results


def apply_slot_genders(
    chars: list[dict[str, Any]],
    *,
    base_caption: str = "",
) -> None:
    """写入每槽 gender / gender_confidence / bundle.gender。"""
    resolved = resolve_slot_genders(chars, base_caption=base_caption)
    for ch, meta in zip(chars, resolved):
        gender = str(meta.get("gender") or "unknown")
        conf = float(meta.get("confidence") or 0.0)
        source = str(meta.get("source") or "")
        ch["gender"] = gender
        ch["gender_confidence"] = round(conf, 3)
        ch["gender_source"] = source
        bundle = ch.get("bundle")
        if not isinstance(bundle, dict):
            bundle = {}
            ch["bundle"] = bundle
        bundle["gender"] = gender


def slot_gender_of(ch: dict[str, Any]) -> str:
    g = str(ch.get("gender") or "").strip().lower()
    if g in {"male", "female"}:
        return g
    bundle = ch.get("bundle") or {}
    g = str(bundle.get("gender") or "").strip().lower()
    if g in {"male", "female"}:
        return g
    meta = resolve_slot_genders(
        [ch],
        base_caption="",
    )
    return str(meta[0].get("gender") or "unknown") if meta else "unknown"


def indices_for_gender(chars: list[dict[str, Any]], gender: str) -> list[int]:
    want = str(gender or "").strip().lower()
    if want not in {"male", "female"}:
        return list(range(len(chars)))
    return [i for i, ch in enumerate(chars) if slot_gender_of(ch) == want]