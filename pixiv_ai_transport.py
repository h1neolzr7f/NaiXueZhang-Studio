"""AI 接口传输层：chat/vision 调用、模型列表、连接自检（从 pixiv_launch.py 拆出）。"""

from __future__ import annotations

import json
import os
import re
import time
from typing import Any

import httpx
from usage_ledger import record_usage
from pixiv_launch_config import (
    DATA_DIR,  # re-exported for tests/diagnostics patching
    _MODEL_TOKEN_RE,
    _read_ai_secret,
    load_config,
    normalize_ai_config,
)


def _extract_json_block(text: str) -> dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("AI 返回为空")
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", raw, re.I)
    if fence:
        raw = fence.group(1).strip()
    try:
        data = json.loads(raw)
        if isinstance(data, dict):
            return data
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        data = json.loads(raw[start : end + 1])
        if isinstance(data, dict):
            return data
    raise ValueError("AI 返回不是合法 JSON")


def _ai_env(cfg: dict[str, Any]) -> dict[str, Any]:
    ai_cfg = normalize_ai_config(cfg.get("ai") or {})
    # 只读一次 ai.local.json：_read_ai_secret 已负责解密与明文迁移。
    secret = _read_ai_secret()

    api_base = str(secret.get("api_base") or ai_cfg.get("api_base") or "").strip()
    raw_key = str(secret.get("api_key") or "").strip()
    provider = str(ai_cfg.get("provider") or "").strip().lower()
    env_key = str(os.environ.get("DEEPSEEK_API_KEY") or "").strip()
    if provider == "deepseek" and env_key:
        # 环境变量中的 DeepSeek key 优先于本地保存的 relay key。
        api_key = env_key
    elif raw_key.startswith("dpapi:"):
        # 解密失败时 _read_ai_secret 保留密文；绝不能把密文当 Bearer 发出去。
        raise ValueError("本地密钥解密失败，请重新保存 API Key")
    else:
        api_key = raw_key

    model = str(secret.get("model") or ai_cfg.get("model") or "").strip()

    return {
        "api_base": api_base,
        "api_key": api_key,
        "model": model,
        "timeout": int(ai_cfg.get("timeout") or 120),
        "max_tokens": int(ai_cfg.get("max_tokens") or 2048),
    }


def _chat_url(api_base: str) -> str:
    base = api_base.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    return f"{base}/chat/completions"


def _models_url(api_base: str) -> str:
    base = str(api_base or "https://api.openai.com/v1").rstrip("/")
    if base.endswith("/chat/completions"):
        return f"{base.removesuffix('/chat/completions')}/models"
    return f"{base}/models"


def list_ai_models() -> dict[str, Any]:
    """Return model identifiers without ever exposing the saved credential."""
    cfg = load_config()
    env = _ai_env(cfg)
    if not env.get("api_key"):
        raise ValueError("请先保存 AI API Key")
    url = _models_url(str(env.get("api_base") or ""))
    with httpx.Client(timeout=min(float(env.get("timeout") or 120), 45), trust_env=True) as client:
        response = client.get(
            url,
            headers={"Authorization": f"Bearer {env['api_key']}"},
        )
    if response.status_code >= 400:
        raise RuntimeError(f"模型列表接口 {response.status_code}: {response.text[:300]}")
    payload = response.json()
    rows = payload.get("data") if isinstance(payload, dict) else None
    models: list[str] = []
    for row in rows if isinstance(rows, list) else []:
        model = str(row.get("id") if isinstance(row, dict) else row or "").strip()
        if model and _MODEL_TOKEN_RE.match(model) and model not in models:
            models.append(model)
    models.sort(key=str.casefold)
    return {"ok": True, "models": models[:200], "count": min(len(models), 200)}


