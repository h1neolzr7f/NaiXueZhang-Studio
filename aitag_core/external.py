"""Typed, read-only boundary for optional external AITag discovery.

The project keeps the external source behind a normalizer.  This module does
not choose a host, mirror images, or make network calls; callers provide a
decoded JSON payload and can explicitly turn a normalized item into a local
reference/recipe record.  That keeps AITag an optional discovery source rather
than a second canonical gallery database.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from typing import Any, Mapping
from urllib.parse import urlparse

from .qualification import (
    aitag_work_is_nai,
    aitag_work_is_safe,
    qualify_aitag_work,
)


def _mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _first_text(values: list[Any], *, limit: int = 2_000) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text[:limit]
    return ""


def _https_url(values: list[Any], *, limit: int = 2_000) -> str:
    """Return the first display URL that is unambiguously HTTPS."""

    for value in values:
        text = str(value or "").strip()[:limit]
        if not text:
            continue
        parsed = urlparse(text)
        if (
            parsed.scheme.casefold() == "https"
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        ):
            return text
    return ""


def _prompt_text(value: Any, *, limit: int = 8_000) -> str:
    if isinstance(value, Mapping):
        return _first_text(
            [
                value.get("base_caption"),
                value.get("caption"),
                value.get("prompt"),
                value.get("text"),
                value.get("Description"),
            ],
            limit=limit,
        )
    return _first_text([value], limit=limit)


def _first_prompt(values: list[Any], *, limit: int = 8_000) -> str:
    for value in values:
        text = _prompt_text(value, limit=limit)
        if text:
            return text
    return ""


def _list_text(value: Any, *, limit: int = 200) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = value.replace("\n", ",").replace("|", ",").split(",")
    elif isinstance(value, (list, tuple, set)):
        values = value
    else:
        values = [value]
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text[:500])
        if len(result) >= limit:
            break
    return tuple(result)


def _int(value: Any) -> int | None:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def _number(value: Any) -> int:
    parsed = _int(value)
    return parsed or 0


def parse_aitag_json(value: Any) -> Any:
    """Parse JSON columns that AITag sometimes returns as strings."""

    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return ""
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return value


def _stable_id(value: Mapping[str, Any], fallback: str) -> str:
    for key in ("id", "work_id", "image_id", "uuid", "slug"):
        text = str(value.get(key) or "").strip()
        if text:
            return text[:300]
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return f"{fallback}_{hashlib.sha256(encoded.encode('utf-8')).hexdigest()[:16]}"


@dataclass(frozen=True)
class AitagImage:
    """One external image reference; the bytes remain outside the catalog."""

    image_id: str
    url: str = ""
    thumbnail_url: str = ""
    width: int | None = None
    height: int | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    work_id: str = ""
    author_id: str = ""
    image_type: str = ""
    model: str = ""
    file_name: str = ""
    ai_json: Any = None
    prompt_text: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {"metadata": dict(self.metadata)}


@dataclass(frozen=True)
class AitagWork:
    """Normalized external work with zero local persistence side effects."""

    work_id: str
    title: str = ""
    creator: str = ""
    images: tuple[AitagImage, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)
    user_id: str = ""
    caption: str = ""
    tags: tuple[str, ...] = ()
    create_date: str = ""
    ai_type: str = ""
    total_view: int = 0
    total_bookmarks: int = 0
    image_count: int = 0
    qualification: str = "review"
    qualification_reasons: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_id": self.work_id,
            "title": self.title,
            "creator": self.creator,
            "images": [image.to_dict() for image in self.images],
            "metadata": dict(self.metadata),
            "user_id": self.user_id,
            "caption": self.caption,
            "tags": list(self.tags),
            "create_date": self.create_date,
            "ai_type": self.ai_type,
            "total_view": self.total_view,
            "total_bookmarks": self.total_bookmarks,
            "image_count": self.image_count,
            "qualification": self.qualification,
            "qualification_reasons": list(self.qualification_reasons),
        }


@dataclass(frozen=True)
class AitagConfig:
    asset_base_url: str = "https://ai-img.10118899.xyz/"
    available_years: tuple[int, ...] = ()
    available_months: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "asset_base_url": self.asset_base_url,
            "available_years": list(self.available_years),
            "available_months": list(self.available_months),
        }


@dataclass(frozen=True)
class AitagWorkDetail:
    work: AitagWork
    images: tuple[AitagImage, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "work": self.work.to_dict(),
            "images": [image.to_dict() for image in self.images],
        }


@dataclass(frozen=True)
class AitagSearchPage:
    query: str
    page: int
    page_size: int
    total: int | None
    has_more: bool
    works: tuple[AitagWork, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "page": self.page,
            "page_size": self.page_size,
            "total": self.total,
            "has_more": self.has_more,
            "works": [work.to_dict() for work in self.works],
        }


def normalize_aitag_image(payload: Mapping[str, Any] | Any) -> AitagImage:
    raw = _mapping(payload)
    url = _https_url(
        [raw.get("url"), raw.get("image_url"), raw.get("full_url"), raw.get("src")]
    )
    thumbnail_url = _https_url(
        [
            raw.get("thumbnail_url"),
            raw.get("thumb_url"),
            raw.get("thumbnail"),
            raw.get("preview_url"),
            raw.get("preview"),
            url,
        ]
    )
    image_id = _stable_id(raw, "image")
    ai_json = parse_aitag_json(raw.get("ai_json", raw.get("aiJson")))
    return AitagImage(
        image_id=image_id,
        url=url,
        thumbnail_url=thumbnail_url,
        width=_int(raw.get("width")),
        height=_int(raw.get("height")),
        metadata=dict(raw),
        work_id=str(raw.get("work_id") or raw.get("workId") or "").strip(),
        author_id=str(raw.get("author_id") or raw.get("authorId") or raw.get("userid") or "").strip(),
        image_type=str(raw.get("image_type") or raw.get("imageType") or "").strip(),
        model=str(raw.get("model") or "").strip(),
        file_name=str(raw.get("file_name") or raw.get("fileName") or "").strip(),
        ai_json=ai_json,
        prompt_text=str(raw.get("prompt_text") or raw.get("promptText") or "").strip(),
    )


def _image_payloads(raw: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    value = raw.get("images") or raw.get("pages") or raw.get("artworks")
    if value is None and any(key in raw for key in ("image_url", "url", "src")):
        value = [raw]
    if isinstance(value, Mapping):
        value = list(value.values())
    if not isinstance(value, (list, tuple)):
        return []
    return [_mapping(item) for item in value if isinstance(item, Mapping)]


def normalize_aitag_work(payload: Mapping[str, Any] | Any) -> AitagWork:
    source = _mapping(payload)
    raw = source
    for key in ("work", "item", "result", "data"):
        nested = source.get(key)
        if isinstance(nested, Mapping):
            raw = nested
            break
    image_source = source if isinstance(source.get("images"), (list, tuple)) else raw
    work_id = _stable_id(raw, "work")
    normalized_images = tuple(
        normalize_aitag_image(item) for item in _image_payloads(image_source)
    )
    images = tuple(
        replace(image, work_id=image.work_id or work_id)
        for image in normalized_images
    )
    title = _first_text([raw.get("title"), raw.get("name"), raw.get("label")], limit=500)
    creator_value = raw.get("creator") or raw.get("author") or raw.get("user")
    creator = _first_text(
        [
            creator_value.get("name") if isinstance(creator_value, Mapping) else creator_value,
            raw.get("username"),
            raw.get("userName"),
        ],
        limit=300,
    )
    original_urls = parse_aitag_json(raw.get("original_urls", raw.get("originalUrls")))
    inferred_image_count = len(original_urls) if isinstance(original_urls, list) else 0
    work = AitagWork(
        work_id=work_id,
        title=title,
        creator=creator,
        images=images,
        metadata=dict(raw),
        user_id=_first_text([raw.get("userId"), raw.get("userid"), raw.get("author_id")], limit=300),
        caption=str(raw.get("caption") or "").strip(),
        tags=_list_text(parse_aitag_json(raw.get("tags"))),
        create_date=_first_text([raw.get("create_date"), raw.get("createDate")], limit=80),
        ai_type=_first_text([raw.get("AI_type"), raw.get("ai_type"), raw.get("aiType")], limit=80),
        total_view=_number(raw.get("total_view", raw.get("totalView"))),
        total_bookmarks=_number(raw.get("total_bookmarks", raw.get("totalBookmarks"))),
        image_count=_number(raw.get("image_count", raw.get("imageCount"))) or len(images) or inferred_image_count,
    )
    qualification, reasons = qualify_aitag_work(work)
    return replace(work, qualification=qualification, qualification_reasons=reasons)


def normalize_aitag_search(
    payload: Mapping[str, Any] | list[Any] | Any,
    *,
    query: str = "",
    page: int = 1,
    page_size: int = 60,
) -> AitagSearchPage:
    root: Mapping[str, Any]
    if isinstance(payload, Mapping):
        nested = payload.get("data")
        root = _mapping(nested) if isinstance(nested, Mapping) else payload
        values = (
            root.get("works")
            or root.get("items")
            or root.get("results")
            or (nested if isinstance(nested, list) else None)
        )
    elif isinstance(payload, list):
        root = {}
        values = payload
    else:
        root = {}
        values = []
    if isinstance(values, Mapping):
        values = list(values.values())
    works = tuple(
        normalize_aitag_work(item)
        for item in (values if isinstance(values, (list, tuple)) else [])
        if isinstance(item, Mapping)
    )
    total = _int(root.get("total") or root.get("count"))
    current_page = max(1, _number(root.get("page")) or int(page or 1))
    safe_size = max(1, min(_number(root.get("page_size", root.get("pageSize"))) or int(page_size or 60), 200))
    explicit_more = root.get("has_more")
    has_more = bool(explicit_more) if explicit_more is not None else bool(
        total is not None and current_page * safe_size < total
    )
    return AitagSearchPage(
        query=str(query or "").strip(),
        page=current_page,
        page_size=safe_size,
        total=total,
        has_more=has_more,
        works=works,
    )


def normalize_aitag_config(payload: Mapping[str, Any] | Any) -> AitagConfig:
    raw = _mapping(payload)
    if isinstance(raw.get("data"), Mapping):
        raw = _mapping(raw.get("data"))
    base = _https_url([raw.get("asset_base_url"), raw.get("assetBaseUrl")], limit=2_000)
    years = tuple(
        year
        for value in (raw.get("available_years") or raw.get("availableYears") or [])
        if (year := _number(value)) >= 2000 and year <= 2200
    )
    months = tuple(
        month
        for month in _list_text(raw.get("available_months") or raw.get("availableMonths"), limit=240)
        if len(month) == 7 and month[4] == "-" and month[:4].isdigit() and month[5:].isdigit()
    )
    return AitagConfig(
        asset_base_url=base.rstrip("/") + "/" if base else AitagConfig.asset_base_url,
        available_years=years,
        available_months=months,
    )


def normalize_aitag_detail(payload: Mapping[str, Any] | Any) -> AitagWorkDetail:
    raw = _mapping(payload)
    if isinstance(raw.get("data"), Mapping):
        raw = _mapping(raw.get("data"))
    work_value = raw.get("work") if isinstance(raw.get("work"), Mapping) else raw
    work = normalize_aitag_work(work_value)
    image_values = raw.get("images") or raw.get("artworks") or raw.get("pages") or []
    if isinstance(image_values, Mapping):
        image_values = list(image_values.values())
    images = tuple(normalize_aitag_image(item) for item in image_values if isinstance(item, Mapping))
    if not images:
        images = work.images
    images = tuple(
        replace(image, work_id=image.work_id or work.work_id)
        for image in images
    )
    if images:
        work = replace(work, images=images, image_count=len(images))
    qualification, reasons = qualify_aitag_work(work)
    work = replace(work, qualification=qualification, qualification_reasons=reasons)
    return AitagWorkDetail(work=work, images=images)


def aitag_image_url(config: AitagConfig, image: AitagImage) -> str:
    """Build the public image URL from AITag's metadata path components."""

    if image.url:
        return _https_url([image.url])
    if not image.author_id or not image.image_type or not image.file_name:
        return ""
    safe_base = _https_url([config.asset_base_url])
    if not safe_base:
        return ""
    base = safe_base.rstrip("/")
    from urllib.parse import quote

    path = "/".join(
        quote(part, safe="")
        for part in (image.image_type, image.author_id, f"{image.file_name}.webp")
    )
    return f"{base}/{path}"


