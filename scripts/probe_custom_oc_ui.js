#!/usr/bin/env node
"use strict";

const { chromium } = require("playwright");

const baseUrl = process.argv.find((value) => value.startsWith("--url="))?.slice(6)
  || "http://127.0.0.1:8791/i/131437249";

async function runCase(browser, { name, viewport, isMobile = false }) {
  const context = await browser.newContext({ viewport, isMobile, hasTouch: isMobile });
  const page = await context.newPage();
  const requests = [];
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") errors.push(message.text());
  });
  await page.route("**/api/plugin/char-swap/presets", async (route) => {
    if (route.request().method() !== "POST") return route.continue();
    requests.push({ type: "preset", body: route.request().postDataJSON() });
    return route.fulfill({
      status: 200,
      contentType: "application/json",
      body: JSON.stringify({
        ok: true,
        preset: {
          id: "probe-custom-oc",
          label: "探针 OC",
          gender: "female",
          kind: "oc",
          char_caption: "1girl, female_focus, silver hair, starry eyes",
          is_custom: true,
          source: "custom",
        },
      }),
    });
  });
  await page.route("**/api/plugin/char-swap/transform", async (route) => {
    requests.push({ type: "transform", body: route.request().postDataJSON() });
    return route.fulfill({ status: 500, contentType: "application/json", body: JSON.stringify({ detail: "probe stops before mutation" }) });
  });

  await page.goto(`${baseUrl}${baseUrl.includes("?") ? "&" : "?"}custom_oc_probe=${Date.now()}`, {
    waitUntil: "networkidle",
    timeout: 60000,
  });
  await page.waitForSelector("#charSwapPanel", { timeout: 15000 });
  const pickerButton = page.locator("#charSwapPanel button", { hasText: "换女角" }).first();
  await pickerButton.click();
  const modal = page.locator(".char-swap-modal-backdrop .char-swap-modal").first();
  await modal.waitFor({ state: "visible" });
  const initial = await modal.evaluate((element) => ({
    text: element.innerText.slice(0, 1400),
    role: element.getAttribute("role"),
    ariaModal: element.getAttribute("aria-modal"),
    searchFocused: document.activeElement?.id === "presetSearchInput",
  }));
  await modal.locator(".char-swap-add-custom-oc").click();
  const form = modal.locator(".char-swap-custom-oc-form");
  await form.waitFor({ state: "visible" });
  const openState = await modal.evaluate((element) => {
    const rect = element.getBoundingClientRect();
    const controls = [...element.querySelectorAll("button, select, input")];
    return {
      rect: { top: rect.top, bottom: rect.bottom, left: rect.left, right: rect.right },
      viewport: { width: innerWidth, height: innerHeight },
      activeTag: document.activeElement?.tagName || "",
      minControlHeight: Math.min(...controls.map((control) => control.getBoundingClientRect().height)),
      controlHeights: controls.map((control) => ({
        text: (control.textContent || control.getAttribute("name") || control.id || control.tagName).trim().slice(0, 28),
        className: control.className,
        height: control.getBoundingClientRect().height,
        minHeight: getComputedStyle(control).minHeight,
      })).filter((control) => control.height < 44).slice(0, 12),
      horizontalOverflow: document.documentElement.scrollWidth > document.documentElement.clientWidth,
    };
  });
  await form.locator('[name="label"]').fill("探针 OC");
  await form.locator('[name="char_caption"]').fill("1girl, female_focus, silver hair, starry eyes");
  const screenshot = `reports/custom-oc-${name}.png`;
  await page.screenshot({ path: screenshot, type: "png", scale: "css" });
  await form.locator('button[type="submit"]').click();
  await page.waitForTimeout(500);

  const multiSelect = modal.locator('.char-swap-multi-slot-select option[value="probe-custom-oc"]');
  const isMulti = await modal.locator(".char-swap-multi-slot-select").count() > 0;
  let selected = false;
  if (isMulti) {
    selected = await multiSelect.count() > 0 && await modal.locator('.char-swap-multi-slot-select').first().inputValue() === "probe-custom-oc";
  }
  const failures = [];
  if (!initial.text.includes("＋ 自定义 OC") || !initial.text.includes("我的 OC")) failures.push("custom OC controls are not immediately visible");
  if (initial.role !== "dialog" || initial.ariaModal !== "true") failures.push("picker dialog semantics missing");
  if (isMobile && initial.searchFocused) failures.push("mobile picker automatically opened the keyboard");
  if (openState.rect.top < 0 || openState.rect.bottom > openState.viewport.height + 1) failures.push("modal is vertically clipped");
  if (openState.rect.left < 0 || openState.rect.right > openState.viewport.width + 1 || openState.horizontalOverflow) failures.push("modal causes horizontal clipping");
  if (isMobile && openState.minControlHeight < 39) failures.push(`mobile control too short: ${openState.minControlHeight}`);
  if (requests.filter((item) => item.type === "preset").length !== 1) failures.push("custom OC was not saved exactly once");
  if (requests[0]?.body?.clothing || requests[0]?.body?.extra || requests[0]?.body?.remove) failures.push("temporary layers leaked into custom OC save");
  if (isMulti && !selected) failures.push("new OC was not injected and selected in the focused slot");
  if (errors.some((message) => /ReferenceError|TypeError|SyntaxError/.test(message))) failures.push(`browser error: ${errors.join(" | ")}`);

  await context.close();
  const requestSummary = requests.map((item) => ({
    type: item.type,
    label: item.body?.label || "",
    presetId: item.body?.preset_id || "",
    targetCharIndex: item.body?.target_char_index,
    galleryId: item.body?.gallery_id || "",
  }));
  return { name, ok: failures.length === 0, failures, initial, openState, requests: requestSummary, isMulti, selected, screenshot, errors };
}

async function main() {
  const browser = await chromium.launch({ headless: true });
  try {
    const desktop = await runCase(browser, { name: "desktop", viewport: { width: 1440, height: 900 } });
    const mobile = await runCase(browser, { name: "mobile", viewport: { width: 390, height: 844 }, isMobile: true });
    const result = { ok: desktop.ok && mobile.ok, desktop, mobile };
    console.log(JSON.stringify(result, null, 2));
    if (!result.ok) process.exitCode = 1;
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error.stack || error.message || String(error));
  process.exit(1);
});
