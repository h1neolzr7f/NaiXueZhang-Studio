"""起号流水线配置域：路径常量、AI 预设、密钥读写、配置加载/保存（从 pixiv_launch.py 拆出）。"""

from __future__ import annotations

import json
import logging
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from atomic_io import atomic_write_text
from local_secrets import protect_secret, unprotect_secret
from pixiv_accounts import (
    accounts_auth_status,
    get_active_account_id,
    pixiv_api_headers,
)
from post_pipeline import discover_anr_root


ROOT = Path(__file__).resolve().parent
from paths import data_dir as _config_data_dir

_logger = logging.getLogger(__name__)

DATA_DIR = _config_data_dir()
GENERATED_DIR = DATA_DIR / "generated"
CONFIG_PATH = DATA_DIR / "pixiv_launch.json"
SECRET_PATH = DATA_DIR / "pixiv.local.json"
HISTORY_PATH = DATA_DIR / "pixiv_uploads.json"
DRAFT_PATH = DATA_DIR / "pixiv_draft.json"
PREPARED_PATH = DATA_DIR / "pixiv_prepared_submission.json"
PREPARED_ARCHIVE_DIR = DATA_DIR / "pixiv_prepared"
LAST_JOB_PATH = DATA_DIR / "pixiv_last_job_request.json"
_PREPARED_LOCK = threading.RLock()
_HISTORY_LOCK = threading.RLock()

PIXIV_API_BASE = "https://app-api.pixiv.net"
PIXIV_MAX_TAGS = 10

AI_PROVIDER_PRESETS: dict[str, dict[str, str]] = {
    "自定义 OpenAI-compatible": {"base": "", "model": "", "note": "任意 /v1 Chat Completions 接口"},
    "Grok 中转站（用户自备）": {
        "base": "https://sub.sixoner.com/v1",
        "model": "",
        "note": "OpenAI 兼容；保存 Key 后读取模型。仅用于服务条款允许的测试场景。",
    },
    "硅基流动 SiliconFlow": {
        "base": "https://api.siliconflow.cn/v1",
        "model": "deepseek-ai/DeepSeek-V3.2",
        "note": "硅基流动官方接口",
    },
    "硅基流动 · 视觉反推": {
        "base": "https://api.siliconflow.cn/v1",
        "model": "Qwen/Qwen2.5-VL-7B-Instruct",
        "note": "看图反推标题简介（civitai-post-splitter 同款思路）",
    },
    "DeepSeek": {
        "base": "https://api.deepseek.com/v1",
        "model": "deepseek-v4-flash",
        "note": "DeepSeek 官方接口（V4 Flash，非思考模式）",
    },
    "通义千问 DashScope": {
        "base": "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "model": "qwen-plus",
        "note": "阿里云百炼兼容模式",
    },
    "OpenRouter": {
        "base": "https://openrouter.ai/api/v1",
        "model": "deepseek/deepseek-chat",
        "note": "OpenRouter 聚合接口",
    },
    "MEOW · DeepSeek V4 Flash": {
        "base": "https://love.chenoodesu.online/v1",
        "model": "deepseek-v4-flash",
        "note": "MEOW 公益站，deepseek-v4-flash 文本导演",
    },
}

DEFAULTS: dict[str, Any] = {
    "ai": {
        "provider": "",
        "api_base": "",
        "model": "",
        "timeout": 120,
        "max_tokens": 2048,
    },
    "account": {
        "persona": {},
        "direction": "AI 生成图爱好者，分享 NovelAI 同人插画",
        "nickname_hint": "",
        "style": "温柔日常",
        "language": "zh",
        "default_tags": ["AI绘画", "二次元", "插画", "AIイラスト", "イラスト"],
        "nsfw_level": "sfw",
    },
    "upload": {
        "restrict": 0,
        "illust_type": 0,
        "ai_type": 1,
        "x_restrict": "r18",
        "browser_headless": False,
        "use_processed": True,
        "auto_pipeline": True,
        "auto_generate_copy": True,
        "visibility": "public",
    },
    "pipeline": {
        "anr_root": "",
        "only_missing": True,
        "upscale": {"enabled": True, "scale": 2},
        "mosaic": {
            "enabled": True,
            "method": "像素",
            "intensity": 24,
            "parts": ["欧金金", "欧芒果", "欧派派"],
        },
        "metadata": {
            "enabled": True,
            "custom_note": "",
            "custom_note_key": "pixiv-nai-gallery",
            "png_text": {},
        },
    },
}

