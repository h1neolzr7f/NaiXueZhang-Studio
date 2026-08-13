"""Local NovelAI proxy: token pool, status, and image generation."""

from __future__ import annotations

import asyncio
import base64
import copy
import hashlib
import io
import json
import mimetypes
import random
import re
import shutil
import subprocess
import threading
import time
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from PIL import Image, UnidentifiedImageError

from generated_gallery import register_generated, _group_key
from local_secrets import (
    PREFIX as SECRET_PREFIX,
    SecretProtectionUnavailable,
    protect_secret,
    unprotect_secret,
)
from atomic_io import atomic_write_text
from nai_char import build_generate_payload, prompt_snapshot_from_comment
from nai_prompt_profiles import apply_prompt_profile_to_comment
from paths import data_dir
from usage_ledger import record_usage

DATA_DIR = data_dir()
TOKEN_PATH = DATA_DIR / "nai_token.local.json"
GENERATED_DIR = DATA_DIR / "generated"
API_BASE = "https://api.novelai.net"
IMAGE_API_BASE = "https://image.novelai.net"
XIANYUN_API_BASE = "https://api.idlecloud.cc/api"
PROVIDER_NOVELAI = "novelai"
PROVIDER_XIANYUN = "xianyun"
PROVIDER_UNKNOWN = "unknown"
DIRECTOR_RESPONSE_MAX_BYTES = 96 * 1024 * 1024
DIRECTOR_ZIP_MAX_ENTRIES = 16
DIRECTOR_OUTPUT_MAX_BYTES = 48 * 1024 * 1024
DIRECTOR_OUTPUT_MAX_PIXELS = 64_000_000


class GenerationProviderError(ValueError):
    """A provider response whose billing/retry outcome is known."""

    def __init__(
        self,
        message: str,
        *,
        retry_safe: bool,
        billing_uncertain: bool,
        wait: float = 0.0,
        request_attempted: bool | None = None,
        error_code: str = "",
    ) -> None:
        super().__init__(message)
        self.retry_safe = bool(retry_safe)
        self.billing_uncertain = bool(billing_uncertain)
        self.wait = max(0.0, float(wait or 0.0))
        if request_attempted is None:
            self.request_attempted = bool(billing_uncertain)
        else:
            self.request_attempted = bool(request_attempted)
        self.error_code = str(error_code or "")

_TOKEN_LOCKS: dict[str, asyncio.Lock] = {}
_LAST_GEN_AT_BY_TOKEN: dict[str, float] = {}
# Slots currently inside generate (prevents wait_for_slot=False from blocking).
_ACTIVE_GEN_SLOTS: set[str] = set()
_ACTIVE_GEN_SLOTS_GUARD = threading.Lock()
_TOKEN_CURSOR = 0
_TOKEN_STATE_LOCK = threading.Lock()
_FILENAME_LOCK = threading.Lock()
_RESERVED_FILENAMES: set[str] = set()
_COOLDOWN_SEC = 3.0
_XIANYUN_COOLDOWN_SEC = 20.0
_TOKEN_ENTRIES_CACHE: list[dict[str, Any]] | None = None
_TOKEN_ENTRIES_CACHE_AT: float = 0.0
_TOKEN_ENTRIES_CACHE_TTL = 5.0
_XIANYUN_POLL_INTERVAL_SEC = 2.0
_XIANYUN_TIMEOUT_SEC = 240.0
_TOKEN_FAILURE_TTL_SEC = 600.0
# 瞬时故障熔断：NAI 官方限流短暂，短熔断即可避过；闲云中转不稳，保持长熔断。
_NAI_TRANSIENT_TTL_SEC = 15.0
_TRANSIENT_PROVIDER_TTL_SEC = 45.0
_TOKEN_FAILURE_LIMIT = 2
_TOKEN_FAILURES: dict[str, dict[str, Any]] = {}
_TOKEN_VALIDATIONS: dict[str, dict[str, Any]] = {}
_ACTIVE_JOBS: dict[str, dict[str, Any]] = {}
_JOB: dict[str, Any] = {
    "status": "idle",
    "message": "",
    "active": [],
    "active_count": 0,
}


def _curl_config_quote(value: Any) -> str:
    text = str(value)
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _curl_request_for_token_check(
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    json_body: dict[str, Any] | None = None,
    timeout_sec: float = 10.0,
    proxy: str = "",
) -> tuple[int, str]:
    exe = shutil.which("curl.exe") or shutil.which("curl")
    if not exe:
        raise FileNotFoundError("curl executable not found")
    marker = "\n__AITAG_HTTP_STATUS__:"
    config = [
        f"url = {_curl_config_quote(url)}",
        f"request = {_curl_config_quote(method.upper())}",
    ]
    for key, value in headers.items():
        config.append(f"header = {_curl_config_quote(f'{key}: {value}')}")
    cmd = [
        exe,
        "--config",
        "-",
        "--silent",
        "--show-error",
        "--max-time",
        str(max(1.0, float(timeout_sec))),
        "--write-out",
        f"{marker}%{{http_code}}",
    ]
    if str(proxy or "").strip():
        cmd.extend(["--proxy", str(proxy).strip()])
    if json_body is not None:
        cmd.extend(["--data-raw", json.dumps(json_body, ensure_ascii=False)])
    completed = subprocess.run(
        cmd,
        input="\n".join(config) + "\n",
        text=True,
        capture_output=True,
        timeout=max(2.0, float(timeout_sec) + 2.0),
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        raise RuntimeError((completed.stderr or completed.stdout or "curl request failed").strip())
    output = completed.stdout or ""
    if marker not in output:
        raise RuntimeError("curl response missing HTTP status")
    body, status_text = output.rsplit(marker, 1)
    try:
        status = int(status_text.strip()[:3])
    except ValueError as exc:
        raise RuntimeError(f"curl response invalid HTTP status: {status_text[:20]}") from exc
    return status, body


def _token_check_request(
    method: str,
    url: str,
    headers: dict[str, str],
    *,
    json_body: dict[str, Any] | None = None,
    timeout_sec: float = 10.0,
    proxy: str = "",
) -> tuple[int, str]:
    try:
        return _curl_request_for_token_check(
            method,
            url,
            headers,
            json_body=json_body,
            timeout_sec=timeout_sec,
            proxy=proxy,
        )
    except FileNotFoundError:
        pass
    timeout = httpx.Timeout(timeout_sec, connect=min(4.0, timeout_sec))
    with httpx.Client(timeout=timeout, proxy=str(proxy or "").strip() or None) as client:
        resp = client.request(method, url, headers=headers, json=json_body)
    return resp.status_code, resp.text


def _read_token_file() -> dict[str, Any]:
    if not TOKEN_PATH.exists():
        return {}
    try:
        data = json.loads(TOKEN_PATH.read_text(encoding="utf-8-sig"))
        if not isinstance(data, dict):
            return {}
        migrated = False
        decoded = copy.deepcopy(data)
        if decoded.get("token"):
            raw = str(decoded["token"])
            decoded["token"] = unprotect_secret(raw)
            if raw and not raw.startswith(SECRET_PREFIX):
                try:
                    data["token"] = protect_secret(raw)
                    migrated = True
                except SecretProtectionUnavailable:
                    pass
        for index, entry in enumerate(decoded.get("tokens") or []):
            if not isinstance(entry, dict) or not entry.get("token"):
                continue
            raw = str(entry["token"])
            entry["token"] = unprotect_secret(raw)
            if raw and not raw.startswith(SECRET_PREFIX):
                try:
                    data["tokens"][index]["token"] = protect_secret(raw)
                    migrated = True
                except SecretProtectionUnavailable:
                    pass
        if migrated:
            encrypted = _encrypt_token_payload(data)
            atomic_write_text(TOKEN_PATH, json.dumps(encrypted, ensure_ascii=False, indent=2) + "\n")
        return decoded
    except Exception:
        return {}


def _provider_key(provider: str) -> str:
    raw = str(provider or "").strip().lower().replace("-", "_")
    if raw in {"xy", "idlecloud", "xianyun_api"}:
        return PROVIDER_XIANYUN
    if raw == PROVIDER_XIANYUN:
        return PROVIDER_XIANYUN
    if raw == PROVIDER_UNKNOWN:
        return PROVIDER_UNKNOWN
    return PROVIDER_NOVELAI


def _provider_label(provider: str) -> str:
    key = _provider_key(provider)
    if key == PROVIDER_XIANYUN:
        return "Xianyun"
    if key == PROVIDER_UNKNOWN:
        return "Unknown"
    return "NAI"


def _token_id(token: str, provider: str = PROVIDER_NOVELAI) -> str:
    prefix = "xianyun" if _provider_key(provider) == PROVIDER_XIANYUN else "nai"
    digest = hashlib.sha1(f"{prefix}:{str(token or '')}".encode("utf-8")).hexdigest()[:12]
    return f"{prefix}_{digest}"


def _mask_token(token: str) -> str:
    raw = str(token or "").strip()
    if not raw:
        return ""
    if len(raw) <= 10:
        return "*" * len(raw)
    # 只保留类型前缀（如 pst-），不回显尾部，避免拼接爆破
    prefix = raw[:4] if re.match(r"^[A-Za-z0-9\-_]{4}", raw) else ""
    return f"{prefix}{'*' * 8}"


def _parse_token_text(raw: str) -> list[Any]:
    text = str(raw or "").strip()
    if not text:
        return []
    if text.startswith(("[", "{")):
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = None
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]

    seen: set[str] = set()
    tokens: list[Any] = []
    splitter = r"[\n;]+" if "{" in text else r"[\n,;]+"
    for part in re.split(splitter, text):
        token = part.strip()
        if not token or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _guess_provider(token: str) -> str:
    raw = str(token or "").strip()
    if raw.lower().startswith("bearer "):
        raw = raw.split(None, 1)[1].strip()
    if raw.startswith("pst-"):
        return PROVIDER_NOVELAI
    if raw.lower().startswith(("xianyun:", "xy:", "idlecloud:")):
        return PROVIDER_XIANYUN
    if re.fullmatch(r"[A-Za-z0-9_-]{32,}", raw):
        return PROVIDER_XIANYUN
    return PROVIDER_UNKNOWN


def _legacy_save_provider(provider: str, token: str) -> str:
    """Bulk save keeps legacy bare NovelAI tokens usable without slow probing.

    `add_token_entry` is the strict path and still probes/rejects unknown
    providers. `save_token` is the backwards-compatible bulk paste path used by
    old installs and tests: short opaque tokens such as `token-alpha` used to be
    treated as NovelAI slots. Long bare keys are still classified as Xianyun by
    `_guess_provider`.
    """
    key = _provider_key(str(provider or PROVIDER_UNKNOWN))
    if key != PROVIDER_UNKNOWN:
        return key
    guessed = _guess_provider(token)
    if guessed != PROVIDER_UNKNOWN:
        return guessed
    return PROVIDER_NOVELAI


