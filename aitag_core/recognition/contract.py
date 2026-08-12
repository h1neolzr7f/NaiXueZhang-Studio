from __future__ import annotations

from dataclasses import asdict, dataclass, field

from aitag_core.prompt import PromptToken


@dataclass(frozen=True)
class TokenAnalysis:
    token: PromptToken
    category: str
    confidence: float
    reason: str
    display: str
    keep_on_replace: bool = False

    def to_dict(self) -> dict:
        data = asdict(self)
        data["token"] = self.token.to_dict()
        return data


@dataclass(frozen=True)
class OcMatch:
    matched: bool = False
    label: str = ""
    preview: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class SlotAnalysis:
    caption: str
    role: str
    identity_name: str | None
    display_name: str
    replaceable: bool
    tokens: tuple[TokenAnalysis, ...] = field(default_factory=tuple)
    token_groups: dict[str, list[str]] = field(default_factory=dict)
    oc: OcMatch = field(default_factory=OcMatch)

    def to_dict(self) -> dict:
        return {
            "caption": self.caption,
            "role": self.role,
            "identity_name": self.identity_name,
            "display_name": self.display_name,
            "replaceable": self.replaceable,
            "tokens": [t.to_dict() for t in self.tokens],
            "token_groups": {k: list(v) for k, v in self.token_groups.items()},
            "oc": self.oc.to_dict(),
        }

