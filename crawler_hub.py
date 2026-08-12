"""Unified crawler entry for multi-gallery NAI product.

Targets:
  site     -> disabled legacy alias; never starts an upstream crawler
  qqgroup  -> local QQ export folder crawler (crawler_qq.py)
  pixiv    -> Pixiv discovery + strict NovelAI intake
  all      -> Pixiv direct NAI intake only (QQ remains explicit)

Codex has no crawler (import only).
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PYTHON = sys.executable


def _load_config() -> dict:
    path = ROOT / "config.json"
    return json.loads(path.read_text(encoding="utf-8"))


def run_site(*, phase: str = "all", background: bool = False) -> int:
    _ = (phase, background)
    print("[hub] legacy site crawler is disabled; use --target pixiv")
    return 2


def run_qq(*, watch: bool = False, background: bool = False) -> int:
    cmd = [PYTHON, "-u", str(ROOT / "crawler_qq.py")]
    if watch:
        cmd.append("--watch")
    else:
        cmd.append("--once")
    print("[hub] start qq crawler:", " ".join(cmd))
    if background:
        log = ROOT / "logs" / "crawl_qq_hub.out.log"
        err = ROOT / "logs" / "crawl_qq_hub.err.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as out, err.open(
            "a", encoding="utf-8"
        ) as errf:
            subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=out,
                stderr=errf,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        print(f"[hub] qq crawler background; logs={log}")
        return 0
    return subprocess.call(cmd, cwd=str(ROOT))


def run_pixiv(*, watch: bool = False, background: bool = False) -> int:
    cmd = [PYTHON, "-u", str(ROOT / "pixiv_nai_crawler.py")]
    cmd.append("--watch" if watch else "--once")
    print("[hub] start Pixiv NAI intake:", " ".join(cmd))
    if background:
        log = ROOT / "logs" / "pixiv-nai-crawler.out.log"
        err = ROOT / "logs" / "pixiv-nai-crawler.err.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as out, err.open(
            "a", encoding="utf-8"
        ) as errf:
            subprocess.Popen(
                cmd,
                cwd=str(ROOT),
                stdout=out,
                stderr=errf,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
        return 0
    return subprocess.call(cmd, cwd=str(ROOT))


def status() -> dict:
    cfg = _load_config()
    crawlers = cfg.get("crawlers") if isinstance(cfg.get("crawlers"), dict) else {}
    site_hb = ROOT / "logs" / "crawler-heartbeat.json"
    qq_hb = ROOT / "logs" / "crawler-qq-heartbeat.json"
    pixiv_hb = ROOT / "logs" / "pixiv-nai-intake-heartbeat.json"

    def _read(p: Path) -> dict:
        if not p.exists():
            return {}
        try:
            return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            return {}

    return {
        "site": {
            "config": {
                "enabled": False,
                "disabled": True,
                "migrated_to": "pixiv",
            },
            "heartbeat": _read(site_hb),
        },
        "qqgroup": {
            "config": crawlers.get("qqgroup") or {},
            "heartbeat": _read(qq_hb),
        },
        "pixiv": {
            "config": crawlers.get("pixiv") or {"enabled": True},
            "heartbeat": _read(pixiv_hb),
        },
        "codex": {
            "crawler": None,
            "note": "import only (scripts/import_codex_gallery.py)",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Pixiv NAI Gallery crawler hub")
    parser.add_argument(
        "--target",
        choices=["site", "qq", "qqgroup", "pixiv", "all", "status"],
        default="status",
    )
    parser.add_argument(
        "--phase",
        choices=["all", "search", "detail", "preview"],
        default="all",
        help="site crawler phase",
    )
    parser.add_argument("--watch", action="store_true", help="qq watch mode")
    parser.add_argument(
        "--background",
        action="store_true",
        help="spawn child processes and return",
    )
    args = parser.parse_args()
    target = "qqgroup" if args.target == "qq" else args.target

    if target == "status":
        print(json.dumps(status(), ensure_ascii=False, indent=2))
        return 0
    if target == "site":
        return run_site(phase=args.phase, background=args.background)
    if target == "qqgroup":
        return run_qq(watch=args.watch, background=args.background)
    if target == "pixiv":
        return run_pixiv(watch=args.watch, background=args.background)
    if target == "all":
        return run_pixiv(watch=args.watch, background=args.background)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
