"""Deterministic NovelAI prompt tag parsing with weight preservation."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass


_NUMERIC_WEIGHT = re.compile(r"^(-?\d+(?:\.\d+)?)::(.*)$", re.DOTALL)


@dataclass(frozen=True)
class NAIPromptTag:
    text: str
    weight: float
    raw_syntax: str
    syntax_type: str

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _split_prompt(prompt: str) -> list[str]:
    parts: list[str] = []
    buffer: list[str] = []
    brace_depth = 0
    bracket_depth = 0
    paren_depth = 0
    in_pipe = False
    index = 0
    while index < len(prompt):
        char = prompt[index]
        if char == "|" and index + 1 < len(prompt) and prompt[index + 1] == "|":
            in_pipe = not in_pipe
            buffer.extend(("|", "|"))
            index += 2
            continue
        if char == "{":
            brace_depth += 1
        elif char == "}":
            brace_depth = max(0, brace_depth - 1)
        elif char == "[":
            bracket_depth += 1
        elif char == "]":
            bracket_depth = max(0, bracket_depth - 1)
        elif char == "(":
            paren_depth += 1
        elif char == ")":
            paren_depth = max(0, paren_depth - 1)
        if (
            char == ","
            and brace_depth == 0
            and bracket_depth == 0
            and paren_depth == 0
            and not in_pipe
        ):
            part = "".join(buffer).strip()
            if part:
                parts.append(part)
            buffer.clear()
        else:
            buffer.append(char)
        index += 1
    part = "".join(buffer).strip()
    if part:
        parts.append(part)
    return parts


def _parse_tag(raw: str) -> NAIPromptTag | None:
    text = raw.strip()
    if not text:
        return None
    numeric = _NUMERIC_WEIGHT.match(text)
    if numeric:
        weighted_text = numeric.group(2)
        if weighted_text.endswith("::"):
            weighted_text = weighted_text[:-2]
        weighted_text = weighted_text.strip()
        if weighted_text:
            return NAIPromptTag(
                text=weighted_text,
                weight=float(numeric.group(1)),
                raw_syntax=raw,
                syntax_type="numeric",
            )
    if text.endswith("::"):
        text = text[:-2].strip()
    open_braces = len(text) - len(text.lstrip("{"))
    close_braces = len(text) - len(text.rstrip("}"))
    open_brackets = len(text) - len(text.lstrip("["))
    close_brackets = len(text) - len(text.rstrip("]"))
    brace_count = min(open_braces, close_braces)
    bracket_count = min(open_brackets, close_brackets)
    weight = 1.0
    syntax_type = "none"
    if brace_count:
        text = text[brace_count:-brace_count].strip()
        weight = 1.0 + brace_count * 0.05
        syntax_type = "bracket"
    elif bracket_count:
        text = text[bracket_count:-bracket_count].strip()
        weight = 1.0 - bracket_count * 0.05
        syntax_type = "bracket"
    if not text:
        return None
    return NAIPromptTag(text, round(weight, 10), raw, syntax_type)


def parse_nai_tags(prompt: str) -> tuple[NAIPromptTag, ...]:
    """Parse one NAI prompt without network or model inference."""

    return tuple(
        parsed
        for raw in _split_prompt(str(prompt or ""))
        if (parsed := _parse_tag(raw)) is not None
    )
