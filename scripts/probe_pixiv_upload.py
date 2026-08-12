"""探测上传文件后投稿页状态。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from pixiv_browser_login import PROFILE_DIR, proxy_settings

CREATE_URL = "https://www.pixiv.net/illustration/create"
IMAGE = Path(__file__).resolve().parent.parent / "data" / "generated" / "20260607_205213_145608349_final.png"


async def main() -> None:
    proxy = proxy_settings()
    ctx_kwargs = {"user_data_dir": str(PROFILE_DIR), "headless": False, "locale": "zh-CN"}
    if proxy:
        ctx_kwargs["proxy"] = proxy

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(channel="chrome", **ctx_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(CREATE_URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2000)

        file_input = page.locator("input[name='files[]']").first
        await file_input.set_input_files(str(IMAGE))

        snapshots = []
        for i in range(30):
            await page.wait_for_timeout(2000)
            info = await page.evaluate(
                """() => {
                  const btns = Array.from(document.querySelectorAll('button')).map((el, idx) => ({
                    idx,
                    text: (el.innerText || '').trim().slice(0, 30),
                    disabled: !!el.disabled,
                    ariaDisabled: el.getAttribute('aria-disabled'),
                    visible: !!(el.offsetParent),
                  }));
                  const imgs = Array.from(document.querySelectorAll('img')).filter(i => (i.src||'').includes('pximg')).length;
                  const spinners = document.querySelectorAll('[class*="loading"], [class*="spinner"], [class*="progress"]').length;
                  return { url: location.href, btns, pximg: imgs, spinners };
                }"""
            )
            info["t"] = i * 2
            snapshots.append(info)
            enabled = [b for b in info["btns"] if "投稿" in b["text"] and not b["disabled"]]
            print(f"t={info['t']}s pximg={info['pximg']} spinners={info['spinners']} 投稿可点={len(enabled)}")
            if enabled and info["pximg"] > 0:
                break

        out = Path(__file__).resolve().parent.parent / "data" / "pixiv_upload_probe.json"
        out.write_text(json.dumps(snapshots, ensure_ascii=False, indent=2), encoding="utf-8")
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())