"""Deep backend modules used by the legacy :mod:`nai_char` compatibility facade."""

from .generation import (
    MAX_FREE_LONG_EDGE,
    MAX_FREE_PIXELS,
    MAX_FREE_STEPS,
    build_generate_payload,
    fit_opus_free_size,
)
from .metadata import (
    GalleryMetadataAdapter,
    ImageMetadataSource,
    MetadataSourceRegistry,
    SiteMetadataAdapter,
)
from .snapshots import (
    comment_from_png,
    normalize_comment,
    parse_comment,
    prompt_snapshot_from_comment,
    prompt_snapshot_from_png,
)

__all__ = [
    "MAX_FREE_LONG_EDGE",
    "MAX_FREE_PIXELS",
    "MAX_FREE_STEPS",
    "GalleryMetadataAdapter",
    "ImageMetadataSource",
    "MetadataSourceRegistry",
    "SiteMetadataAdapter",
    "build_generate_payload",
    "comment_from_png",
    "fit_opus_free_size",
    "normalize_comment",
    "parse_comment",
    "prompt_snapshot_from_comment",
    "prompt_snapshot_from_png",
]