_DEEPSEEK_DEFAULT_MODEL = "deepseek-v4-flash"
_DEEPSEEK_ALLOWED_MODELS = {"deepseek-v4-flash", "deepseek-v4-pro"}
_MODEL_TOKEN_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/+-]{1,160}$")
_STALE_AI_WARNING_MARKERS = (
    "The supported API model names are deepseek-v4-pro or deepseek-v4-flash",
    "but you passed 明日方舟",
    "but you passed 鏄庢棩鏂硅垷",
)


def _provider_preset(provider: str) -> dict[str, str]:
    provider = str(provider or "").strip()
    preset = AI_PROVIDER_PRESETS.get(provider)
    if isinstance(preset, dict):
        return preset
    if "DeepSeek" in provider:
        return AI_PROVIDER_PRESETS.get("DeepSeek") or {}
    return {}


def _looks_like_bad_model(value: str) -> bool:
    model = str(value or "").strip()
    if not model:
        return True
    if re.search(r"[\u4e00-\u9fff\u3040-\u30ff]", model):
        return True
    if re.search(r"\s", model):
        return True
    return not bool(_MODEL_TOKEN_RE.match(model))


def normalize_ai_config(ai_cfg: dict[str, Any] | None) -> dict[str, Any]:
    """Normalize OpenAI-compatible LLM config and repair common UI field drift."""
    raw = ai_cfg if isinstance(ai_cfg, dict) else {}
    ai = {**DEFAULTS["ai"], **raw}
    provider = str(ai.get("provider") or "").strip()
    preset = _provider_preset(provider)
    api_base = str(ai.get("api_base") or "").strip()
    model = str(ai.get("model") or "").strip()

    if not api_base and preset.get("base"):
        api_base = str(preset["base"]).strip()
    if not model and preset.get("model"):
        model = str(preset["model"]).strip()

    deepseek_like = "deepseek" in provider.lower() or "api.deepseek.com" in api_base.lower()
    if deepseek_like and model not in _DEEPSEEK_ALLOWED_MODELS:
        model = _DEEPSEEK_DEFAULT_MODEL
    elif _looks_like_bad_model(model):
        model = str(preset.get("model") or "").strip()

    ai["provider"] = provider
    ai["api_base"] = api_base
    ai["model"] = model
    ai["timeout"] = int(ai.get("timeout") or DEFAULTS["ai"]["timeout"])
    ai["max_tokens"] = int(ai.get("max_tokens") or DEFAULTS["ai"]["max_tokens"])
    return ai

PERSONA_SYSTEM = """你是 Pixiv AI 绘图爱好者账号起号顾问。
目标用户：使用 NovelAI / Stable Diffusion 等工具生成二次元图、在 Pixiv 分享作品的爱好者（不是职业手绘画师）。
根据账号方向，生成可执行的爱好者人设方案。
只返回一个 JSON 对象，不要 Markdown，不要解释。

JSON 字段：
account_name_suggestion, persona_summary, bio_template, voice_tone,
content_pillars, posting_rhythm, tag_strategy, hashtag_style, sample_greetings

硬规则：
- 人设是「AI 发图爱好者」：真诚、克制、像真实二次元爱好者，不要装成职业画师或工作室。
- bio_template 控制在 80 字以内，适合 Pixiv 简介，可提 AI 辅助创作但不过度营销。
- content_pillars 3-5 条：更新类型、题材偏好、与粉丝的互动风格。
- tag_strategy：中文为主 + 少量日文检索 tag，共最多 10 个（Pixiv 上限）。
- 面向中日双语受众，语气自然。
- 不要写露骨成人向起号建议。"""

