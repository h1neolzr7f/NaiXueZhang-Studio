"""Server-owned safety and NAI qualification for remote discovery records."""

from __future__ import annotations

import json
import re
from typing import Any, Mapping, Protocol


class _ImageLike(Protocol):
    model: str
    prompt_text: str
    ai_json: Any


class WorkQualificationInput(Protocol):
    metadata: Mapping[str, Any]
    tags: tuple[str, ...]
    ai_type: str
    images: tuple[_ImageLike, ...]


_UNSAFE_TAGS = frozenset(
    {
        "r-18", "r18", "nsfw", "explicit", "questionable", "adult",
        "sex", "nude", "naked", "nipples", "pussy", "penis", "cum",
        "gore", "guro",
    }
)


def _positive_int(value: Any) -> bool:
    try:
        return int(value) > 0
    except (TypeError, ValueError):
        return False


def aitag_work_is_safe(work: WorkQualificationInput) -> bool:
    """Conservatively classify remote content without trusting the browser."""

    metadata = work.metadata if isinstance(work.metadata, Mapping) else {}
    if any(_positive_int(metadata.get(key)) for key in ("x_restrict", "xRestrict")):
        return False
    values = [
        metadata.get("rating"),
        metadata.get("safety"),
        *work.tags,
    ]
    normalized = {
        str(value or "").strip().casefold().replace("_", "-").replace(" ", "-")
        for value in values
        if str(value or "").strip()
    }
    if normalized & _UNSAFE_TAGS:
        return False
    joined = " ".join(normalized)
    return not any(marker in joined for marker in ("r-18", "r18", "nsfw", "explicit"))


def aitag_work_is_nai(work: WorkQualificationInput) -> bool:
    metadata = work.metadata if isinstance(work.metadata, Mapping) else {}
    model_signals = [
        work.ai_type,
        metadata.get("model"),
        metadata.get("Source"),
        metadata.get("Software"),
        *(image.model for image in work.images),
        *(
            signal
            for image in work.images
            for signal in _ai_json_model_signals(image.ai_json)
        ),
    ]
    model_text = " ".join(str(value or "").casefold() for value in model_signals)
    return bool(
        "novelai" in model_text
        or "nai-diffusion" in model_text
        or re.search(r"(?:^|[\s_-])nai(?:$|[\s_-]|v\d)", model_text)
    )


_AI_JSON_MODEL_KEYS = frozenset(
    {"software", "source", "model", "ai_type", "aitype"}
)


def _ai_json_model_signals(value: Any) -> tuple[str, ...]:
    """Extract producer/model evidence without treating prompt prose as proof."""

    parsed = value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            if re.search(
                r"(?:software|source|model|ai[_-]?type)\s*[:=]\s*[^,;\n]*(?:novelai|nai-diffusion)",
                text,
                re.IGNORECASE,
            ):
                return (text[:1000],)
            return ()

    signals: list[str] = []

    def walk(item: Any, depth: int = 0) -> None:
        if depth > 6 or len(signals) >= 128:
            return
        if isinstance(item, Mapping):
            for key, nested in item.items():
                normalized_key = str(key or "").strip().casefold().replace("-", "_")
                if normalized_key in _AI_JSON_MODEL_KEYS and not isinstance(
                    nested, (Mapping, list, tuple)
                ):
                    signals.append(str(nested or "")[:1000])
                if isinstance(nested, (Mapping, list, tuple)):
                    walk(nested, depth + 1)
        elif isinstance(item, (list, tuple)):
            for nested in item:
                walk(nested, depth + 1)

    walk(parsed)
    return tuple(signals)


def qualify_aitag_work(
    work: WorkQualificationInput,
) -> tuple[str, tuple[str, ...]]:
    """Classify a work as direct, remix-only or manual review."""

    metadata = work.metadata if isinstance(work.metadata, Mapping) else {}
    has_prompt = bool(
        work.tags
        or metadata.get("prompt")
        or metadata.get("positive_prompt")
        or any(image.prompt_text or image.ai_json for image in work.images)
    )
    if not aitag_work_is_safe(work):
        return "review", ("unsafe-rating-or-tag",)
    if aitag_work_is_nai(work) and has_prompt:
        return "direct", ("novelai-metadata", "prompt-available")
    if has_prompt:
        return "remix-only", ("non-novelai-source", "prompt-available")
    return "review", ("prompt-metadata-missing",)


__all__ = ["aitag_work_is_nai", "aitag_work_is_safe", "qualify_aitag_work"]
