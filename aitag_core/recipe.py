"""Small domain records for private character assets and remix recipes."""

from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass, field
from typing import Any, Mapping

from .external import AitagImage, AitagWork, AitagWorkDetail, to_reference_record
from .recognition import analyze_slot_caption
from nai_char_modules.snapshots import effective_comment


def _texts(value: Any, *, limit: int = 200) -> tuple[str, ...]:
    if value is None:
        return ()
    values = value.replace("\n", ",").split(",") if isinstance(value, str) else value
    if not isinstance(values, (list, tuple, set)):
        values = [values]
    result: list[str] = []
    seen: set[str] = set()
    for item in values:
        text = str(item or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        result.append(text[:500])
        if len(result) >= limit:
            break
    return tuple(result)


_CHARACTER_CATEGORIES = frozenset(
    {"identity", "gender", "body", "appearance", "creature"}
)


def _prompt_without_character(prompt: str, character: "CharacterAsset | None") -> str:
    """Return the reusable scene/style portion of a flat source prompt."""

    source_keys = {
        str(value or "").strip().casefold().replace("_", " ")
        for value in (
            *(character.identity_tags if character else ()),
            *(character.appearance_tags if character else ()),
            character.trigger if character else "",
        )
        if str(value or "").strip()
    }
    analysis = analyze_slot_caption(prompt)
    kept = [
        item.token.raw
        for item in analysis.tokens
        if item.category not in _CHARACTER_CATEGORIES
        and item.token.normalized not in source_keys
    ]
    return ", ".join(value for value in kept if value).strip(" ,")


@dataclass(frozen=True)
class CharacterAsset:
    """A user-selectable identity, independent from one generated image."""

    asset_id: str
    label: str
    identity_tags: tuple[str, ...] = ()
    appearance_tags: tuple[str, ...] = ()
    trigger: str = ""
    source: str = "local"
    source_ref: str = ""
    reference_images: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self) | {
            "identity_tags": list(self.identity_tags),
            "appearance_tags": list(self.appearance_tags),
            "reference_images": list(self.reference_images),
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_reference_record(cls, record: Mapping[str, Any]) -> "CharacterAsset":
        raw = dict(record)
        identity = _texts(raw.get("identity_tags") or raw.get("identity"))
        appearance = _texts(raw.get("appearance_tags") or raw.get("appearance"))
        core = _texts(raw.get("core_tags") or raw.get("tags"))
        if not identity:
            identity = core
        label = str(raw.get("label") or raw.get("name") or raw.get("character") or "").strip()
        source = str(raw.get("source") or "local").strip() or "local"
        source_ref = str(raw.get("source_id") or raw.get("id") or "").strip()
        asset_id = str(raw.get("asset_id") or raw.get("reference_id") or "").strip()
        if not asset_id:
            digest = hashlib.sha256(f"{source}\0{source_ref}\0{label}".encode("utf-8")).hexdigest()
            asset_id = f"asset_{digest[:24]}"
        images = _texts(
            raw.get("reference_images")
            or [raw.get("image_url"), raw.get("thumb_url")]
        )
        return cls(
            asset_id=asset_id,
            label=label or asset_id,
            identity_tags=identity,
            appearance_tags=appearance,
            trigger=str(raw.get("trigger") or raw.get("trigger_tag") or "").strip(),
            source=source,
            source_ref=source_ref,
            reference_images=images,
            metadata=dict(raw.get("metadata") or raw.get("provenance") or {}),
        )


@dataclass(frozen=True)
class CharacterCandidate:
    """One replaceable character slot discovered in one remote image."""

    candidate_id: str
    image_index: int
    slot_index: int
    caption: str
    role: str
    character: CharacterAsset

    def to_reference_record(self) -> dict[str, Any]:
        record = self.character.to_dict()
        record.update(
            {
                "id": self.character.source_ref or self.candidate_id,
                "source": self.character.source,
                "source_id": self.character.source_ref or self.candidate_id,
                "name": self.character.label,
                "character": self.character.label,
                "trigger": self.character.trigger,
                "core_tags": list(
                    self.character.identity_tags + self.character.appearance_tags
                ),
                "candidate_id": self.candidate_id,
            }
        )
        return record

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "image_index": self.image_index,
            "slot_index": self.slot_index,
            "caption": self.caption,
            "role": self.role,
            "label": self.character.label,
            "identity_tags": list(self.character.identity_tags),
            "appearance_tags": list(self.character.appearance_tags),
            "reference_images": list(self.character.reference_images),
            "source_ref": self.character.source_ref,
            "asset": self.character.to_dict(),
        }
