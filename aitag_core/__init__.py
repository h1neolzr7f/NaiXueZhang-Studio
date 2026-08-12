"""AITag recognition, online discovery and zero-generation Remix contracts."""

from .external import (
    AitagConfig,
    AitagImage,
    AitagSearchPage,
    AitagWork,
    AitagWorkDetail,
    aitag_image_url,
    aitag_work_is_nai,
    aitag_work_is_safe,
    qualify_aitag_work,
)
from .online import AitagClient, AitagClientError, AitagSearchRequest
from .recipe import (
    CharacterAsset,
    CharacterCandidate,
    RemixRecipe,
    discover_character_candidates,
    select_character_candidate,
)
from .studio import AitagMetadataAdapter, compile_aitag_studio_draft, compile_aitag_studio_drafts

__all__ = [
    "AitagClient",
    "AitagClientError",
    "AitagConfig",
    "AitagImage",
    "AitagMetadataAdapter",
    "AitagSearchPage",
    "AitagSearchRequest",
    "AitagWork",
    "AitagWorkDetail",
    "CharacterAsset",
    "CharacterCandidate",
    "RemixRecipe",
    "aitag_image_url",
    "aitag_work_is_nai",
    "aitag_work_is_safe",
    "compile_aitag_studio_draft",
    "compile_aitag_studio_drafts",
    "discover_character_candidates",
    "select_character_candidate",
    "qualify_aitag_work",
]
