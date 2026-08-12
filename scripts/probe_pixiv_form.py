"""列出投稿页全部表单控件（含 R18 / AI）。"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

from pixiv_browser_login import PROFILE_DIR, proxy_settings

CREATE_URL = "https://www.pixiv.net/illustration/create"


async def main() -> None:
    proxy = proxy_settings()
    ctx_kwargs = {"user_data_dir": str(PROFILE_DIR), "headless": False, "locale": "zh-CN"}
    if proxy:
        ctx_kwargs["proxy"] = proxy

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(channel="chrome", **ctx_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(CREATE_URL, wait_until="domcontentloaded", timeout=90000)
        await page.wait_for_timeout(2500)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
        await page.wait_for_timeout(1500)
        info = await page.evaluate(
            """() => {
              const pick = (sel) => Array.from(document.querySelectorAll(sel)).map(el => ({
                tag: el.tagName,
                name: el.getAttribute('name'),
                id: el.id,
                type: el.getAttribute('type'),
                value: el.getAttribute('value'),
                placeholder: el.getAttribute('placeholder'),
                text: (el.labels && el.labels[0] ? el.labels[0].innerText : el.parentElement?.innerText || '').trim().slice(0, 80),
                checked: el.checked,
                disabled: !!el.disabled,
              }));
              const labels = Array.from(document.querySelectorAll('label, legend, h2, h3, p, span')).map(el => (el.innerText||'').trim()).filter(t => /R-?18|AI|色情|性|成人|生成|描写|限制/i.test(t)).slice(0, 40);
              return {
                url: location.href,
                all_inputs: pick('input, textarea, select'),
                r18_labels: labels,
                submit_btns: pick('button').filter(b => (b.text||'').includes('投稿')),
              };
            }"""
        )
        out = Path(__file__).resolve().parent.parent / "data" / "pixiv_form_probe.json"
        out.write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(info, ensure_ascii=False, indent=2)[:8000])
        await context.close()


if __name__ == "__main__":
    asyncio.run(main())