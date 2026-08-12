"""探测 Pixiv 投稿页 DOM（一次性调试脚本）。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from pixiv_browser_login import PROFILE_DIR, proxy_settings

CREATE_URL = "https://www.pixiv.net/illustration/create"


async def main() -> None:
    proxy = proxy_settings()
    ctx_kwargs: dict = {
        "user_data_dir": str(PROFILE_DIR),
        "headless": False,
        "locale": "zh-CN",
    }
    if proxy:
        ctx_kwargs["proxy"] = proxy

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(channel="chrome", **ctx_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(CREATE_URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(4000)
        info = await page.evaluate(
            """() => {
              const pick = (sel) => Array.from(document.querySelectorAll(sel)).slice(0, 8).map(el => ({
                tag: el.tagName,
                name: el.getAttribute('name'),
                type: el.getAttribute('type'),
                placeholder: el.getAttribute('placeholder'),
                value: el.getAttribute('value'),
                text: (el.innerText || '').trim().slice(0, 40),
                disabled: !!el.disabled,
              }));
              return {
                url: location.href,
                title: document.title,
                files: pick("input[type='file']"),
                titles: pick("input[name='title'], input#title"),
                comments: pick("textarea[name='comment'], textarea#caption"),
                tags: pick("input[placeholder*='标签'], input[placeholder*='タグ'], input[name*='tag']"),
                submits: pick("button[type='submit'], button"),
                ai: pick("input[name='ai_type']"),
                restrict: pick("input[name='restrict']"),
              };
            }"""
        )
        out = Path(__file__).resolve().parent.parent / "data" / "pixiv_create_probe.json"
        out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(info, ensure_ascii=False, indent=2))
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())