def _parse_token_line(raw: Any, idx: int) -> dict[str, Any] | None:
    if isinstance(raw, dict):
        token = str(raw.get("token") or raw.get("api_key") or "").strip()
        if token.lower().startswith("bearer "):
            token = token.split(None, 1)[1].strip()
        provider = _provider_key(str(raw.get("provider") or raw.get("type") or _guess_provider(token)))
        label = str(raw.get("label") or f"{_provider_label(provider)} #{idx + 1}").strip()
        if not token:
            return None
        return {
            "id": str(raw.get("id") or _token_id(token, provider)).strip(),
            "label": label,
            "provider": provider,
            "token": token,
            "enabled": raw.get("enabled") is not False,
            "api_base": str(raw.get("api_base") or raw.get("base_url") or "").strip(),
            "proxy": str(raw.get("proxy") or "").strip(),
        }

    text = str(raw or "").strip()
    if not text:
        return None
    provider = ""
    label = ""
    token = text
    if text.startswith("{"):
        try:
            obj = json.loads(text)
        except Exception:
            obj = {}
        if isinstance(obj, dict):
            token = str(obj.get("token") or obj.get("api_key") or "").strip()
            provider = _provider_key(str(obj.get("provider") or obj.get("type") or _guess_provider(token)))
            label = str(obj.get("label") or "").strip()
            api_base = str(obj.get("api_base") or obj.get("base_url") or "").strip()
            proxy = str(obj.get("proxy") or "").strip()
        else:
            api_base = ""
            proxy = ""
    else:
        api_base = ""
        proxy = ""
        match = re.match(r"^(?P<prefix>xianyun|xy|idlecloud|novelai|nai)\s*:\s*(?P<token>.+)$", text, re.I)
        if match:
            provider = _provider_key(match.group("prefix"))
            token = match.group("token").strip()
        else:
            provider = _guess_provider(token)
    if token.lower().startswith("bearer "):
        token = token.split(None, 1)[1].strip()
    if not token:
        return None
    provider = _provider_key(provider or _guess_provider(token))
    label = label or f"{_provider_label(provider)} #{idx + 1}"
    return {
        "id": _token_id(token, provider),
        "label": label,
        "provider": provider,
        "token": token,
        "enabled": True,
        "api_base": api_base,
        "proxy": proxy,
    }


