import json
import httpx
from pathlib import Path
from db import Database
from paths import bundled_web_dir, project_root, normalize_config
from tag_translate import TagTranslator
from crawler_watchdog import get_watchdog

ROOT = project_root()
_CONFIG_PATH = ROOT / "config.json"


def _config_source() -> Path:
    if _CONFIG_PATH.exists():
        return _CONFIG_PATH

    # A clean source checkout intentionally does not track config.json. Read
    # the public release defaults from the repository root without mutating the
    # checkout. Frozen bundles still materialize a writable config beside the
    # EXE so runtime settings can be persisted normally.
    import sys

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidate = Path(meipass) / "config.release.json"
    else:
        candidate = Path(__file__).resolve().parent / "config.release.json"
    if not candidate.exists():
        raise FileNotFoundError(
            f"missing config.json and release defaults: {candidate}"
        )
    if getattr(sys, "frozen", False):
        _CONFIG_PATH.write_text(candidate.read_text(encoding="utf-8"), encoding="utf-8")
        return _CONFIG_PATH
    return candidate


CONFIG = normalize_config(
    json.loads(_config_source().read_text(encoding="utf-8")), ROOT
)
DATA_DIR = Path(CONFIG["data_dir"])
_bundled_web = bundled_web_dir()
WEB_DIR = _bundled_web if _bundled_web is not None else Path(CONFIG["web_dir"])
CDN_URL = CONFIG.get("cdn_url", "")
DB = Database(DATA_DIR / "aitag.db")
TAG_TRANSLATOR = TagTranslator()
GALLERY_SCOPE = CONFIG.get("gallery_scope") or "local"
GALLERY_LOCAL_ONLY = bool(CONFIG.get("gallery_local_only", True))
CRAWLER_WATCHDOG = get_watchdog(CONFIG)
# CDN 回源不跟随重定向：3xx 目标不受控，跟随会把本服务变成有限开放代理。
# 静态图床不需要重定向；遇到 3xx 一律按 soft-miss 处理（见 routes/gallery.py）。
_CDN_CLIENT = httpx.Client(timeout=20.0, follow_redirects=False)
_CDN_MISS_CACHE: dict[str, float] = {}
_CDN_MISS_TTL = 60.0
_CDN_MISS_CACHE_MAX = 10000


def record_cdn_miss(
    url: str,
    now: float,
    *,
    cache: dict[str, float] | None = None,
    ttl: float = _CDN_MISS_TTL,
    max_size: int = _CDN_MISS_CACHE_MAX,
) -> None:
    """Record a CDN soft-miss with a bounded cache.

    Once over capacity, expired entries are evicted first; if the cache is
    still too large, the oldest half (insertion order) is dropped.
    """

    store = _CDN_MISS_CACHE if cache is None else cache
    if len(store) >= max_size:
        for key in [key for key, ts in store.items() if now - ts >= ttl]:
            store.pop(key, None)
        if len(store) >= max_size:
            for key in list(store)[: max(1, len(store) // 2)]:
                store.pop(key, None)
    store[url] = now