def format_aitag_metadata(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, indent=2, default=str)
    except (TypeError, ValueError):
        return str(value or "")


def aitag_metadata_record(image: AitagImage, ai_type: str = "") -> dict[str, str]:
    """Flatten one AITag image into fields accepted by the local inspector."""

    parsed = image.ai_json
    source = parsed if isinstance(parsed, Mapping) else {}
    result = {
        str(key): format_aitag_metadata(value)
        for key, value in source.items()
    }
    if isinstance(parsed, str) and parsed.strip():
        result["parameters"] = parsed
    if isinstance(source.get("parameters"), str):
        result["parameters"] = str(source["parameters"])
    if isinstance(source.get("prompt"), Mapping):
        result["prompt"] = format_aitag_metadata(source["prompt"])
    if isinstance(source.get("workflow"), Mapping):
        result["workflow"] = format_aitag_metadata(source["workflow"])
    if ("novel" in str(ai_type).lower() or "nai" in str(ai_type).lower() or "novel" in image.model.lower()) and not any(
        result.get(key) for key in ("parameters", "prompt", "workflow")
    ):
        prompt = image.prompt_text or str(source.get("prompt") or "")
        result.setdefault("Description", prompt)
        result.setdefault("Comment", format_aitag_metadata(source))
        result.setdefault("Source", image.model)
        result.setdefault("Software", "NovelAI")
    if not result and image.prompt_text:
        result["Description"] = image.prompt_text
    return result