def _chat_completion(
    env: dict[str, Any],
    system: str,
    user_payload: dict[str, Any],
    *,
    image_data_url: str = "",
    image_data_urls: list[str] | None = None,
    image_detail: str = "auto",
    max_tokens_override: int | None = None,
    temperature_override: float | None = None,
    json_mode: bool = False,
) -> str:
    if not env.get("api_key") or not env.get("model"):
        raise ValueError("未配置 AI API Key 或模型")
    from network_safety import validate_ai_api_base

    raw_base = str(env.get("api_base") or "https://api.openai.com/v1").strip()
    try:
        safe_base = validate_ai_api_base(raw_base, allow_empty=False)
    except ValueError as exc:
        raise ValueError(f"AI api_base 不安全或未在白名单：{exc}") from exc
    url = _chat_url(safe_base)
    user_content: Any = json.dumps(user_payload, ensure_ascii=False)
    images = [str(url) for url in (image_data_urls or []) if str(url).strip()]
    if image_data_url:
        images.insert(0, image_data_url)
    if images:
        detail = image_detail if image_detail in {"low", "high", "auto"} else "auto"
        user_content = [{"type": "text", "text": user_content}]
        user_content.extend(
            {
                "type": "image_url",
                "image_url": {"url": url, "detail": detail},
            }
            for url in images[:6]
        )
    request_body: dict[str, Any] = {
        "model": env["model"],
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ],
        "temperature": (
            max(0.0, min(float(temperature_override), 2.0))
            if temperature_override is not None
            else 0.55
        ),
        "max_tokens": (
            max(64, min(int(max_tokens_override), 4096))
            if max_tokens_override is not None
            else int(env.get("max_tokens") or 2048)
        ),
    }
    model_name = str(env.get("model") or "").lower()
    if model_name.startswith("grok-4.5"):
        # Grok 4.5 defaults to high reasoning. Low is enough for bounded
        # classification/JSON tasks and materially reduces latency and tokens.
        request_body["reasoning_effort"] = "low"
        if json_mode:
            request_body["response_format"] = {"type": "json_object"}

    started = time.perf_counter()
    with httpx.Client(timeout=float(env.get("timeout") or 120), trust_env=True) as client:
        resp = client.post(
            url,
            headers={
                "Authorization": f"Bearer {env['api_key']}",
                "Content-Type": "application/json",
            },
            json=request_body,
        )
        if resp.status_code >= 400:
            raise RuntimeError(f"AI 接口 {resp.status_code}: {resp.text[:400]}")
        data = resp.json()
    usage = data.get("usage") if isinstance(data, dict) else {}
    usage = usage if isinstance(usage, dict) else {}
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    prompt_details = prompt_details if isinstance(prompt_details, dict) else {}
    def usage_int(value: Any) -> int:
        try:
            return max(0, int(value or 0))
        except (TypeError, ValueError):
            return 0

    record_usage(
        kind="llm",
        provider=str(env.get("provider") or "openai-compatible"),
        model=str(env.get("model") or ""),
        input_tokens=usage_int(usage.get("prompt_tokens") or usage.get("input_tokens")),
        output_tokens=usage_int(usage.get("completion_tokens") or usage.get("output_tokens")),
        cached_tokens=usage_int(prompt_details.get("cached_tokens")),
        cost_source="not_reported",
        duration_ms=round((time.perf_counter() - started) * 1000),
        metadata={"image_inputs": len(images), "json_mode": bool(json_mode)},
    )
    return _chat_response_text(data)


def _chat_response_text(data: Any) -> str:
    """Extract final assistant text from common OpenAI-compatible relay shapes.

    Some relays return content parts instead of a string, while an overloaded
    or interrupted relay can return an assistant message without ``content``.
    Convert the former and raise a retryable ValueError for the latter instead
    of leaking a bare KeyError into Butler task reports.
    """

    if not isinstance(data, dict):
        raise ValueError("AI 接口返回格式不正确")

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice, dict) else {}
        message = message if isinstance(message, dict) else {}
        content = message.get("content")
        if isinstance(content, str) and content.strip():
            return content
        if isinstance(content, list):
            parts = [
                str(part.get("text") or "")
                for part in content
                if isinstance(part, dict)
                and part.get("type") in {"text", "output_text"}
                and str(part.get("text") or "").strip()
            ]
            if parts:
                return "".join(parts)
        legacy_text = choice.get("text") if isinstance(choice, dict) else None
        if isinstance(legacy_text, str) and legacy_text.strip():
            return legacy_text

    output_text = data.get("output_text")
    if isinstance(output_text, str) and output_text.strip():
        return output_text
    raise ValueError("AI 接口没有返回可用文本，请稍后重试")


