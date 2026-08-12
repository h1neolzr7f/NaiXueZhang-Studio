from __future__ import annotations

import re
from dataclasses import dataclass

from char_tag_db import repair_prompt_caption, split_prompt_tags, weighted_tag_inner


_WEIGHT_RE = re.compile(r"^(-?\d+(?:\.\d+)?)::")


@dataclass(frozen=True)
class PromptToken:
    raw: str
    normalized: str
    index: int
    inner: str = ""
    weight: float | None = None

    @property
    def text(self) -> str:
        return self.inner or self.raw

    @property
    def weighted(self) -> bool:
        return bool(self.inner)

    def to_dict(self) -> dict:
        return {
            "raw": self.raw,
            "normalized": self.normalized,
            "index": self.index,
            "inner": self.inner,
            "weight": self.weight,
            "weighted": self.weighted,
        }


def normalize_tag(tag: str) -> str:
    return str(tag or "").strip().lower().replace("_", " ")


def _weight(raw: str) -> float | None:
    m = _WEIGHT_RE.match(str(raw or "").strip())
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def tokenize_prompt(text: str) -> list[PromptToken]:
    repaired = repair_prompt_caption(text)
    tokens: list[PromptToken] = []
    for index, raw in enumerate(split_prompt_tags(repaired)):
        inner = weighted_tag_inner(raw)
        target = inner or raw
        tokens.append(
            PromptToken(
                raw=str(raw).strip(),
                normalized=normalize_tag(target),
                index=index,
                inner=inner,
                weight=_weight(raw),
            )
        )
    return tokens