POST_SYSTEM = """你是 Pixiv 投稿 AI 导演。
根据账号人设、生成图 tag/prompt（换角/改词/流水线后）和补充说明，完成 Pixiv 标注与投稿文案。
只返回一个 JSON 对象，不要 Markdown，不要解释。

JSON 字段：
title_ja, title_zh, caption_ja, caption_zh, tags, alt_titles

硬规则：
- 输入里的 source_tags / prompt_text 是「本张生成图」实际使用的 tag（已换角/改词），不是源站原图 tag；必须以此为准写标题与简介。
- 先分析 source_tags 与 prompt_text，tags 数组恰好不超过 10 个（Pixiv 硬上限），中文为主、日文为辅（约 6-7 个中文 + 3-4 个日文）：
  · 中文：角色中文名、作品中文名、画面氛围/构图/情绪（如 斯卡蒂、明日方舟、白发、温柔、日常）
  · 日文：保留 Pixiv 检索价值高的（如 女の子、AIイラスト、イラスト、アークナイツ、制服）；明日方舟角色务必带日文角色 tag，格式为 カタカナ名(アークナイツ)（如 プリースティス(アークナイツ)、テレジア(アークナイツ)），tag_hints_ja 里会有建议
  · 同一含义不要中英文日重复堆砌；tag_hints_zh / tag_hints_ja 可参考，但以本图实际咒语为准
  · 不要编造无关 tag，不要退回未修改的旧 tag
- title_ja：日文短标题，诗体感，≤20 字。
- title_zh：中文短标题，≤16 字。
- caption_ja：日文简介，2-4 句，语气克制。
- caption_zh：中文小作文，180-320 字，像真实画师发图，有画面感但不尬。
- alt_titles：2 个备选标题。
- 默认同人/二次元插画语境；根据 tag 判断角色与氛围，不要营销腔。
- 默认 SFW；只有 nsfw_level=nsfw 时才写成人向 tag/文案。"""

def _clean_stale_ai_warning(persona: dict[str, Any] | None) -> dict[str, Any]:
    """Drop cached fallback warnings from the old DeepSeek model-name bug."""
    if not isinstance(persona, dict):
        return {}
    out = dict(persona)
    warning = str(out.get("warning") or "")
    if warning and any(marker in warning for marker in _STALE_AI_WARNING_MARKERS):
        out.pop("warning", None)
    return out