def chat_json(
    system: str,
    user_payload: dict[str, Any],
    *,
    image_data_url: str = "",
    image_data_urls: list[str] | None = None,
    image_detail: str = "auto",
    max_tokens: int | None = None,
    temperature: float | None = None,
) -> dict[str, Any]:
    """Call the configured OpenAI-compatible model and parse one JSON object.

    API credentials remain inside this module. Callers only receive the parsed
    response, which keeps browser-facing features from ever handling the key.
    """
    env = _ai_env(load_config())
    if not env.get("api_key"):
        raise ValueError("未配置 AI API Key")
    if not env.get("model"):
        raise ValueError("未配置 AI 模型")
    return _extract_json_block(
        _chat_completion(
            env,
            system,
            user_payload,
            image_data_url=image_data_url,
            image_data_urls=image_data_urls,
            image_detail=image_detail,
            max_tokens_override=max_tokens,
            temperature_override=temperature,
            json_mode=True,
        )
    )


def test_ai_connection() -> dict[str, Any]:
    cfg = load_config()
    env = _ai_env(cfg)
    if not env.get("api_key"):
        raise ValueError("未配置 AI API Key")
    if not env.get("model"):
        raise ValueError("未配置 AI 模型")
    text = _chat_completion(
        env,
        "Return compact JSON only.",
        {"task": "health_check", "reply": {"ok": True}},
    )
    parsed: dict[str, Any] | None = None
    try:
        parsed = _extract_json_block(text)
    except Exception:
        parsed = None
    return {
        "ok": True,
        "provider": (cfg.get("ai") or {}).get("provider", ""),
        "api_base": env.get("api_base", ""),
        "model": env.get("model", ""),
        "sample": parsed if parsed is not None else text[:200],
    }


def _vision_health_data_url() -> str:
    """Build a standards-compliant 32px PNG entirely in memory."""
    import base64
    import io

    from PIL import Image, ImageDraw

    image = Image.new("RGB", (32, 32), (226, 52, 68))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 21, 21), fill=(55, 96, 210))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def test_ai_vision_connection() -> dict[str, Any]:
    """Send four tiny in-memory PNGs to verify multi-image support."""
    cfg = load_config()
    env = _ai_env(cfg)
    if not env.get("api_key"):
        raise ValueError("未配置 AI API Key")
    if not env.get("model"):
        raise ValueError("未配置 AI 模型")
    health_images = [_vision_health_data_url() for _ in range(4)]
    text = _chat_completion(
        env,
        (
            "You are a vision transport health checker. Inspect the attached "
            "test PNG and return compact JSON only with keys "
            "vision_confirmed (boolean) and description (short string)."
        ),
        {"task": "vision_health_check", "instruction": "Describe the attached test image."},
        image_data_urls=health_images,
        image_detail="low",
        max_tokens_override=160,
        temperature_override=0.0,
        json_mode=True,
    )
    parsed: dict[str, Any] | None = None
    try:
        parsed = _extract_json_block(text)
    except Exception:
        parsed = None
    return {
        "ok": True,
        "provider": (cfg.get("ai") or {}).get("provider", ""),
        "api_base": env.get("api_base", ""),
        "model": env.get("model", ""),
        "vision_confirmed": bool((parsed or {}).get("vision_confirmed")),
        "sample": parsed if parsed is not None else text[:200],
    }
