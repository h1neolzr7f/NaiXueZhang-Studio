"""Gallery Work metadata source seam and concrete local adapters."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass
from typing import Callable, Protocol


class ImageMetadataSource(Protocol):
    """Load one image's normalized metadata for a Gallery Work Reference."""

    def load(self, work_id: int, page_index: int = 0) -> dict: ...


@dataclass(frozen=True)
class SiteMetadataAdapter:
    loader: Callable[[int, int], dict]

    def load(self, work_id: int, page_index: int = 0) -> dict:
        return copy.deepcopy(self.loader(work_id, page_index))


@dataclass(frozen=True)
class GalleryMetadataAdapter:
    gallery_id: str
    database_loader: Callable[[str], object]

    def load(self, work_id: int, page_index: int = 0) -> dict:
        database = self.database_loader(self.gallery_id)
        detail = database.get_work_detail(work_id) or {}
        images = [image for image in (detail.get("images") or []) if isinstance(image, dict)]
        if not images:
            raise ValueError(f"Work {work_id} has no image metadata")
        page = int(page_index or 0)
        image = None
        for candidate in images:
            try:
                pi = int(candidate.get("page_index"))
            except (TypeError, ValueError):
                continue
            if pi == page:
                image = candidate
                break
        if image is None:
            available = []
            for candidate in images:
                try:
                    available.append(int(candidate.get("page_index")))
                except (TypeError, ValueError):
                    pass
            raise ValueError(
                f"Work {work_id} has no page_index={page} "
                f"(available: {available or list(range(len(images)))})"
            )
        raw = image.get("ai_json")
        if not raw:
            raise ValueError("The selected image has no AI metadata")
        if isinstance(raw, dict):
            return copy.deepcopy(raw)
        parsed = json.loads(str(raw))
        if not isinstance(parsed, dict):
            raise ValueError("Image AI metadata must be an object")
        return parsed


class MetadataSourceRegistry:
    """Resolve the correct Adapter while preserving gallery identity."""

    def __init__(
        self,
        *,
        site: ImageMetadataSource,
        gallery_factory: Callable[[str], ImageMetadataSource],
        normalize_gallery_id: Callable[[str], str],
    ) -> None:
        self._site = site
        self._gallery_factory = gallery_factory
        self._normalize_gallery_id = normalize_gallery_id

    def source_for(self, gallery_id: str) -> ImageMetadataSource:
        normalized = self._normalize_gallery_id(gallery_id)
        return self._site if normalized == "site" else self._gallery_factory(normalized)

    def load(self, gallery_id: str, work_id: int, page_index: int = 0) -> dict:
        return self.source_for(gallery_id).load(work_id, page_index)
