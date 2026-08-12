"""Keep credentials and browser session secrets out of model/checkpoint state."""

from __future__ import annotations

import re
from typing import Any


_SECRET_PATTERNS = (
    re.compile(r"(?i)\b(bearer)\s+[A-Za-z0-9._~+/=-]{12,}"),
    re.compile(r"\bsk-[A-Za-z0-9_-]{12,}\b"),
    re.compile(r"\bpst-[A-Za-z0-9_-]{20,}\b", re.IGNORECASE),
    re.compile(
        r"\b(?=[A-Za-z0-9_-]{32,}\b)"
        r"(?=[A-Za-z0-9_-]*[A-Z])"
        r"(?=[A-Za-z0-9_-]*[a-z])"
        r"(?=[A-Za-z0-9_-]*\d)"
        r"[A-Za-z0-9_-]{32,}\b"
    ),
    re.compile(
        r"(?i)\b(api[_ -]?key|access[_ -]?token|refresh[_ -]?token|token|password|passwd|"
        r"username|account|cookie|authorization)\b\s*[:：=]\s*[^\s,，;；。]{3,}"
    ),
    re.compile(
        r"(访问令牌|刷新令牌|账号|帐号|用户名|密码|口令|令牌|密钥)"
        r"\s*(?:[:：=]|是|为)?\s*[A-Za-z0-9@._+\-]{5,}"
    ),
)


def redact_text(value: Any, *, limit: int = 4000) -> str:
    text = str(value or "").replace("\x00", " ").strip()[:limit]
    for pattern in _SECRET_PATTERNS:
        text = pattern.sub(lambda match: f"{match.group(1) if match.lastindex else 'secret'} [REDACTED]", text)
    return text


def redact_history(value: Any, *, maximum: int = 12) -> list[dict[str, str]]:
    if not isinstance(value, list):
        return []
    history: list[dict[str, str]] = []
    for item in value[-maximum:]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        if role not in {"user", "assistant"}:
            continue
        content = redact_text(item.get("content"), limit=2000)
        if content:
            history.append({"role": role, "content": content})
    return history


def redact_value(value: Any) -> Any:
    """Recursively redact persisted JSON-like values without changing their shape."""

    if isinstance(value, dict):
        return {str(key): redact_value(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, str):
        return redact_text(value, limit=max(4000, len(value)))
    return value
