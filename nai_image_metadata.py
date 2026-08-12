"""Strict, local-only NovelAI image metadata parsing for gallery ingestion."""

from __future__ import annotations

import json
import copy
import warnings
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image, UnidentifiedImageError

from nai_prompt_tags import parse_nai_tags
from third_party.novelai_image_metadata import extract_image_metadata

PARSER_VERSION = "qq-nai-v1+novelai-3d9c7b7"
MAX_IMAGE_PIXELS = 64_000_000
MAX_TEXT_VALUE = 256_000
_COMFY_KEYS = {"workflow", "prompt"}
_COMFY_MARKERS = (
    "comfyui",
    '"class_type"',
    '"widgets_values"',
    '"last_node_id"',
    '"ksampler"',
)


@dataclass(frozen=True)
class NAIParseResult:
    accepted: bool
    reason: str
    parser_version: str = PARSER_VERSION
    metadata_source: str = ""
    prompt: str = ""
    negative_prompt: str = ""
    model: str = ""
    seed: int | None = None
    width: int = 0
    height: int = 0
    metadata: dict[str, Any] | None = None

    def storage_metadata(self) -> dict[str, Any]:
        payload = asdict(self)
        payload.pop("accepted", None)
        payload.pop("reason", None)
        return payload

    def canonical_metadata(self) -> dict[str, Any]:
        """Return the AITag-compatible NAI metadata shape used by consumers."""

        if not self.accepted:
            return {}
        payload = copy.deepcopy(self.metadata or {})
        comment = payload.get("Comment")
        if isinstance(comment, str):
            comment = _json_dict(comment)
        if not isinstance(comment, dict):
            comment = {}
        if self.prompt and not comment.get("prompt"):
            comment["prompt"] = self.prompt
        if self.negative_prompt and not comment.get("uc"):
            comment["uc"] = self.negative_prompt
        if self.seed is not None and comment.get("seed") is None:
            comment["seed"] = self.seed
        if self.width and comment.get("width") is None:
            comment["width"] = self.width
        if self.height and comment.get("height") is None:
            comment["height"] = self.height
        payload["Software"] = _text(payload.get("Software") or "NovelAI", limit=512)
        payload["Source"] = _text(payload.get("Source") or self.model, limit=1024)
        payload["Description"] = _text(payload.get("Description") or self.prompt)
        payload["Comment"] = comment
        payload["_local"] = {
            "parser_version": self.parser_version,
            "metadata_source": self.metadata_source,
            "width": self.width,
            "height": self.height,
            "parsed_nai_tags": [tag.to_dict() for tag in parse_nai_tags(self.prompt)],
        }
        return payload


def _text(value: Any, *, limit: int = MAX_TEXT_VALUE) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        try:
            value = value.decode("utf-8", errors="replace")
        except Exception:
            return ""
    if isinstance(value, (dict, list)):
        try:
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        except Exception:
            return ""
    return str(value).strip()[:limit]


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raw = _text(value)
    if not raw or len(raw) > MAX_TEXT_VALUE:
        return {}
    try:
        parsed = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _embedded_metadata(image: Image.Image) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in dict(image.info or {}).items():
        if str(key).lower() in {"icc_profile", "exif"} and isinstance(value, bytes):
            continue
        text = _text(value)
        if text:
            out[str(key)] = text
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            exif = image.getexif()
            exif_items = list(exif.items()) if exif else []
    except Exception:
        exif_items = []
    for tag_id, value in exif_items:
        key = str(ExifTags.TAGS.get(tag_id, tag_id))
        text = _text(value)
        if text and key not in out:
            out[key] = text
    return out


def _ci_get(metadata: dict[str, Any], *keys: str) -> Any:
    wanted = {key.lower() for key in keys}
    for key, value in metadata.items():
        if str(key).lower() in wanted:
            return value
    return None


def _has_comfy_metadata(metadata: dict[str, Any]) -> bool:
    lowered = {str(key).lower(): value for key, value in metadata.items()}
    software = _text(lowered.get("software")).lower()
    if "comfy" in software:
        return True
    for key in _COMFY_KEYS:
        if key not in lowered:
            continue
        raw = _text(lowered[key]).lower()
        if any(marker in raw for marker in _COMFY_MARKERS):
            return True
        parsed = _json_dict(lowered[key])
        if key == "workflow" and (
            isinstance(parsed.get("nodes"), list)
            or "last_node_id" in parsed
        ):
            return True
        if key == "prompt" and any(
            isinstance(value, dict) and "class_type" in value
            for value in parsed.values()
        ):
            return True
    return False