def _normalize_token_entries(data: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    data = data if isinstance(data, dict) else _read_token_file()
    raw_entries: list[Any] = []
    if isinstance(data.get("tokens"), list):
        raw_entries.extend(data.get("tokens") or [])
    elif data.get("token"):
        raw_entries.append(
            {
                "token": data.get("token"),
                "label": data.get("label") or "NAI #1",
                "enabled": True,
                "updated_at": data.get("updated_at", ""),
            }
        )

    seen: set[str] = set()
    entries: list[dict[str, Any]] = []
    for idx, raw in enumerate(raw_entries):
        if isinstance(raw, dict):
            token = str(raw.get("token") or "").strip()
            if token.lower().startswith("bearer "):
                token = token.split(None, 1)[1].strip()
            provider = _provider_key(str(raw.get("provider") or _guess_provider(token)))
            label = (
                str(raw.get("label") or f"{_provider_label(provider)} #{idx + 1}").strip()
                or f"{_provider_label(provider)} #{idx + 1}"
            )
            enabled = raw.get("enabled") is not False
            updated_at = str(raw.get("updated_at") or data.get("updated_at") or "")
            entry_id = str(raw.get("id") or _token_id(token, provider)).strip()
            api_base = str(raw.get("api_base") or raw.get("base_url") or "").strip()
            proxy = str(raw.get("proxy") or "").strip()
        else:
            parsed = _parse_token_line(str(raw or ""), idx)
            if not parsed:
                continue
            token = str(parsed.get("token") or "").strip()
            provider = _provider_key(str(parsed.get("provider") or _guess_provider(token)))
            label = str(parsed.get("label") or f"{_provider_label(provider)} #{idx + 1}")
            enabled = True
            updated_at = str(data.get("updated_at") or "")
            entry_id = str(parsed.get("id") or _token_id(token, provider))
            api_base = str(parsed.get("api_base") or "").strip()
            proxy = str(parsed.get("proxy") or "").strip()
        dedupe_key = f"{provider}:{token}"
        if not token or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        entries.append(
            {
                "id": entry_id or _token_id(token, provider),
                "label": label,
                "provider": provider,
                "token": token,
                "enabled": enabled,
                "updated_at": updated_at,
                "masked": _mask_token(token),
                "api_base": api_base,
                "proxy": proxy,
                "disabled_at": str(raw.get("disabled_at") or ""),
                "disabled_reason": str(raw.get("disabled_reason") or ""),
            }
        )
    return entries


def _invalidate_token_cache() -> None:
    """在保存 token 后使缓存失效。"""
    global _TOKEN_ENTRIES_CACHE, _TOKEN_ENTRIES_CACHE_AT
    _TOKEN_ENTRIES_CACHE = None
    _TOKEN_ENTRIES_CACHE_AT = 0.0


def _disable_token_entry(entry: dict[str, Any], reason: str) -> None:
    token_id = str(entry.get("id") or "")
    token_value = str(entry.get("token") or "")
    if not token_id and not token_value:
        return
    data = _read_token_file()
    raw_entries = data.get("tokens")
    if not isinstance(raw_entries, list):
        return
    changed = False
    now = datetime.now().isoformat(timespec="seconds")
    for raw in raw_entries:
        if not isinstance(raw, dict):
            continue
        raw_token = str(raw.get("token") or raw.get("api_key") or "").strip()
        raw_provider = _provider_key(str(raw.get("provider") or raw.get("type") or _guess_provider(raw_token)))
        raw_id = str(raw.get("id") or _token_id(raw_token, raw_provider)).strip()
        if (token_id and raw_id == token_id) or (token_value and raw_token == token_value):
            raw["enabled"] = False
            raw["disabled_at"] = now
            raw["disabled_reason"] = str(reason or "provider disabled")[:500]
            changed = True
    if not changed:
        return
    data["tokens"] = raw_entries
    data["updated_at"] = now
    encrypted = _encrypt_token_payload(data)
    atomic_write_text(TOKEN_PATH, json.dumps(encrypted, ensure_ascii=False, indent=2) + "\n")
    _invalidate_token_cache()


def _remove_token_entry(entry: dict[str, Any], reason: str) -> bool:
    token_id = str(entry.get("id") or "")
    token_value = str(entry.get("token") or "")
    if not token_id and not token_value:
        return False
    data = _read_token_file()
    raw_entries = data.get("tokens")
    if not isinstance(raw_entries, list):
        return False
    kept: list[Any] = []
    removed = False
    for raw in raw_entries:
        if not isinstance(raw, dict):
            kept.append(raw)
            continue
        raw_token = str(raw.get("token") or raw.get("api_key") or "").strip()
        raw_provider = _provider_key(str(raw.get("provider") or raw.get("type") or _guess_provider(raw_token)))
        raw_id = str(raw.get("id") or _token_id(raw_token, raw_provider)).strip()
        if (token_id and raw_id == token_id) or (token_value and raw_token == token_value):
            removed = True
            continue
        kept.append(raw)
    if not removed:
        return False
    now = datetime.now().isoformat(timespec="seconds")
    last_removed = {
        "id": token_id,
        "label": str(entry.get("label") or ""),
        "provider": _provider_key(str(entry.get("provider") or "")),
        "removed_at": now,
        "reason": str(reason or "token unusable")[:500],
    }
    _write_token_entries([raw for raw in kept if isinstance(raw, dict)], last_removed=last_removed)
    return True


def _encrypt_token_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy and encrypt every token field so plaintext credentials never
    hit disk. protect_secret is idempotent (dpapi:v1: prefix is kept)."""
    import copy as _copy

    out = _copy.deepcopy(payload)
    for key in ("token", "api_key"):
        value = out.get(key)
        if isinstance(value, str) and value:
            out[key] = protect_secret(value)
    for entry in out.get("tokens") or []:
        if not isinstance(entry, dict):
            continue
        for key in ("token", "api_key"):
            value = entry.get(key)
            if isinstance(value, str) and value:
                entry[key] = protect_secret(value)
    removed = out.get("last_removed_token")
    if isinstance(removed, dict):
        for key in ("token", "api_key"):
            value = removed.get(key)
            if isinstance(value, str) and value:
                removed[key] = protect_secret(value)
    return out


def _write_token_entries(
    entries: list[dict[str, Any]],
    *,
    last_removed: dict[str, Any] | None = None,
) -> None:
    now = datetime.now().isoformat(timespec="seconds")
    payload: dict[str, Any] = {
        "token": str((entries[0] if entries else {}).get("token") or ""),
        "tokens": entries,
        "updated_at": now,
    }
    if last_removed:
        payload["last_removed_token"] = last_removed
    encrypted = _encrypt_token_payload(payload)
    atomic_write_text(TOKEN_PATH, json.dumps(encrypted, ensure_ascii=False, indent=2) + "\n")
    _invalidate_token_cache()


def _enabled_token_entries() -> list[dict[str, Any]]:
    global _TOKEN_ENTRIES_CACHE, _TOKEN_ENTRIES_CACHE_AT
    now = time.time()
    if _TOKEN_ENTRIES_CACHE is not None and now - _TOKEN_ENTRIES_CACHE_AT < _TOKEN_ENTRIES_CACHE_TTL:
        return list(_TOKEN_ENTRIES_CACHE)
    entries = [
        entry for entry in _normalize_token_entries()
        if entry.get("enabled") and _provider_key(str(entry.get("provider", ""))) != PROVIDER_UNKNOWN
    ]
    _TOKEN_ENTRIES_CACHE = entries
    _TOKEN_ENTRIES_CACHE_AT = now
    return list(entries)


def _token_disabled_until(token_id: str) -> float:
    state = _TOKEN_FAILURES.get(str(token_id) or "")
    if not state:
        return 0.0
    until = float(state.get("disabled_until") or 0.0)
    if until and until <= time.time():
        _TOKEN_FAILURES.pop(str(token_id), None)
        return 0.0
    return until


def _is_token_temporarily_disabled(entry: dict[str, Any]) -> bool:
    return _token_disabled_until(str(entry.get("id") or "")) > time.time()


def _record_token_failure(entry: dict[str, Any], message: str) -> bool:
    token_id = str(entry.get("id") or "")
    if not token_id:
        return False
    provider = _provider_key(str(entry.get("provider") or PROVIDER_NOVELAI))
    text = str(message or "").lower()
    if provider == PROVIDER_NOVELAI and (
        "not enough anlas" in text
        or "out of trial image generations" in text
        or ("402" in text and "trial" in text)
    ):
        now = time.time()
        _TOKEN_FAILURES[token_id] = {
            "count": 1,
            "reason": "quota_exhausted",
            "last_error": str(message or ""),
            "last_at": now,
            "disabled_until": now + _TOKEN_FAILURE_TTL_SEC,
        }
        validation = dict(_TOKEN_VALIDATIONS.get(token_id) or {})
        validation["quota_exhausted"] = True
        _TOKEN_VALIDATIONS[token_id] = validation
        return True
    # Phrase-only matches — never bare status codes or bare "banned"/"suspended"
    # (those match too much unrelated provider text).
    permanent_parts = (
        "token invalid",
        "api key invalid",
        "invalid or expired",
        "unauthorized",
        "forbidden",
        "permission denied",
        "account disabled",
        "account banned",
        "account suspended",
        "or banned",
        "insufficient balance",
        "no balance",
        "quota exceeded",
        "recaptcha",
        "status code 401",
        "status code 403",
        "http 401",
        "http 403",
        "error 401",
        "error 403",
    )
    permanent_failure = any(part in text for part in permanent_parts)
    if permanent_failure:
        _remove_token_entry(entry, message)
        _TOKEN_FAILURES.pop(token_id, None)
        return True
    xianyun_disabled = (
        provider == PROVIDER_XIANYUN
        and any(
            part in text
            for part in (
                "api key invalid",
                "invalid or expired",
                "unauthorized",
                "forbidden",
                "permission denied",
                "account disabled",
                "account banned",
                "account suspended",
                "or banned",
                "封禁",
                "禁用",
                "停用",
                "冻结",
                "余额不足",
                "insufficient balance",
                "no balance",
                "quota exceeded",
                "status code 403",
                "http 403",
                "error 403",
            )
        )
    )
    if xianyun_disabled:
        _remove_token_entry(entry, message)
        _TOKEN_FAILURES.pop(token_id, None)
        return True

    transient_failure = any(
        part in text
        for part in (
            "request too frequent",
            "too frequent",
            "retry later",
            "429",
            "500",
            "502",
            "503",
            "504",
            "internal server error",
            "bad gateway",
            "service unavailable",
            "gateway timeout",
            "temporarily unavailable",
            "timeout",
            "timed out",
        )
    )
    if transient_failure:
        now = time.time()
        ttl = (
            _TRANSIENT_PROVIDER_TTL_SEC
            if provider == PROVIDER_XIANYUN
            else _NAI_TRANSIENT_TTL_SEC
        )
        state = _TOKEN_FAILURES.get(token_id) or {"count": 0}
        state["count"] = int(state.get("count") or 0) + 1
        state["last_error"] = str(message or "")
        state["last_at"] = now
        state["disabled_until"] = now + ttl
        _TOKEN_FAILURES[token_id] = state
        _LAST_GEN_AT_BY_TOKEN[token_id] = now
        return True

    hard_failure = any(
        part in text
        for part in (
            "token invalid",
            "invalid or expired",
            "expired",
            "recaptcha token is required",
            "recaptcha",
            "401",
        )
    )
    if provider == PROVIDER_NOVELAI and "trial generation" in text:
        hard_failure = True
    if not hard_failure:
        return False
    now = time.time()
    state = _TOKEN_FAILURES.get(token_id) or {"count": 0}
    state["count"] = int(state.get("count") or 0) + 1
    state["last_error"] = str(message or "")
    state["last_at"] = now
    if state["count"] >= _TOKEN_FAILURE_LIMIT:
        state["disabled_until"] = now + _TOKEN_FAILURE_TTL_SEC
    _TOKEN_FAILURES[token_id] = state
    return True


def _clear_token_failure(entry: dict[str, Any]) -> None:
    token_id = str(entry.get("id") or "")
    if token_id:
        _TOKEN_FAILURES.pop(token_id, None)


def _exception_message(exc: BaseException) -> str:
    text = str(exc).strip()
    return text or type(exc).__name__


def _candidate_token_entries(preferred: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    entries = _enabled_token_entries()
    live_ids = {str(entry.get("id") or "") for entry in entries}
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def add(entry: dict[str, Any] | None) -> None:
        if not entry:
            return
        token_id = str(entry.get("id") or "")
        if not token_id or token_id in seen:
            return
        if token_id not in live_ids:
            return
        if _is_token_temporarily_disabled(entry):
            return
        seen.add(token_id)
        out.append(entry)

    def safe_rank(entry: dict[str, Any]) -> int:
        provider = _provider_key(str(entry.get("provider") or ""))
        if provider == PROVIDER_XIANYUN:
            return 2
        validation = _TOKEN_VALIDATIONS.get(str(entry.get("id") or "")) or {}
        if int(validation.get("tier") or 0) == 0:
            return 0
        if validation.get("is_opus"):
            return 1
        if validation:
            return 99
        return 1

    ordered = sorted(entries, key=safe_rank)
    if preferred and safe_rank(preferred) < 99:
        add(preferred)
    for entry in ordered:
        if safe_rank(entry) < 99:
            add(entry)
    return out


def _public_token_entry(entry: dict[str, Any]) -> dict[str, Any]:
    # api_base/proxy 属于本机网络布局信息，不回显原值，只暴露是否已配置
    return {
        "id": entry.get("id", ""),
        "label": entry.get("label", ""),
        "provider": _provider_key(str(entry.get("provider") or PROVIDER_NOVELAI)),
        "enabled": bool(entry.get("enabled")),
        "masked": entry.get("masked", ""),
        "updated_at": entry.get("updated_at", ""),
        "api_base": "",
        "proxy": "",
        "has_api_base": bool(str(entry.get("api_base") or "").strip()),
        "has_proxy": bool(str(entry.get("proxy") or "").strip()),
        "disabled_at": entry.get("disabled_at", ""),
        "disabled_reason": entry.get("disabled_reason", ""),
    }


def _probe_provider(token: str, api_base: str = "", timeout: float = 8.0) -> str:
    """通过 API 探测 token 的实际 provider。"""
    raw = str(token or "").strip()
    if not raw:
        return PROVIDER_UNKNOWN
    # 先尝试 NAI 订阅接口
    try:
        import httpx
        headers = {"Authorization": f"Bearer {raw}"}
        r = httpx.get("https://api.novelai.net/user/subscription", headers=headers, timeout=timeout)
        if r.status_code == 200:
            return PROVIDER_NOVELAI
    except Exception:
        pass
    # 再尝试闲云提交接口
    try:
        import httpx
        test_base = (api_base or XIANYUN_API_BASE).rstrip("/")
        headers = {"Authorization": f"Bearer {raw}", "Content-Type": "application/json"}
        r = httpx.post(f"{test_base}/generate_image", headers=headers, json={}, timeout=timeout)
        if r.status_code in {400, 422} or (r.status_code != 404 and r.status_code < 500):
            return PROVIDER_XIANYUN
    except Exception:
        pass
    return PROVIDER_UNKNOWN


def save_token(token: str, default_provider: str = "") -> dict[str, Any]:
    """Save multi-line tokens. Optional default_provider forces unknown bare keys."""
    raw_tokens = _parse_token_text(token)
    parsed_entries = [
        parsed
        for idx, value in enumerate(raw_tokens)
        if (parsed := _parse_token_line(value, idx))
    ]
    if not parsed_entries:
        raise ValueError("token cannot be empty")
    force_provider = _provider_key(str(default_provider or "").strip()) if default_provider else ""
    if force_provider == PROVIDER_UNKNOWN:
        force_provider = ""
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    updated_at = datetime.now().isoformat(timespec="seconds")
    # Inherit proxy/api_base from the previous config when the incoming line
    # does not carry them, so UI saves never wipe per-token network settings.
    old_entries = _normalize_token_entries()
    old_settings = {
        f"{_provider_key(str(e.get('provider') or ''))}:{str(e.get('token') or '')}": e
        for e in old_entries
    }
    entries = []
    seen: set[str] = set()
    provider_counts: dict[str, int] = {}
    for entry in parsed_entries:
        raw_provider = str(entry.get("provider") or PROVIDER_UNKNOWN)
        value = str(entry.get("token") or "").strip()
        if force_provider and _provider_key(raw_provider) == PROVIDER_UNKNOWN:
            provider = force_provider
        else:
            provider = _legacy_save_provider(raw_provider, value)
        dedupe_key = f"{provider}:{value}"
        if not value or dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        provider_counts[provider] = provider_counts.get(provider, 0) + 1
        old_entry = old_settings.get(dedupe_key) or {}
        entries.append(
            {
                "id": _token_id(value, provider),
                "label": entry.get("label") or f"{_provider_label(provider)} #{provider_counts[provider]}",
                "provider": provider,
                "token": value,
                "enabled": entry.get("enabled") is not False,
                "updated_at": updated_at,
                "api_base": str(entry.get("api_base") or old_entry.get("api_base") or "").strip(),
                "proxy": str(entry.get("proxy") or old_entry.get("proxy") or "").strip(),
            }
        )
    if not entries:
        raise ValueError("token cannot be empty")
    payload = {
        "token": entries[0]["token"],
        "tokens": entries,
        "updated_at": updated_at,
    }
    encrypted = _encrypt_token_payload(payload)
    atomic_write_text(TOKEN_PATH, json.dumps(encrypted, ensure_ascii=False, indent=2) + "\n")
    _invalidate_token_cache()
    return {
        "ok": True,
        "has_token": True,
        "updated_at": updated_at,
        "token_count": len(entries),
        "enabled_count": len(entries),
        "concurrency": len(entries),
        "providers": dict(provider_counts),
    }


def add_token_entry(payload: dict[str, Any]) -> dict[str, Any]:
    raw_token = str(payload.get("token") or payload.get("api_key") or "").strip()
    parsed_line = _parse_token_line(raw_token, 0)
    if parsed_line and _provider_key(str(parsed_line.get("provider") or "")) != PROVIDER_UNKNOWN:
        payload = {**payload, **parsed_line}
    parsed = _parse_token_line(
        {
            "token": payload.get("token") or payload.get("api_key") or "",
            "provider": payload.get("provider") or payload.get("type") or "",
            "label": payload.get("label") or "",
            "api_base": payload.get("api_base") or payload.get("base_url") or "",
            "proxy": payload.get("proxy") or "",
        },
        0,
    )
    if not parsed:
        raise ValueError("token cannot be empty")
    provider = _provider_key(str(parsed.get("provider") or PROVIDER_UNKNOWN))
    value = str(parsed.get("token") or "").strip()
    if provider == PROVIDER_UNKNOWN:
        probed = _probe_provider(value, api_base=str(parsed.get("api_base") or ""))
        if probed != PROVIDER_UNKNOWN:
            provider = probed
    if provider == PROVIDER_UNKNOWN:
        raise ValueError("provider is required: choose novelai or xianyun")
    from network_safety import validate_outbound_proxy, validate_provider_api_base

    api_base = validate_provider_api_base(str(parsed.get("api_base") or ""))
    proxy = validate_outbound_proxy(str(parsed.get("proxy") or ""))
    entries = _normalize_token_entries()
    dedupe_key = f"{provider}:{value}"
    if any(f"{_provider_key(str(e.get('provider') or ''))}:{e.get('token')}" == dedupe_key for e in entries):
        raise ValueError("token already exists in pool")
    updated_at = datetime.now().isoformat(timespec="seconds")
    provider_count = sum(1 for e in entries if _provider_key(str(e.get("provider") or "")) == provider) + 1
    entries.append(
        {
            "id": _token_id(value, provider),
            "label": str(parsed.get("label") or f"{_provider_label(provider)} #{provider_count}").strip(),
            "provider": provider,
            "token": value,
            "enabled": True,
            "updated_at": updated_at,
            "api_base": api_base,
            "proxy": proxy,
        }
    )
    _write_token_entries(entries)
    return {"ok": True, "message": "token added", **token_status()}


def delete_token_entry(token_id: str) -> dict[str, Any]:
    tid = str(token_id or "").strip()
    if not tid:
        raise ValueError("token_id is required")
    entries = _normalize_token_entries()
    kept = [entry for entry in entries if str(entry.get("id") or "") != tid]
    if len(kept) == len(entries):
        raise ValueError("token not found")
    _write_token_entries(kept)
    _TOKEN_FAILURES.pop(tid, None)
    return {"ok": True, "message": "token deleted", **token_status()}


def _check_one_token_entry(entry: dict[str, Any], *, remove_bad: bool = True) -> dict[str, Any]:
    provider = _provider_key(str(entry.get("provider") or PROVIDER_NOVELAI))
    token_id = str(entry.get("id") or "")
    result = {
        **_public_token_entry(entry),
        "ok": False,
        "checked": True,
        "removed": False,
        "message": "",
    }
    if provider == PROVIDER_XIANYUN:
        api_base = str(entry.get("api_base") or XIANYUN_API_BASE).rstrip("/")
        try:
            status, text = _token_check_request(
                "POST",
                f"{api_base}/generate_image",
                _xianyun_headers(str(entry.get("token") or "")),
                json_body={},
                timeout_sec=10.0,
            )
            if status in {401, 403}:
                msg = f"Xianyun token check failed {status}: {text[:200]}"
                if remove_bad:
                    result["removed"] = _remove_token_entry(entry, msg)
                result["message"] = msg
                return result
            if status in {400, 422}:
                result["ok"] = True
                result["message"] = "Xianyun token accepted; generation endpoint reached parameter validation"
                return result
            if status >= 500:
                result["message"] = f"Xianyun check inconclusive {status}: {text[:200]}"
                return result
            result["ok"] = True
            result["message"] = "Xianyun token accepted by generation endpoint"
            return result
        except Exception as exc:
            result["message"] = f"Xianyun check inconclusive: {exc}"
            return result

    try:
        status, text = _token_check_request(
            "GET",
            f"{API_BASE}/user/subscription",
            _auth_headers(str(entry.get("token") or "")),
            timeout_sec=12.0,
            proxy=str(entry.get("proxy") or ""),
        )
        if status == 200:
            data = json.loads(text or "{}")
            tier = int(data.get("tier") or 0)
            result.update(
                {
                    "ok": True,
                    "tier": tier,
                    "is_opus": tier >= 3 or "opus" in str(data.get("activeSubscription", "")).lower(),
                    "message": "NovelAI token OK",
                }
            )
            return result
        if status == 400:
            # pst- persistent tokens are rejected by the account API (api.novelai.net);
            # validate against the image API instead.
            img_status, img_text = _token_check_request(
                "GET",
                f"{IMAGE_API_BASE}/user/data",
                _auth_headers(str(entry.get("token") or "")),
                timeout_sec=12.0,
                proxy=str(entry.get("proxy") or ""),
            )
            if img_status == 200:
                img = json.loads(img_text or "{}")
                sub = img.get("subscription") or {}
                tier = int(sub.get("tier") or 0)
                result.update(
                    {
                        "ok": True,
                        "tier": tier,
                        "is_opus": tier >= 3 or "opus" in str(sub.get("activeSubscription", "")).lower(),
                        "account_status_available": False,
                        "message": f"NovelAI persistent token OK (image API, tier={tier})",
                    }
                )
                return result
            if img_status in {401, 403}:
                msg = f"NAI token check failed {img_status}: {img_text[:200]}"
                if remove_bad:
                    result["removed"] = _remove_token_entry(entry, msg)
                result["message"] = msg
                return result
            info_status, info_text = _token_check_request(
                "GET",
                f"{API_BASE}/user/information",
                _auth_headers(str(entry.get("token") or "")),
                timeout_sec=12.0,
                proxy=str(entry.get("proxy") or ""),
            )
            if info_status == 200:
                info = json.loads(info_text or "{}")
                result.update(
                    {
                        "ok": True,
                        "tier": 0,
                        "plan": "paper",
                        "is_opus": False,
                        "free_confirmed": True,
                        "account_status_available": True,
                        "email_verified": bool(info.get("emailVerified")),
                        "message": "NovelAI Paper account verified",
                    }
                )
                return result
            result.update(
                {
                    "account_status_available": False,
                    "removed": False,
                    "message": (
                        "NovelAI persistent generation token preserved; "
                        "account status endpoint is unavailable"
                    ),
                }
            )
            return result
        msg = f"NAI token check failed {status}: {text[:200]}"
        if status in {400, 401, 403} and remove_bad:
            result["removed"] = _remove_token_entry(entry, msg)
        result["message"] = msg
        return result
    except Exception as exc:
        result["message"] = f"NovelAI check failed: {exc}"
        return result


def check_token_pool(token_id: str = "", *, remove_bad: bool = True) -> dict[str, Any]:
    entries = _normalize_token_entries()
    if token_id:
        entries = [entry for entry in entries if str(entry.get("id") or "") == str(token_id)]
        if not entries:
            raise ValueError("token not found")
    results = [_check_one_token_entry(entry, remove_bad=remove_bad) for entry in entries]
    for entry, result in zip(entries, results):
        entry_id = str(entry.get("id") or "")
        if not entry_id:
            continue
        validation = dict(_TOKEN_VALIDATIONS.get(entry_id) or {})
        validation.update(
            {
                "ok": bool(result.get("ok")),
                "tier": result.get("tier"),
                "is_opus": bool(result.get("is_opus")),
                "free_confirmed": bool(result.get("free_confirmed")),
                "quota_exhausted": False if result.get("ok") else bool(
                    validation.get("quota_exhausted")
                ),
            }
        )
        _TOKEN_VALIDATIONS[entry_id] = validation
        if result.get("ok"):
            _TOKEN_FAILURES.pop(entry_id, None)
    return {"ok": True, "results": results, **token_status()}


def token_status() -> dict[str, Any]:
    data = _read_token_file()
    entries = _normalize_token_entries(data)
    enabled = [entry for entry in entries if entry.get("enabled")]
    providers: dict[str, int] = {}
    for entry in enabled:
        provider = _provider_key(str(entry.get("provider") or PROVIDER_NOVELAI))
        providers[provider] = providers.get(provider, 0) + 1
    return {
        "has_token": bool(enabled),
        "token_count": len(entries),
        "enabled_count": len(enabled),
        "concurrency": len(enabled),
        "providers": providers,
        "tokens": [_public_token_entry(entry) for entry in entries],
        "updated_at": data.get("updated_at", ""),
    }


def list_generation_slots() -> list[dict[str, Any]]:
    return [_public_token_entry(entry) for entry in _enabled_token_entries()]


def generation_concurrency() -> int:
    return len(_candidate_token_entries())


def generation_concurrency_for_batch(target_count: Any = 1, **_kwargs: Any) -> int:
    try:
        count = max(0, int(target_count))
    except (TypeError, ValueError):
        count = len(target_count or []) if isinstance(target_count, list) else 1
    return min(count, generation_concurrency())


def _slot_cooldown_sec(entry: dict[str, Any]) -> float:
    """Cooldown per provider.

    NAI keeps a fixed 3s cooldown for stability regardless of slot count
    (user preference: 稳一点).  Xianyun is a slow relay and keeps its own
    longer cooldown; the two providers stay separated.
    """
    provider = _provider_key(str(entry.get("provider") or PROVIDER_NOVELAI))
    if provider == PROVIDER_XIANYUN:
        return _XIANYUN_COOLDOWN_SEC
    return _COOLDOWN_SEC


def _select_token_entry(token_id: str = "") -> dict[str, Any]:
    entries = _enabled_token_entries()
    if not entries:
        raise ValueError("NovelAI token is not configured")
    if token_id:
        for entry in entries:
            if str(entry.get("id") or "") == str(token_id):
                return entry
        raise ValueError(f"NovelAI token slot is missing or disabled: {token_id}")
    return entries[0]


def _next_token_entry() -> dict[str, Any]:
    global _TOKEN_CURSOR
    entries = _candidate_token_entries()
    if not entries:
        raise ValueError("NovelAI token is not configured")
    idx = _TOKEN_CURSOR % len(entries)
    _TOKEN_CURSOR = (idx + 1) % len(entries)
    return entries[idx]


def _auth_headers(token: str) -> dict[str, str]:
    token = str(token or "").strip()
    if not token:
        raise ValueError("NovelAI token is not configured")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Referer": "https://novelai.net/",
    }


def _xianyun_headers(token: str) -> dict[str, str]:
    token = str(token or "").strip()
    if not token:
        raise ValueError("Xianyun API key is not configured")
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json, text/plain, */*",
        "Origin": "https://nai3.idlecloud.cc",
        "Referer": "https://nai3.idlecloud.cc/",
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }


def get_subscription(token_id: str = "") -> dict[str, Any]:
    entry = _select_token_entry(token_id)
    provider = _provider_key(str(entry.get("provider") or PROVIDER_NOVELAI))
    if provider == PROVIDER_XIANYUN:
        return {
            "ok": True,
            "tier": None,
            "is_opus": True,
            "anlas_subscription": None,
            "anlas_purchased": None,
            "anlas_total": None,
            "perks": {"provider": PROVIDER_XIANYUN, "membership": True},
            "queue": queue_status(),
            "token_id": entry.get("id", ""),
            "token_label": entry.get("label", ""),
            "provider": provider,
        }
    proxy = str(entry.get("proxy") or "").strip()
    with httpx.Client(timeout=httpx.Timeout(12.0, connect=6.0), proxy=proxy or None) as client:
        resp = client.get(
            f"{API_BASE}/user/subscription",
            headers=_auth_headers(str(entry.get("token") or "")),
        )
        if resp.status_code == 200:
            data = resp.json()
        else:
            # pst- persistent tokens are rejected by the account API; use the
            # image API which carries the same subscription payload.
            image = client.get(
                f"{IMAGE_API_BASE}/user/data",
                headers=_auth_headers(str(entry.get("token") or "")),
            )
            if image.status_code == 200:
                image_payload = image.json()
                data = dict(image_payload.get("subscription") or {})
                steps = image_payload.get("trainingStepsLeft") or {}
                if steps and "trainingAmountLeft" not in data:
                    data["trainingAmountLeft"] = steps
            else:
                info = client.get(
                    f"{API_BASE}/user/information",
                    headers=_auth_headers(str(entry.get("token") or "")),
                )
                if info.status_code == 200:
                    payload = info.json()
                    return {
                        "ok": True,
                        "tier": 0,
                        "plan": "paper",
                        "membership_active": False,
                        "is_opus": False,
                        "email_verified": bool(payload.get("emailVerified")),
                        "free_confirmed": True,
                        "account_status_available": True,
                        "anlas_subscription": None,
                        "anlas_purchased": None,
                        "anlas_total": None,
                        "token_id": entry.get("id", ""),
                        "provider": provider,
                    }
                return {
                    "ok": True,
                    "tier": None,
                    "plan": "unknown",
                    "membership_active": None,
                    "is_opus": False,
                    "generation_token_configured": True,
                    "account_status_available": False,
                    "token_valid": None,
                    "anlas_subscription": None,
                    "anlas_purchased": None,
                    "anlas_total": None,
                    "token_id": entry.get("id", ""),
                    "provider": provider,
                }
    training = data.get("trainingAmountLeft") or data.get("trainingStepsLeft") or {}
    fixed = int(training.get("fixedTrainingStepsLeft") or 0)
    purchased = int(data.get("totalCredits") or data.get("purchasedTrainingSteps") or 0)
    tier = int(data.get("tier") or 0)
    is_opus = tier >= 3 or "opus" in str(data.get("activeSubscription", "")).lower()
    return {
        "ok": True,
        "tier": tier,
        "is_opus": is_opus,
        "anlas_subscription": fixed,
        "anlas_purchased": purchased,
        "anlas_total": fixed + purchased,
        "perks": data.get("perks") or {},
        "queue": queue_status(),
        "token_id": entry.get("id", ""),
        "token_label": entry.get("label", ""),
    }


def queue_status() -> dict[str, Any]:
    with _TOKEN_STATE_LOCK:
        job = dict(_JOB)
        job["active"] = list(_ACTIVE_JOBS.values())
        job["active_count"] = len(_ACTIVE_JOBS)
        if _ACTIVE_JOBS:
            job["status"] = "running"
        return job


def _lock_for_token(token_id: str) -> asyncio.Lock:
    lock = _TOKEN_LOCKS.get(token_id)
    if lock is None:
        lock = asyncio.Lock()
        _TOKEN_LOCKS[token_id] = lock
    return lock


def _cooldown_wait(token_id: str, entry: dict[str, Any] | None = None) -> float:
    last = float(_LAST_GEN_AT_BY_TOKEN.get(token_id) or 0.0)
    cooldown = _slot_cooldown_sec(entry or {})
    return max(0.0, cooldown - (time.time() - last))


def _pick_available_token() -> tuple[dict[str, Any] | None, str, float, str]:
    global _TOKEN_CURSOR
    entries = _enabled_token_entries()
    if not entries:
        raise ValueError("NovelAI token is not configured")
    best_cooldown: tuple[dict[str, Any], float] | None = None
    best_disabled_wait = 0.0
    start = _TOKEN_CURSOR % len(entries)
    for offset in range(len(entries)):
        idx = (start + offset) % len(entries)
        entry = entries[idx]
        token_id = str(entry["id"])
        disabled_until = _token_disabled_until(token_id)
        if disabled_until > time.time():
            disabled_wait = max(0.0, disabled_until - time.time())
            if best_disabled_wait <= 0 or disabled_wait < best_disabled_wait:
                best_disabled_wait = disabled_wait
            continue
        if _lock_for_token(token_id).locked():
            continue
        wait = _cooldown_wait(token_id, entry)
        if wait <= 0:
            _TOKEN_CURSOR = (idx + 1) % len(entries)
            return entry, "", 0.0, _provider_key(str(entry.get("provider") or PROVIDER_NOVELAI))
        if best_cooldown is None or wait < best_cooldown[1]:
            best_cooldown = (entry, wait)
    if best_cooldown is not None:
        return None, "cooldown", best_cooldown[1], _provider_key(str(best_cooldown[0].get("provider") or PROVIDER_NOVELAI))
    if best_disabled_wait > 0:
        return None, "cooldown", best_disabled_wait, PROVIDER_NOVELAI
    return None, "busy", 0.0, PROVIDER_NOVELAI


def _set_active_job(token_id: str, payload: dict[str, Any]) -> None:
    with _TOKEN_STATE_LOCK:
        _ACTIVE_JOBS[token_id] = dict(payload)
        _JOB.update(
            {
                "status": "running",
                "message": str(payload.get("message") or "Requesting NovelAI..."),
                "started_at": payload.get("started_at") or datetime.now().isoformat(timespec="seconds"),
                "work_id": payload.get("work_id"),
                "active": list(_ACTIVE_JOBS.values()),
                "active_count": len(_ACTIVE_JOBS),
            }
        )


def _clear_active_job(token_id: str, *, result: dict[str, Any] | None = None, error: str = "") -> None:
    with _TOKEN_STATE_LOCK:
        _ACTIVE_JOBS.pop(token_id, None)
        if result:
            _JOB["last_result"] = result
        if error:
            _JOB["last_error"] = error
            _JOB["error_at"] = datetime.now().isoformat(timespec="seconds")
        _JOB["active"] = list(_ACTIVE_JOBS.values())
        _JOB["active_count"] = len(_ACTIVE_JOBS)
        if _ACTIVE_JOBS:
            active = next(iter(_ACTIVE_JOBS.values()))
            _JOB["status"] = "running"
            _JOB["message"] = str(active.get("message") or "Requesting NovelAI...")
            _JOB["work_id"] = active.get("work_id")
        elif error:
            _JOB["status"] = "error"
            _JOB["message"] = error
            _JOB["work_id"] = None
        else:
            _JOB["status"] = "idle"
            _JOB["message"] = "idle"
            _JOB["work_id"] = None


def _reserve_generated_filename(work_id: int | None) -> str:
    suffix = f"_{work_id}" if work_id else ""
    with _FILENAME_LOCK:
        start = datetime.now()
        for offset in range(180):
            ts = (start + timedelta(seconds=offset)).strftime("%Y%m%d_%H%M%S")
            filename = f"{ts}{suffix}.png"
            if filename in _RESERVED_FILENAMES:
                continue
            if (GENERATED_DIR / filename).exists():
                continue
            _RESERVED_FILENAMES.add(filename)
            return filename
    return f"{datetime.now().strftime('%Y%m%d_%H%M%S')}_{int(time.time() * 1000)}{suffix}.png"


def _release_generated_filename(filename: str) -> None:
    name = str(filename or "").strip()
    if not name:
        return
    with _FILENAME_LOCK:
        _RESERVED_FILENAMES.discard(name)


def _clean_none_values(data: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in data.items() if value is not None}


def _read_image_reference(raw: Any) -> Any:
    if isinstance(raw, dict):
        out: dict[str, Any] = {}
        for key, value in raw.items():
            if key in {
                "image",
                "image_url",
                "image_base64",
                "base64",
                "path",
                "file",
                "url",
                "reference_image",
                "referenceImage",
            }:
                out[key] = _read_image_reference(value)
            else:
                out[key] = value
        return _clean_none_values(out)
    if not isinstance(raw, str):
        return raw

    text = raw.strip()
    if not text:
        return ""
    if text.startswith(("http://", "https://", "data:image/")):
        return text
    try:
        path = Path(text).expanduser()
        if not path.is_absolute():
            path = Path.cwd() / path
        if path.is_file():
            mime = mimetypes.guess_type(str(path))[0] or "image/png"
            encoded = base64.b64encode(path.read_bytes()).decode("ascii")
            return f"data:{mime};base64,{encoded}"
    except Exception:
        pass
    return text


def _as_list(value: Any) -> list[Any]:
    if value is None or value is False:
        return []
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    return [value]


def _truthy_config(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in {"", "0", "false", "no", "off"}
    return bool(value)


def _first_present(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, "", [], {}):
            return mapping.get(key)
    return None


def _collect_xianyun_vibe_candidates(
    patched_comment: dict[str, Any] | None,
    payload_info: dict[str, Any],
    body: dict[str, Any],
) -> list[Any]:
    params = body.get("parameters") or {}
    candidates: list[Any] = []
    sources = [patched_comment or {}, payload_info, params]
    config_keys = (
        "xianyun_vibe",
        "xianyun_vibe_transfer",
        "vibe_transfer",
        "vibeTransfer",
        "vibe",
        "character_transfer",
        "characterTransfer",
    )
    for source in sources:
        if not isinstance(source, dict):
            continue
        for key in config_keys:
            value = source.get(key)
            if value not in (None, "", False, [], {}):
                candidates.append(value)

        nai_images = _first_present(
            source,
            (
                "reference_image_multiple",
                "reference_images",
                "referenceImages",
                "reference_image",
                "referenceImage",
            ),
        )
        nai_strength = _first_present(
            source,
            ("reference_strength_multiple", "reference_strength", "referenceStrength"),
        )
        if nai_images is not None or nai_strength is not None:
            candidates.append(
                {
                    "images": nai_images,
                    "strength": nai_strength,
                    "information_extracted": _first_present(
                        source,
                        (
                            "reference_information_extracted_multiple",
                            "information_extracted",
                            "informationExtracted",
                        ),
                    ),
                }
            )
    return candidates


def _normalize_xianyun_vibe_config(config: Any) -> dict[str, Any]:
    if isinstance(config, str):
        config = {"images": [config]}
    elif isinstance(config, list):
        config = {"images": config}
    elif not isinstance(config, dict):
        return {}

    if "enabled" in config and not _truthy_config(config.get("enabled")):
        return {}

    raw_images = _first_present(
        config,
        (
            "images",
            "image",
            "image_url",
            "image_urls",
            "image_base64",
            "reference_image",
            "reference_images",
            "referenceImage",
            "referenceImages",
            "reference_image_multiple",
            "vibe_image",
            "vibe_images",
            "path",
        ),
    )
    references = [
        _read_image_reference(item)
        for item in _as_list(raw_images)
        if item not in (None, "", False)
    ]
    if not references:
        return {}

    strength = _first_present(
        config,
        (
            "strength",
            "reference_strength",
            "referenceStrength",
            "reference_strength_multiple",
            "character_strength",
            "characterStrength",
        ),
    )
    if strength is None:
        strength = 0.6
    strengths = _as_list(strength)
    if references and len(strengths) == 1:
        strengths = strengths * len(references)

    info = _first_present(
        config,
        (
            "information_extracted",
            "informationExtracted",
            "reference_information_extracted",
            "reference_information_extracted_multiple",
        ),
    )
    if info is None:
        info = 1.0
    information = _as_list(info)
    if references and len(information) == 1:
        information = information * len(references)

    payload = {
        "enabled": True,
        "images": references,
        "strength": strength,
        "reference_image_multiple": references,
        "reference_strength_multiple": strengths,
        "reference_information_extracted_multiple": information,
    }
    for key in (
        "mode",
        "preset",
        "model",
        "mask",
        "mask_image",
        "maskImage",
        "character_id",
        "characterId",
    ):
        if key in config and config.get(key) not in (None, "", [], {}):
            payload[key] = _read_image_reference(config.get(key))
    return _clean_none_values(payload)


def _xianyun_vibe_payload(
    patched_comment: dict[str, Any] | None,
    payload_info: dict[str, Any],
    body: dict[str, Any],
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for candidate in _collect_xianyun_vibe_candidates(patched_comment, payload_info, body):
        vibe = _normalize_xianyun_vibe_config(candidate)
        if not vibe:
            continue
        merged.update(vibe)

    if not merged:
        return {}

    references = merged.get("reference_image_multiple") or merged.get("images") or []
    strengths = merged.get("reference_strength_multiple") or []
    information = merged.get("reference_information_extracted_multiple") or []
    transfer = _clean_none_values(
        {
            "enabled": True,
            "images": references,
            "reference_image_multiple": references,
            "reference_strength_multiple": strengths,
            "reference_information_extracted_multiple": information,
            "strength": merged.get("strength"),
        }
    )
    return _clean_none_values(
        {
            "reference_image_multiple": references,
            "reference_strength_multiple": strengths,
            "reference_information_extracted_multiple": information,
            "reference_images": references,
            "referenceImages": references,
            "vibe_transfer": transfer,
            "vibeTransfer": transfer,
            "character_transfer": transfer,
            "characterTransfer": transfer,
        }
    )


def _xianyun_raw_extra(patched_comment: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(patched_comment, dict):
        return {}
    extra: dict[str, Any] = {}
    for key in ("xianyun_extra", "xianyun_payload", "xianyun_request"):
        value = patched_comment.get(key)
        if isinstance(value, dict):
            extra.update(value)
    return _clean_none_values(extra)


def _xianyun_body_from_payload(
    payload_info: dict[str, Any],
    body: dict[str, Any],
    patched_comment: dict[str, Any] | None = None,
) -> dict[str, Any]:
    params = body.get("parameters") or {}
    negative = str(
        params.get("negative_prompt")
        or params.get("uc")
        or ""
    )
    seed = params.get("seed")
    if seed is None:
        seed = random.randint(0, 999_999_999)
    req = {
        "model": body.get("model") or payload_info.get("model") or "nai-diffusion-4-full",
        "positivePrompt": body.get("input") or "",
        "negativePrompt": negative,
        "scale": float(params.get("scale") or 5),
        "steps": int(params.get("steps") or payload_info.get("steps") or 28),
        "width": int(params.get("width") or payload_info.get("width") or 832),
        "height": int(params.get("height") or payload_info.get("height") or 1216),
        "promptGuidanceRescale": float(params.get("cfg_rescale") or 0),
        "noise_schedule": params.get("noise_schedule") or "karras",
        "seed": str(seed),
        "sampler": params.get("sampler") or "k_euler_ancestral",
        "sm": bool(params.get("sm") or False),
        "sm_dyn": bool(params.get("sm_dyn") or False),
        "decrisp": False,
        "variety": False,
        "v4_prompt_char_captions": (
            ((params.get("v4_prompt") or {}).get("caption") or {}).get("char_captions")
            if isinstance(params.get("v4_prompt"), dict)
            else None
        ),
        "v4_negative_prompt_char_captions": (
            ((params.get("v4_negative_prompt") or {}).get("caption") or {}).get("char_captions")
            if isinstance(params.get("v4_negative_prompt"), dict)
            else None
        ),
        "use_coords": bool(
            (params.get("v4_prompt") or {}).get("use_coords", True)
            if isinstance(params.get("v4_prompt"), dict)
            else True
        ),
    }
    req.update(_xianyun_vibe_payload(patched_comment, payload_info, body))
    req.update(_xianyun_raw_extra(patched_comment))
    return req


async def _download_image_url(client: httpx.AsyncClient, image_url: str) -> bytes:
    from network_safety import validate_image_download_url

    safe_url = validate_image_download_url(image_url)
    resp = await client.get(
        safe_url,
        headers={
            "User-Agent": _xianyun_headers("placeholder")["User-Agent"],
            "Referer": "https://nai3.idlecloud.cc/",
        },
        follow_redirects=False,
    )
    resp.raise_for_status()
    return resp.content


def _retry_after_seconds(response: httpx.Response) -> float:
    raw = str((response.headers or {}).get("Retry-After") or "").strip()
    if not raw:
        return 0.0
    try:
        return max(0.0, float(raw))
    except (TypeError, ValueError):
        return 0.0


def _raise_pre_request_transport_error(exc: BaseException) -> None:
    raise GenerationProviderError(
        f"TLS/connect failed before request was sent: {exc}",
        retry_safe=True,
        billing_uncertain=False,
        request_attempted=False,
        error_code="connect_failed",
    ) from exc


async def _generate_novelai_png(
    client: httpx.AsyncClient,
    token_entry: dict[str, Any],
    body: dict[str, Any],
) -> bytes:
    try:
        resp = await client.post(
            f"{IMAGE_API_BASE}/ai/generate-image",
            headers=_auth_headers(str(token_entry.get("token") or "")),
            json=body,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        _raise_pre_request_transport_error(exc)
    except httpx.TimeoutException as exc:
        raise GenerationProviderError(
            f"NAI request timed out after send: {exc}",
            retry_safe=False,
            billing_uncertain=True,
            request_attempted=True,
            error_code="billing_uncertain",
        ) from exc
    if resp.status_code == 401:
        raise GenerationProviderError(
            "Token invalid or expired",
            retry_safe=True,
            billing_uncertain=False,
            request_attempted=True,
            error_code="provider_unavailable",
        )
    if resp.status_code == 429:
        wait = _retry_after_seconds(resp)
        raise GenerationProviderError(
            "Request too frequent; please retry later",
            retry_safe=True,
            billing_uncertain=False,
            request_attempted=False,
            wait=wait,
            error_code="rate_limited",
        )
    if resp.status_code >= 500:
        raise GenerationProviderError(
            f"NAI API error {resp.status_code}: {resp.text[:500]}",
            retry_safe=False,
            billing_uncertain=True,
            request_attempted=True,
            error_code="http_5xx",
        )
    if resp.status_code >= 400:
        text = resp.text[:500]
        raise GenerationProviderError(
            f"NAI API error {resp.status_code}: {text}",
            retry_safe=True,
            billing_uncertain=False,
            request_attempted=True,
            error_code="generate_failed",
        )
    return _extract_png_from_zip(resp.content)



def novelai_director_status() -> dict[str, Any]:
    """Report local Director readiness without contacting the paid provider."""

    entries = [
        entry
        for entry in _enabled_token_entries()
        if _provider_key(str(entry.get("provider") or "")) == PROVIDER_NOVELAI
    ]
    return {
        "available": bool(entries),
        "slot_count": len(entries),
        "verified": False,
        "verified_slot_count": 0,
        "provider": PROVIDER_NOVELAI,
        "endpoint": f"{IMAGE_API_BASE}/ai/augment-image",
        "slots": [_public_token_entry(entry) for entry in entries],
        "message": (
            "NovelAI Director slot configured"
            if entries
            else "NovelAI token is not configured for Director"
        ),
    }



async def call_nai_director(
    *,
    request: dict[str, Any],
    provenance: dict[str, Any] | None = None,
    token_id: str = "",
) -> dict[str, Any]:
    """Call NovelAI Director once with one fixed slot and bounded ZIP handling.

    Director requests can be billable.  A request that reached the provider is
    never failed over to another slot and is never automatically retried.
    """

    candidates = [
        entry
        for entry in _candidate_token_entries(
            _select_token_entry(token_id) if token_id else None
        )
        if _provider_key(str(entry.get("provider") or "")) == PROVIDER_NOVELAI
    ]
    if not candidates:
        return {
            "ok": False,
            "error": "missing_token",
            "message": "NovelAI token is not configured for Director",
            "outputs": [],
            "retry_safe": True,
            "billing_uncertain": False,
        }

    entry = candidates[0]
    slot_id = str(entry.get("id") or "")
    slot_label = str(entry.get("label") or slot_id)
    lock = _lock_for_token(slot_id)
    started_request = False
    async with lock:
        wait = _cooldown_wait(slot_id, entry)
        if wait > 0:
            await asyncio.sleep(wait)
        _set_active_job(
            slot_id,
            {
                "token_id": slot_id,
                "token_label": slot_label,
                "provider": PROVIDER_NOVELAI,
                "status": "running",
                "message": f"{slot_label} running NovelAI Director",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "work_id": None,
                "kind": "director",
            },
        )
        try:
            timeout = httpx.Timeout(240.0, connect=20.0)
            async with httpx.AsyncClient(timeout=timeout) as client:
                started_request = True
                async with client.stream(
                    "POST",
                    f"{IMAGE_API_BASE}/ai/augment-image",
                    headers=_auth_headers(str(entry.get("token") or "")),
                    json=dict(request or {}),
                ) as response:
                    status = int(response.status_code)
                    content_length_raw = str(
                        (response.headers or {}).get("content-length") or ""
                    ).strip()
                    if content_length_raw:
                        try:
                            content_length = int(content_length_raw)
                        except ValueError:
                            content_length = 0
                        if content_length > DIRECTOR_RESPONSE_MAX_BYTES:
                            message = (
                                "NovelAI Director response exceeds the safe response limit"
                            )
                            _record_token_failure(entry, message)
                            return {
                                "ok": False,
                                "error": "response_too_large",
                                "message": message,
                                "outputs": [],
                                "retry_safe": False,
                                "billing_uncertain": True,
                                "token_id": slot_id,
                                "token_label": slot_label,
                            }

                    body = bytearray()
                    async for chunk in response.aiter_bytes():
                        body.extend(chunk)
                        if len(body) > DIRECTOR_RESPONSE_MAX_BYTES:
                            message = (
                                "NovelAI Director response exceeds the safe response limit"
                            )
                            _record_token_failure(entry, message)
                            return {
                                "ok": False,
                                "error": "response_too_large",
                                "message": message,
                                "outputs": [],
                                "retry_safe": False,
                                "billing_uncertain": True,
                                "token_id": slot_id,
                                "token_label": slot_label,
                            }

                    if status >= 400:
                        text = bytes(body).decode("utf-8", errors="replace")[:500]
                        message = f"NAI Director API error {status}: {text}"
                        _record_token_failure(entry, message)
                        definite_rejection = 400 <= status < 500
                        return {
                            "ok": False,
                            "error": (
                                "director_rejected"
                                if definite_rejection
                                else "director_failed"
                            ),
                            "message": message,
                            "outputs": [],
                            "retry_safe": definite_rejection,
                            "billing_uncertain": not definite_rejection,
                            "token_id": slot_id,
                            "token_label": slot_label,
                        }

            outputs = _extract_pngs_from_zip(bytes(body))
            _clear_token_failure(entry)
            return {
                "ok": True,
                "message": "NovelAI Director completed",
                "outputs": outputs,
                "usage": {
                    "anlas_spent": None,
                    "cost_source": "unknown",
                },
                "provenance": copy.deepcopy(provenance or {}),
                "token_id": slot_id,
                "token_label": slot_label,
                "provider": PROVIDER_NOVELAI,
                "retry_safe": False,
                "billing_uncertain": False,
            }
        except Exception as exc:
            message = f"NovelAI Director request failed: {exc}"
            _record_token_failure(entry, message)
            return {
                "ok": False,
                "error": "director_failed",
                "message": message,
                "outputs": [],
                "retry_safe": False,
                "billing_uncertain": bool(started_request),
                "token_id": slot_id,
                "token_label": slot_label,
            }
        finally:
            if started_request:
                _LAST_GEN_AT_BY_TOKEN[slot_id] = time.time()
            _clear_active_job(slot_id)


async def _generate_xianyun_png(
    client: httpx.AsyncClient,
    token_entry: dict[str, Any],
    payload_info: dict[str, Any],
    body: dict[str, Any],
    *,
    patched_comment: dict[str, Any] | None = None,
    slot_id: str,
    slot_label: str,
    work_id: int | None,
) -> bytes:
    api_base = str(token_entry.get("api_base") or XIANYUN_API_BASE).rstrip("/")
    req_body = _clean_none_values(_xianyun_body_from_payload(payload_info, body, patched_comment))
    try:
        submit = await client.post(
            f"{api_base}/generate_image",
            headers=_xianyun_headers(str(token_entry.get("token") or "")),
            json=req_body,
        )
    except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout) as exc:
        _raise_pre_request_transport_error(exc)
    except httpx.TimeoutException as exc:
        raise GenerationProviderError(
            f"Xianyun request timed out after send: {exc}",
            retry_safe=False,
            billing_uncertain=True,
            request_attempted=True,
            error_code="billing_uncertain",
        ) from exc
    if submit.status_code == 401:
        raise GenerationProviderError(
            "Xianyun API key invalid or expired",
            retry_safe=True,
            billing_uncertain=False,
            request_attempted=True,
            error_code="provider_unavailable",
        )
    if submit.status_code == 403:
        raise GenerationProviderError(
            f"Xianyun account forbidden or banned: {submit.text[:300]}",
            retry_safe=True,
            billing_uncertain=False,
            request_attempted=True,
            error_code="generate_failed",
        )
    if submit.status_code == 429:
        wait = _retry_after_seconds(submit)
        raise GenerationProviderError(
            f"Xianyun request too frequent: {submit.text[:300]}",
            retry_safe=True,
            billing_uncertain=False,
            request_attempted=False,
            wait=wait,
            error_code="rate_limited",
        )
    if submit.status_code >= 500:
        raise GenerationProviderError(
            f"Xianyun API error {submit.status_code}: {submit.text[:500]}",
            retry_safe=False,
            billing_uncertain=True,
            request_attempted=True,
            error_code="http_5xx",
        )
    if submit.status_code >= 400:
        raise GenerationProviderError(
            f"Xianyun API error {submit.status_code}: {submit.text[:500]}",
            retry_safe=True,
            billing_uncertain=False,
            request_attempted=True,
            error_code="generate_failed",
        )
    data = submit.json()
    job_id = str(data.get("job_id") or "")
    if not job_id:
        raise ValueError(f"Xianyun response missing job_id: {str(data)[:300]}")

    deadline = time.time() + _XIANYUN_TIMEOUT_SEC
    queue_position = data.get("queue_position")
    while time.time() < deadline:
        _set_active_job(
            slot_id,
            {
                "token_id": slot_id,
                "token_label": slot_label,
                "provider": PROVIDER_XIANYUN,
                "status": "queued" if queue_position else "running",
                "message": f"{slot_label} Xianyun job {job_id[:8]} polling",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "work_id": work_id,
                "remote_job_id": job_id,
                "queue_position": queue_position,
            },
        )
        await asyncio.sleep(_XIANYUN_POLL_INTERVAL_SEC)
        poll = await client.get(
            f"{api_base}/get_result/{quote(job_id, safe='')}",
            headers=_xianyun_headers(str(token_entry.get("token") or "")),
        )
        if poll.status_code == 429:
            await asyncio.sleep(_XIANYUN_POLL_INTERVAL_SEC)
            continue
        if poll.status_code >= 400:
            raise GenerationProviderError(
                f"Xianyun result error {poll.status_code}: {poll.text[:500]}",
                retry_safe=False,
                billing_uncertain=True,
            )
        result = poll.json()
        status = str(result.get("status") or "")
        queue_position = result.get("queue_position")
        if status == "completed":
            if result.get("image_base64"):
                raw = str(result["image_base64"])
                if "," in raw and raw.lower().startswith("data:image"):
                    raw = raw.split(",", 1)[1]
                return base64.b64decode(raw)
            image_url = str(result.get("image_url") or "")
            if not image_url:
                raise ValueError(f"Xianyun completed without image_url: {str(result)[:300]}")
            return await _download_image_url(client, image_url)
        if status == "failed":
            raise GenerationProviderError(
                f"Xianyun generation failed: {str(result)[:500]}",
                retry_safe=False,
                billing_uncertain=True,
            )
    raise TimeoutError("Xianyun generation timed out")


async def generate_image(
    patched_comment: dict[str, Any],
    *,
    work_id: int | None = None,
    source_gallery_id: str = "site",
    force_free: bool = True,
    prompt_profile: str = "native",
    token_id: str = "",
    wait_for_slot: bool = False,
    generation_series_id: str = "",
    source_title: str = "",
    source_thumb: str = "",
    remote_work_id: str = "",
) -> dict[str, Any]:
    if _JOB.get("status") == "error":
        _JOB.update({"status": "idle", "message": "idle"})

    try:
        if token_id:
            token_entry = _select_token_entry(token_id)
        elif wait_for_slot:
            token_entry = _next_token_entry()
        else:
            token_entry, blocked_reason, wait, _provider = _pick_available_token()
            if token_entry is None:
                if blocked_reason == "cooldown":
                    return {
                        "ok": False,
                        "error": "cooldown",
                        "message": f"NAI token pool cooling down; retry in {round(wait, 1)}s",
                        "queue": queue_status(),
                        "provider": _provider,
                        "wait": wait,
                    }
                return {
                    "ok": False,
                    "error": "busy",
                    "message": "No idle NAI token slot; please wait",
                    "queue": queue_status(),
                    "provider": _provider,
                }
    except ValueError as exc:
        return {
            "ok": False,
            "error": "missing_token",
            "message": str(exc),
            "queue": queue_status(),
        }

    profiled_comment, profile_info = apply_prompt_profile_to_comment(
        patched_comment,
        prompt_profile,
    )
    payload_info = build_generate_payload(profiled_comment, force_free=force_free)
    params = {
        k: v
        for k, v in (payload_info["parameters"] or {}).items()
        if v is not None
    }
    body = {
        "input": payload_info["input"],
        "model": payload_info["model"],
        "action": payload_info["action"],
        "parameters": params,
        # 本项目只使用 NovelAI 会员号（Opus/Scroll/Tablet）。会员免费标准
        # 由 build_generate_payload 的尺寸/步数裁剪保证（≤1024×1024、≤28 steps
        # 单张生成不扣 Anlas）。因此始终走订阅通道（use_new_shared_trial=false）：
        # shared trial 是给未订阅账号的，且不完整支持 v4 char_caption，
        # 会导致发色/角色特征在生成时丢失。
        "use_new_shared_trial": False,
    }

    last_failure: dict[str, Any] | None = None
    for attempt_entry in _candidate_token_entries(token_entry):
        token_entry = attempt_entry
        slot_id = str(token_entry.get("id") or "")
        slot_label = str(token_entry.get("label") or slot_id)
        provider = _provider_key(str(token_entry.get("provider") or PROVIDER_NOVELAI))
        with _ACTIVE_GEN_SLOTS_GUARD:
            busy = slot_id in _ACTIVE_GEN_SLOTS
            if busy and not wait_for_slot:
                last_failure = {
                    "ok": False,
                    "error": "busy",
                    "message": f"{slot_label} is busy",
                    "queue": queue_status(),
                }
                continue
            _ACTIVE_GEN_SLOTS.add(slot_id)
        try:
            result = await _generate_image_with_entry(
                token_entry,
                profiled_comment,
                profile_info,
                payload_info,
                body,
                work_id=work_id,
                source_gallery_id=source_gallery_id,
                wait_for_slot=wait_for_slot,
                generation_series_id=generation_series_id,
                source_title=source_title,
                source_thumb=source_thumb,
                remote_work_id=remote_work_id,
            )
        finally:
            with _ACTIVE_GEN_SLOTS_GUARD:
                _ACTIVE_GEN_SLOTS.discard(slot_id)
        if result.get("ok"):
            usage = result.get("usage")
            if not isinstance(usage, dict):
                usage = {
                    "anlas_spent": None,
                    "cost_source": "unknown",
                }
                result["usage"] = usage
            record_usage(
                kind="image_generation",
                provider=str(result.get("provider") or provider),
                model=str(result.get("model") or payload_info.get("model") or ""),
                images=1,
                anlas_spent=usage.get("anlas_spent"),
                cost_source=str(usage.get("cost_source") or "unknown"),
            )
            return result
        last_failure = result
        if not result.get("provider"):
            result["provider"] = provider
        # Paid POST: only fail over when the request was not billable
        # (invalid token / pre-request TLS). HTTP 5xx must not hop slots.
        if (
            result.get("retry_safe")
            and not result.get("billing_uncertain")
            and result.get("error") == "provider_unavailable"
        ):
            continue
        return result

    return last_failure or {
        "ok": False,
        "error": "missing_token",
        "message": "No usable generation provider is available",
        "queue": queue_status(),
    }


async def _generate_image_with_entry(
    token_entry: dict[str, Any],
    profiled_comment: dict[str, Any],
    profile_info: dict[str, Any],
    payload_info: dict[str, Any],
    body: dict[str, Any],
    *,
    work_id: int | None,
    source_gallery_id: str = "site",
    wait_for_slot: bool,
    generation_series_id: str = "",
    source_title: str = "",
    source_thumb: str = "",
    remote_work_id: str = "",
) -> dict[str, Any]:
    slot_id = str(token_entry.get("id") or "")
    slot_label = str(token_entry.get("label") or slot_id)
    provider = _provider_key(str(token_entry.get("provider") or PROVIDER_NOVELAI))
    lock = _lock_for_token(slot_id)
    request_started = False
    async with lock:
        wait = _cooldown_wait(slot_id, token_entry)
        if wait > 0 and not wait_for_slot:
            return {
                "ok": False,
                "error": "cooldown",
                "message": f"{slot_label} cooling down; retry in {round(wait, 1)}s",
                "queue": queue_status(),
                "provider": provider,
                "wait": wait,
            }
        if wait > 0:
            _set_active_job(
                slot_id,
                {
                    "token_id": slot_id,
                    "token_label": slot_label,
                    "provider": provider,
                    "status": "cooldown",
                    "message": f"{slot_label} cooldown {round(wait, 1)}s",
                    "started_at": datetime.now().isoformat(timespec="seconds"),
                    "work_id": work_id,
                },
            )
            await asyncio.sleep(wait)

        _set_active_job(
            slot_id,
            {
                "token_id": slot_id,
                "token_label": slot_label,
                "provider": provider,
                "status": "running",
                "message": f"{slot_label} requesting {_provider_label(provider)}...",
                "started_at": datetime.now().isoformat(timespec="seconds"),
                "work_id": work_id,
            },
        )
        try:
            client_kwargs: dict[str, Any] = {"timeout": 300.0}
            if token_entry.get("proxy"):
                client_kwargs["proxy"] = str(token_entry.get("proxy") or "")
            async with httpx.AsyncClient(**client_kwargs) as client:
                if provider == PROVIDER_XIANYUN:
                    png_bytes = await _generate_xianyun_png(
                        client,
                        token_entry,
                        payload_info,
                        body,
                        patched_comment=profiled_comment,
                        slot_id=slot_id,
                        slot_label=slot_label,
                        work_id=work_id,
                    )
                else:
                    png_bytes = await _generate_novelai_png(client, token_entry, body)
            GENERATED_DIR.mkdir(parents=True, exist_ok=True)
            filename = _reserve_generated_filename(work_id)
            out_path = GENERATED_DIR / filename
            try:
                from atomic_io import atomic_write_bytes

                atomic_write_bytes(out_path, png_bytes)
            except Exception:
                out_path.write_bytes(png_bytes)
            finally:
                _release_generated_filename(filename)
            register_warning = ""
            try:
                aitag_meta = (
                    profiled_comment.get("_aitag_source")
                    if isinstance(profiled_comment, dict)
                    else None
                )
                if not isinstance(aitag_meta, dict):
                    aitag_meta = {}
                title = str(
                    source_title or aitag_meta.get("title") or ""
                ).strip()
                thumb = str(
                    source_thumb or aitag_meta.get("thumb") or ""
                ).strip()
                remote_id = str(
                    remote_work_id or aitag_meta.get("work_id") or work_id or ""
                ).strip()
                register_generated(
                    filename,
                    work_id=work_id,
                    source_gallery_id=source_gallery_id,
                    model=str(payload_info.get("model") or ""),
                    width=payload_info.get("width"),
                    height=payload_info.get("height"),
                    steps=payload_info.get("steps"),
                    free_eligible=payload_info.get("free_eligible"),
                    prompt_snapshot={
                        **prompt_snapshot_from_comment(profiled_comment),
                        "prompt_profile": profile_info,
                        "generation_provider": provider,
                    },
                    generation_series_id=generation_series_id,
                    source_title=title,
                    source_thumb=thumb,
                    remote_work_id=remote_id,
                )
            except Exception as exc:
                register_warning = f"metadata registration failed: {exc}"

            try:
                from post_pipeline import load_config as load_pipe_config
                from post_pipeline import schedule_auto_pipeline

                schedule_auto_pipeline(filename)
                pipe_cfg = load_pipe_config()
                pipeline_queued = bool(pipe_cfg.get("auto_after_generate"))
            except Exception:
                pipeline_queued = False

            _LAST_GEN_AT_BY_TOKEN[slot_id] = time.time()
            # Must match register_generated / generated_gallery._group_key
            # (non-site galleries use gallery:{id}:{base}).
            group_id = _group_key(
                work_id,
                source_gallery_id=str(source_gallery_id or "site"),
                generation_series_id=str(generation_series_id or "").strip(),
            )
            result = {
                "ok": True,
                "message": "Image generated",
                "image_url": f"/data/generated/{filename}",
                "filename": filename,
                "group_id": group_id,
                "gallery_url": f"/generated?g={group_id}",
                "free_eligible": payload_info.get("free_eligible"),
                "resized_for_free": payload_info.get("resized_for_free"),
                "width": payload_info.get("width"),
                "height": payload_info.get("height"),
                "steps": payload_info.get("steps"),
                "model": payload_info.get("model"),
                "provider": provider,
                "source_gallery_id": str(source_gallery_id or "site"),
                "prompt_profile": profile_info,
                "token_id": slot_id,
                "token_label": slot_label,
                "pool_concurrency": generation_concurrency(),
                "usage": {
                    "anlas_spent": None,
                    "cost_source": "unknown",
                },
                "request_attempted": True,
                "retry_safe": False,
                "billing_uncertain": False,
            }
            if pipeline_queued:
                result["pipeline_queued"] = True
                result["pipeline_message"] = "Post pipeline queued"
            if register_warning:
                result["register_warning"] = register_warning
            _clear_token_failure(token_entry)
            _clear_active_job(slot_id, result=result)
            return result
        except Exception as exc:
            message = _exception_message(exc)
            failed_provider = _record_token_failure(token_entry, message)
            _clear_active_job(slot_id, error=message)
            wait = 0.0
            error_code = ""
            if isinstance(exc, GenerationProviderError):
                retry_safe = exc.retry_safe
                billing_uncertain = exc.billing_uncertain
                request_started = bool(exc.request_attempted)
                wait = float(exc.wait or 0.0)
                error_code = str(exc.error_code or "")
            elif isinstance(
                exc,
                (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout),
            ):
                retry_safe = True
                billing_uncertain = False
                request_started = False
                error_code = "connect_failed"
            else:
                retry_safe = False
                billing_uncertain = True
                request_started = True
                error_code = "billing_uncertain"
            if not error_code:
                error_code = (
                    "billing_uncertain"
                    if billing_uncertain
                    else "provider_unavailable" if failed_provider else "generate_failed"
                )
            result = {
                "ok": False,
                "error": error_code,
                "message": message,
                "token_id": slot_id,
                "token_label": slot_label,
                "provider": provider,
                "fallback_available": bool(failed_provider and retry_safe and not billing_uncertain),
                "request_attempted": bool(request_started),
                "retry_safe": bool(retry_safe),
                "billing_uncertain": bool(billing_uncertain),
                "queue": queue_status(),
            }
            if wait > 0:
                result["wait"] = wait
            return result
        finally:
            # 安全清理：如果 slot 仍然在 _ACTIVE_JOBS 中（return 前未清除），
            # 说明是异常退出或中途返回遗漏了清理。正常路径已在 _clear_active_job 中移除。
            if slot_id in _ACTIVE_JOBS:
                _clear_active_job(slot_id)



def _extract_pngs_from_zip(data: bytes) -> list[dict[str, Any]]:
    """Extract every bounded, structurally valid PNG from a Director response."""

    outputs: list[dict[str, Any]] = []
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = [
                info
                for info in zf.infolist()
                if not info.is_dir() and info.filename.lower().endswith(".png")
            ]
            if not infos:
                raise ValueError("NovelAI response zip did not contain a PNG")
            if len(infos) > DIRECTOR_ZIP_MAX_ENTRIES:
                raise ValueError("NovelAI Director response contains too many PNG entries")
            total_size = sum(max(0, int(info.file_size)) for info in infos)
            if total_size > DIRECTOR_RESPONSE_MAX_BYTES:
                raise ValueError("NovelAI Director PNG output exceeds the safe response limit")

            for info in infos:
                if int(info.file_size) > DIRECTOR_OUTPUT_MAX_BYTES:
                    raise ValueError("NovelAI Director PNG exceeds the per-output limit")
                raw = zf.read(info)
                if (
                    len(raw) > DIRECTOR_OUTPUT_MAX_BYTES
                    or not raw.startswith(b"\x89PNG\r\n\x1a\n")
                ):
                    raise ValueError(f"invalid PNG in Director response: {info.filename}")
                try:
                    with Image.open(io.BytesIO(raw)) as image:
                        if image.format != "PNG":
                            raise ValueError(
                                f"invalid PNG in Director response: {info.filename}"
                            )
                        width, height = image.size
                        image.verify()
                    if (
                        width <= 0
                        or height <= 0
                        or width * height > DIRECTOR_OUTPUT_MAX_PIXELS
                    ):
                        raise ValueError(
                            f"invalid PNG dimensions in Director response: {info.filename}"
                        )
                except (UnidentifiedImageError, OSError, SyntaxError) as exc:
                    raise ValueError(
                        f"invalid PNG in Director response: {info.filename}"
                    ) from exc
                outputs.append(
                    {
                        "archive_name": Path(info.filename).name,
                        "bytes": raw,
                        "width": int(width),
                        "height": int(height),
                    }
                )
    except zipfile.BadZipFile as exc:
        raise ValueError("NovelAI Director response is not a valid ZIP") from exc
    return outputs


def _extract_png_from_zip(data: bytes) -> bytes:
    """Extract one provider image while retaining the legacy generation contract.

    Director responses use ``_extract_pngs_from_zip`` and receive full Pillow
    verification.  The regular generation endpoint historically accepted valid
    PNG-framed payloads from NovelAI even when ancillary chunk checksums were
    non-canonical, so keep that compatibility path bounded but signature-based.
    """

    try:
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            infos = [
                info
                for info in zf.infolist()
                if not info.is_dir() and info.filename.lower().endswith(".png")
            ]
            if not infos:
                raise ValueError("NovelAI response zip did not contain a PNG")
            info = infos[0]
            if int(info.file_size) > DIRECTOR_OUTPUT_MAX_BYTES:
                raise ValueError("NovelAI PNG exceeds the safe response limit")
            raw = zf.read(info)
            if (
                len(raw) > DIRECTOR_OUTPUT_MAX_BYTES
                or not raw.startswith(b"\x89PNG\r\n\x1a\n")
            ):
                raise ValueError("NovelAI response did not contain a valid PNG payload")
            return raw
    except zipfile.BadZipFile as exc:
        raise ValueError("NovelAI response is not a valid ZIP") from exc