def _read_secret() -> dict[str, Any]:
    if not SECRET_PATH.exists():
        return {}
    try:
        data = json.loads(SECRET_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _read_ai_secret() -> dict[str, Any]:
    path = DATA_DIR / "ai.local.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    stored_key = str(data.get("api_key") or "")
    if not stored_key:
        return data
    try:
        data["api_key"] = unprotect_secret(stored_key)
    except Exception:
        # 解密失败（例如换了 Windows 用户/机器导致 DPAPI 不可解）时保留
        # dpapi: 前缀密文，由调用方识别并给出明确报错，绝不把密文当密钥用。
        _logger.warning("AI 密钥解密失败（%s），请重新保存 API Key", path)
        return data
    if not stored_key.startswith("dpapi:v1:"):
        migrated = {**data, "api_key": protect_secret(str(data["api_key"]))}
        try:
            atomic_write_text(
                path,
                json.dumps(migrated, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
        except Exception as exc:
            _logger.warning("AI 密钥迁移写回失败（%s）: %s", path, exc)
    return data


def save_pixiv_token(
    refresh_token: str,
    *,
    label: str = "",
    account_id: str = "",
) -> dict[str, Any]:
    from pixiv_accounts import add_account, update_account_token

    refresh_token = str(refresh_token or "").strip()
    if not refresh_token:
        raise ValueError("refresh_token 不能为空")
    if account_id:
        return update_account_token(str(account_id), refresh_token)
    return add_account(refresh_token=refresh_token, label=label)


def save_ai_key(
    api_key: str, *, model: str | None = None, api_base: str | None = None
) -> dict[str, Any]:
    api_key = str(api_key or "").strip()
    if not api_key:
        raise ValueError("api_key 不能为空")
    path = DATA_DIR / "ai.local.json"
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    existing = _read_ai_secret()
    updates: dict[str, Any] = {
        "api_key": protect_secret(api_key),
        "updated_at": datetime.now().isoformat(timespec="seconds"),
    }
    if model is not None:
        updates["model"] = str(model).strip()
    if api_base is not None:
        updates["api_base"] = str(api_base).strip()
    existing.update(updates)
    atomic_write_text(
        path,
        json.dumps(existing, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return {"ok": True, "has_key": True, "updated_at": existing["updated_at"]}


def pixiv_auth_status() -> dict[str, Any]:
    return accounts_auth_status(get_active_account_id())


def ai_auth_status() -> dict[str, Any]:
    cfg = load_config()
    secret = _read_ai_secret()
    has_key = bool(secret.get("api_key"))
    ai_cfg = normalize_ai_config(cfg.get("ai") or {})
    return {
        "has_api_key": has_key,
        "provider": ai_cfg.get("provider", ""),
        "api_base": ai_cfg.get("api_base", ""),
        "model": ai_cfg.get("model", ""),
        "updated_at": secret.get("updated_at", ""),
    }


def load_config() -> dict[str, Any]:
    cfg = json.loads(json.dumps(DEFAULTS))
    if CONFIG_PATH.exists():
        try:
            raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                for key in ("ai", "account", "upload", "pipeline"):
                    if key in raw and isinstance(raw[key], dict):
                        cfg[key] = {**cfg.get(key, {}), **raw[key]}
                if "pipeline" in raw and isinstance(raw["pipeline"], dict):
                    pipe = raw["pipeline"]
                    if "anr_root" in pipe:
                        cfg["pipeline"]["anr_root"] = pipe["anr_root"]
                    for sub in ("upscale", "mosaic", "metadata"):
                        if sub in pipe and isinstance(pipe[sub], dict):
                            cfg["pipeline"][sub] = {
                                **cfg.get("pipeline", {}).get(sub, {}),
                                **pipe[sub],
                            }
                if "account" in raw and isinstance(raw["account"].get("persona"), dict):
                    cfg["account"]["persona"] = _clean_stale_ai_warning(
                        raw["account"]["persona"]
                    )
        except Exception as exc:
            _logger.warning(
                "起号配置 %s 已损坏，已回退到默认配置: %s", CONFIG_PATH, exc
            )
    cfg["ai"] = normalize_ai_config(cfg.get("ai") or {})
    cfg["account"]["persona"] = _clean_stale_ai_warning(
        (cfg.get("account") or {}).get("persona")
    )
    if not str((cfg.get("pipeline") or {}).get("anr_root") or "").strip():
        found = discover_anr_root()
        if found:
            cfg.setdefault("pipeline", {})["anr_root"] = found
    cfg = _ensure_upload_mosaic_policy(cfg)
    return cfg


def _ensure_upload_mosaic_policy(cfg: dict[str, Any]) -> dict[str, Any]:
    """上传前必须打码：起号页 pipeline 配置里打码始终开启。"""
    pipe = cfg.setdefault("pipeline", {})
    mosaic = pipe.setdefault("mosaic", {})
    if isinstance(mosaic, dict):
        mosaic["enabled"] = True
    upload = cfg.setdefault("upload", {})
    if isinstance(upload, dict):
        upload["use_processed"] = True
    return cfg


def save_config(updates: dict[str, Any]) -> dict[str, Any]:
    cfg = load_config()
    for block in ("ai", "account", "upload", "pipeline"):
        if block in updates and isinstance(updates[block], dict):
            cfg[block] = {**cfg.get(block, {}), **updates[block]}
    cfg["ai"] = normalize_ai_config(cfg.get("ai") or {})
    if "pipeline" in updates and isinstance(updates["pipeline"], dict):
        pipe = updates["pipeline"]
        if "anr_root" in pipe:
            cfg["pipeline"]["anr_root"] = pipe["anr_root"]
        for sub in ("upscale", "mosaic", "metadata"):
            if sub in pipe and isinstance(pipe[sub], dict):
                cfg["pipeline"][sub] = {
                    **cfg.get("pipeline", {}).get(sub, {}),
                    **pipe[sub],
                }
    if "persona" in updates and isinstance(updates["persona"], dict):
        cfg["account"]["persona"] = updates["persona"]
    cfg["account"]["persona"] = _clean_stale_ai_warning(
        (cfg.get("account") or {}).get("persona")
    )
    cfg = _ensure_upload_mosaic_policy(cfg)
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        CONFIG_PATH,
        json.dumps(cfg, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return cfg