def _is_novelai(metadata: dict[str, Any]) -> bool:
    software = _text(_ci_get(metadata, "Software")).lower()
    source = _text(_ci_get(metadata, "Source", "Model")).lower()
    return "novelai" in software or "novelai diffusion" in source


def _comment(metadata: dict[str, Any]) -> dict[str, Any]:
    return _json_dict(_ci_get(metadata, "Comment", "Parameters"))


def _v4_prompt(comment: dict[str, Any]) -> str:
    v4 = comment.get("v4_prompt")
    if not isinstance(v4, dict):
        return ""
    caption = v4.get("caption")
    if not isinstance(caption, dict):
        return ""
    parts: list[str] = []
    base = _text(caption.get("base_caption"))
    if base:
        parts.append(base)
    chars = caption.get("char_captions")
    if isinstance(chars, list):
        for item in chars:
            if not isinstance(item, dict):
                continue
            char = _text(item.get("char_caption") or item.get("caption"))
            if char:
                parts.append(char)
    return ", ".join(dict.fromkeys(parts))


def _normalize_metadata(
    metadata: dict[str, Any],
    *,
    width: int,
    height: int,
    source: str,
) -> NAIParseResult:
    if _has_comfy_metadata(metadata):
        return NAIParseResult(
            False,
            "comfy_metadata",
            metadata_source=source,
            width=width,
            height=height,
        )
    if not _is_novelai(metadata):
        return NAIParseResult(
            False,
            "nai_metadata_missing",
            metadata_source=source,
            width=width,
            height=height,
        )

    comment = _comment(metadata)
    prompt = (
        _text(_ci_get(metadata, "Description"))
        or _text(comment.get("prompt"))
        or _v4_prompt(comment)
    )
    if not prompt:
        return NAIParseResult(
            False,
            "nai_prompt_missing",
            metadata_source=source,
            width=width,
            height=height,
        )
    negative = _text(
        comment.get("uc")
        or comment.get("negative_prompt")
        or comment.get("negative")
    )
    model = _text(
        _ci_get(metadata, "Source", "Model")
        or comment.get("model")
        or comment.get("model_name")
    )
    seed_raw = comment.get("seed")
    try:
        seed = int(seed_raw) if seed_raw is not None else None
    except (TypeError, ValueError):
        seed = None

    safe_metadata = {
        "Software": _text(_ci_get(metadata, "Software"), limit=512),
        "Source": _text(_ci_get(metadata, "Source"), limit=1024),
        "Description": prompt,
        "Comment": comment,
    }
    return NAIParseResult(
        True,
        "accepted",
        metadata_source=source,
        prompt=prompt,
        negative_prompt=negative,
        model=model,
        seed=seed,
        width=width,
        height=height,
        metadata=safe_metadata,
    )


def parse_nai_image(path: Path) -> NAIParseResult:
    """Accept only locally parseable images with explicit NovelAI provenance."""

    path = Path(path)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", UserWarning)
            with Image.open(path) as image:
                width, height = image.size
                if width <= 0 or height <= 0 or width * height > MAX_IMAGE_PIXELS:
                    return NAIParseResult(
                        False,
                        "image_too_large",
                        width=width,
                        height=height,
                    )
                embedded = _embedded_metadata(image)
                if _has_comfy_metadata(embedded):
                    return NAIParseResult(
                        False,
                        "comfy_metadata",
                        metadata_source="embedded",
                        width=width,
                        height=height,
                    )
                direct = _normalize_metadata(
                    embedded,
                    width=width,
                    height=height,
                    source="embedded",
                )
                if direct.accepted or path.suffix.lower() != ".png":
                    return direct
                try:
                    stealth = extract_image_metadata(image)
                except Exception:
                    stealth = {}
                if isinstance(stealth, dict) and stealth:
                    return _normalize_metadata(
                        stealth,
                        width=width,
                        height=height,
                        source="stealth_pngcomp",
                    )
                return direct
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return NAIParseResult(False, "unreadable_image")
    except Exception:
        return NAIParseResult(False, "metadata_parse_error")