def strip_aitag_html(value: str) -> str:
    import re

    return re.sub(r"<[^>]+>", "", str(value or "").replace("<br />", "\n")).strip()


def to_reference_record(work: AitagWork, image: AitagImage | None = None) -> dict[str, Any]:
    """Build an explicit-import record for ``ReferenceCatalog``.

    The caller decides whether to persist this record.  The function does not
    download the image and keeps source/provenance fields so a later import can
    be audited or discarded without confusing it with a canonical gallery work.
    """

    metadata = dict(work.metadata)
    if image is not None:
        metadata = {**metadata, **dict(image.metadata)}
    parsed = parse_aitag_json(image.ai_json if image is not None else None)
    ai_fields = dict(parsed) if isinstance(parsed, Mapping) else {}
    comment = parse_aitag_json(ai_fields.get("Comment"))
    if not isinstance(comment, Mapping):
        comment = {}
    metadata = {**metadata, "aitag_ai_json": parsed} if parsed is not None else metadata
    tags = _list_text(
        [
            *work.tags,
            *(_list_text(metadata.get("character_tags"), limit=200)),
            *(_list_text(metadata.get("core_tags"), limit=200)),
            *(_list_text(metadata.get("tags"), limit=200)),
            *(_list_text(metadata.get("prompt_tags"), limit=200)),
        ]
    )
    prompt = _first_prompt(
        [
            image.prompt_text if image is not None else "",
            metadata.get("prompt"),
            metadata.get("positive_prompt"),
            ai_fields.get("prompt"),
            ai_fields.get("Description"),
            ai_fields.get("description"),
            comment.get("prompt"),
            comment.get("Description"),
            _mapping(ai_fields.get("v4_prompt")).get("caption"),
            metadata.get("caption"),
            work.caption,
        ],
        limit=8_000,
    )
    negative_prompt = _first_prompt(
        [
            metadata.get("negative_prompt"),
            metadata.get("negative"),
            metadata.get("uc"),
            ai_fields.get("negative_prompt"),
            ai_fields.get("negative"),
            ai_fields.get("uc"),
            comment.get("uc"),
            comment.get("negative_prompt"),
            _mapping(ai_fields.get("v4_negative_prompt")).get("caption"),
        ],
        limit=8_000,
    )
    tags = _list_text([*tags, *_list_text(prompt, limit=200)])
    image_id = image.image_id if image else ""
    source_id = f"{work.work_id}/{image_id}" if image_id else work.work_id
    image_url = image.url if image else ""
    thumb_url = image.thumbnail_url if image else ""
    provenance = {
        "provider": "aitag",
        "work_id": work.work_id,
        "image_id": image_id,
        "creator": work.creator,
        "external_url": f"https://aitag.win/i/{work.work_id}",
        "model": image.model if image is not None else "",
        "ai_type": work.ai_type,
        "image_url": image_url,
    }
    return {
        "id": source_id,
        "slug": source_id,
        "name": work.title or work.work_id,
        "character": work.title or work.work_id,
        "trigger": _first_text([metadata.get("trigger"), metadata.get("trigger_tag")], limit=500),
        "core_tags": list(tags),
        "prompt": prompt,
        "negative_prompt": negative_prompt,
        "thumb_url": thumb_url,
        "image_url": image_url,
        "source": "aitag",
        "source_id": source_id,
        "provenance": provenance,
        "metadata": metadata,
    }


__all__ = [
    "AitagImage",
    "AitagConfig",
    "AitagSearchPage",
    "AitagWork",
    "AitagWorkDetail",
    "aitag_image_url",
    "aitag_metadata_record",
    "format_aitag_metadata",
    "normalize_aitag_image",
    "normalize_aitag_config",
    "normalize_aitag_detail",
    "normalize_aitag_search",
    "normalize_aitag_work",
    "parse_aitag_json",
    "strip_aitag_html",
    "to_reference_record",
]