@dataclass(frozen=True)
class RemixRecipe:
    """A reproducible generation intent that can swap its character asset."""

    recipe_id: str
    prompt: str = ""
    negative_prompt: str = ""
    model: str = ""
    character: CharacterAsset | None = None
    scene_tags: tuple[str, ...] = ()
    style_tags: tuple[str, ...] = ()
    source_ref: str = ""
    provenance: Mapping[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["scene_tags"] = list(self.scene_tags)
        data["style_tags"] = list(self.style_tags)
        data["character"] = self.character.to_dict() if self.character else None
        data["provenance"] = dict(self.provenance)
        return data

    def with_character(
        self,
        character: CharacterAsset,
        *,
        prompt: str | None = None,
    ) -> "RemixRecipe":
        """Swap identity without carrying the source character into the recipe.

        ``prompt`` may be supplied by a compiler that already owns a normalized
        base-caption.  Direct domain callers get a conservative character-free
        prompt assembled from the original prompt plus explicit scene/style
        cards.
        """

        scene_prompt = (
            str(prompt).strip()
            if prompt is not None
            else _prompt_without_character(self.prompt, self.character)
        )
        prompt_parts = _texts(
            [
                *(_texts(scene_prompt)),
                *self.scene_tags,
                *self.style_tags,
            ]
        )

        return RemixRecipe(
            recipe_id=self.recipe_id,
            prompt=", ".join(prompt_parts),
            negative_prompt=self.negative_prompt,
            model=self.model,
            character=character,
            scene_tags=self.scene_tags,
            style_tags=self.style_tags,
            source_ref=self.source_ref,
            provenance=self.provenance,
        )

    def to_reference_record(self) -> dict[str, Any]:
        """Convert a recipe asset into the existing explicit-import contract."""

        if self.character is None:
            raise ValueError("recipe has no character asset")
        record = self.character.to_dict()
        record.update(
            {
                "id": self.character.source_ref or self.character.asset_id,
                "source": self.character.source,
                "source_id": self.character.source_ref or self.character.asset_id,
                "name": self.character.label,
                "character": self.character.label,
                "trigger": self.character.trigger,
                "core_tags": list(self.character.identity_tags + self.character.appearance_tags),
                "prompt": self.prompt,
                "negative_prompt": self.negative_prompt,
                "recipe_id": self.recipe_id,
                "provenance": dict(self.provenance),
            }
        )
        return record

    @classmethod
    def from_aitag(cls, work: AitagWork, image: AitagImage | None = None) -> "RemixRecipe":
        record = to_reference_record(work, image)
        metadata = dict(record.get("metadata") or {})
        candidates = discover_character_candidates(
            AitagWorkDetail(work=work, images=(image,) if image is not None else work.images)
        )
        asset = candidates[0].character if candidates else CharacterAsset.from_reference_record(record)
        source_ref = str(record.get("source_id") or work.work_id)
        digest = hashlib.sha256(source_ref.encode("utf-8")).hexdigest()[:24]
        return cls(
            recipe_id=f"recipe_{digest}",
            prompt=str(record.get("prompt") or ""),
            negative_prompt=str(record.get("negative_prompt") or ""),
            model=str(
                (image.model if image is not None else "")
                or metadata.get("model")
                or metadata.get("model_dialect")
                or ""
            ),
            character=asset,
            scene_tags=_texts(metadata.get("scene_tags") or metadata.get("scene")),
            style_tags=_texts(metadata.get("style_tags") or metadata.get("styles")),
            source_ref=source_ref,
            provenance=dict(record.get("provenance") or {}),
        )


def _image_comment(image: AitagImage) -> dict[str, Any]:
    raw = image.ai_json
    if isinstance(raw, Mapping):
        return effective_comment(dict(raw))
    return {}


def discover_character_candidates(detail: AitagWorkDetail) -> tuple[CharacterCandidate, ...]:
    """Normalize all replaceable slots across every image in one work."""

    candidates: list[CharacterCandidate] = []
    for image_index, image in enumerate(detail.images):
        record = to_reference_record(detail.work, image)
        comment = _image_comment(image)
        v4_prompt = comment.get("v4_prompt") or {}
        caption = (v4_prompt.get("caption") or {}) if isinstance(v4_prompt, Mapping) else {}
        raw_slots = caption.get("char_captions") or []
        slot_captions = [
            (slot_index, str(item.get("char_caption") or "").strip())
            for slot_index, item in enumerate(raw_slots)
            if isinstance(item, Mapping) and str(item.get("char_caption") or "").strip()
        ]
        if not slot_captions:
            fallback = str(record.get("prompt") or image.prompt_text or "").strip()
            if fallback:
                slot_captions = [(0, fallback)]
        for slot_index, slot_caption in slot_captions[:6]:
            analysis = analyze_slot_caption(slot_caption)
            groups = analysis.token_groups
            named_identity = _texts(
                [*(groups.get("identity") or []), *(groups.get("creature") or [])]
            )
            identity = _texts(
                [
                    *(groups.get("gender") or []),
                    *(groups.get("identity") or []),
                    *(groups.get("creature") or []),
                ]
            )
            appearance = _texts(
                [
                    *(groups.get("body") or []),
                    *(groups.get("appearance") or []),
                ]
            )
            if not identity:
                identity = _texts(record.get("core_tags"))
            candidate_id = (
                f"{detail.work.work_id}/{image.image_id}/slot-{slot_index}"
            )
            label = analysis.identity_name or (named_identity[0] if named_identity else "") or detail.work.title or analysis.display_name
            role = analysis.role or ("creature" if groups.get("creature") else "")
            asset = CharacterAsset(
                asset_id=f"asset_{hashlib.sha256(candidate_id.encode('utf-8')).hexdigest()[:24]}",
                label=label,
                identity_tags=identity,
                appearance_tags=appearance,
                trigger=str(record.get("trigger") or ""),
                source="aitag-online",
                source_ref=candidate_id,
                reference_images=_texts([image.url, image.thumbnail_url]),
                metadata={
                    **dict(record.get("metadata") or {}),
                    "provider": "aitag-online",
                    "work_id": detail.work.work_id,
                    "image_id": image.image_id,
                    "image_index": image_index,
                    "slot_index": slot_index,
                },
            )
            candidates.append(
                CharacterCandidate(
                    candidate_id=candidate_id,
                    image_index=image_index,
                    slot_index=slot_index,
                    caption=slot_caption,
                    role=role,
                    character=asset,
                )
            )
    return tuple(candidates)


def select_character_candidate(
    candidates: tuple[CharacterCandidate, ...] | list[CharacterCandidate],
    *,
    candidate_id: str = "",
    image_index: int | None = None,
    slot_index: int | None = None,
) -> CharacterCandidate:
    """Select one exact candidate for route/service integration.

    A supplied ``candidate_id`` is authoritative and is never silently replaced
    by another slot.  Without an id, callers must provide ``image_index``; an
    omitted ``slot_index`` selects that image's first candidate.
    """

    normalized_id = str(candidate_id or "").strip()
    if normalized_id:
        for candidate in candidates:
            if candidate.candidate_id != normalized_id:
                continue
            if image_index is not None and candidate.image_index != image_index:
                break
            if slot_index is not None and candidate.slot_index != slot_index:
                break
            return candidate
        raise ValueError("AITag character candidate was not found")

    if image_index is None:
        raise ValueError("image_index is required when candidate_id is omitted")
    selected = [item for item in candidates if item.image_index == image_index]
    if slot_index is None and selected:
        return selected[0]
    for candidate in selected:
        if candidate.slot_index == slot_index:
            return candidate
    raise ValueError("AITag character candidate was not found")


__all__ = [
    "CharacterAsset",
    "CharacterCandidate",
    "RemixRecipe",
    "discover_character_candidates",
    "select_character_candidate",
]
