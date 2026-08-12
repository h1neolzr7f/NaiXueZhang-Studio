#!/usr/bin/env node
"use strict";

function requirePlaywright() {
  try {
    return require("playwright");
  } catch (error) {
    throw new Error(
      `Project Playwright dependency is unavailable. Run npm install in the project directory first. ${error.message}`,
    );
  }
}

function arg(name, fallback) {
  const prefix = `--${name}=`;
  const found = process.argv.find((x) => x.startsWith(prefix));
  return found ? found.slice(prefix.length) : fallback;
}

async function main() {
  const { chromium } = requirePlaywright();
  const url = arg("url", "http://127.0.0.1:8797/i/131437249");
  const scenario = arg("scenario", "replace-female-current");
  const clickText = arg("click", "换女角");
  const applyText = arg("apply", "当前图");
  const expectedAppVersion = arg("expected-app-version", "");
  // tests/regression_manifest.json 的 entry_versions（文件名 -> 内容哈希），
  // 由 run_regression_guards.ps1 通过环境变量传入；缺省时跳过该项校验。
  let expectedEntryVersions = {};
  try {
    expectedEntryVersions = JSON.parse(process.env.EXPECTED_ENTRY_VERSIONS || "{}");
  } catch {
    expectedEntryVersions = {};
  }

  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage({ viewport: { width: 1440, height: 1000 } });
  const logs = [];
  page.on("console", (msg) => logs.push({ type: msg.type(), text: msg.text() }));
  page.on("pageerror", (err) => logs.push({ type: "pageerror", text: err.message }));

  const visitUrl = url.includes("?") ? `${url}&probe_ts=${Date.now()}` : `${url}?probe_ts=${Date.now()}`;
  await page.goto(visitUrl, { waitUntil: "networkidle", timeout: 60000 });
  await page.waitForTimeout(1200);

  if (scenario === "favorites-smoke") {
    const result = await page.evaluate(() => {
      const scripts = [...document.scripts].map((s) => s.src).filter(Boolean);
      const appScript = scripts.find((s) => s.includes("/assets/app.js")) || "";
      const resources = performance.getEntriesByType("resource").map((r) => String(r.name || ""));
      const images = [...document.querySelectorAll(".card[data-work-id] img")]
        .slice(0, 12)
        .map((img) => ({
          loading: img.loading || "",
          priority: img.fetchPriority || "",
          hasSrc: !!(img.currentSrc || img.src),
        }));
      return {
        appScript,
        scripts,
        cards: document.querySelectorAll(".card[data-work-id]").length,
        favButtons: document.querySelectorAll(".fav-btn-card").length,
        charSwapScript: scripts.find((s) => s.includes("/plugins/char-swap/plugin.js")) || "",
        charSwapResource: resources.find((s) => s.includes("/plugins/char-swap/plugin.js")) || "",
        favoritesSummaryResource: resources.find((s) => /\/api\/favorites(?:\?|$)/.test(s)) || "",
        loadingVisible: (() => {
          const el = document.getElementById("loading");
          return !!(el && getComputedStyle(el).display !== "none");
        })(),
        images,
      };
    });
    await browser.close();
    const badLogs = logs.filter((x) => x.type === "pageerror" || /ReferenceError|TypeError|SyntaxError/.test(x.text));
    const failures = [];
    if (expectedAppVersion && !result.appScript.includes(`v=${expectedAppVersion}`)) {
      failures.push(`app.js version mismatch: ${result.appScript}`);
    }
    for (const [name, version] of Object.entries(expectedEntryVersions)) {
      const src = result.scripts.find((s) => s.includes(`/assets/${name}`)) || "";
      if (!src) {
        failures.push(`entry script not loaded on page: ${name}`);
      } else if (!src.includes(`v=${version}`)) {
        failures.push(`${name} version mismatch: ${src}`);
      }
    }
    if (!result.cards) failures.push("favorites page rendered no cards");
    if (result.cards !== result.favButtons) failures.push(`favorite button/card mismatch: ${result.favButtons}/${result.cards}`);
    if (result.charSwapScript || result.charSwapResource) failures.push(`favorites page eagerly loaded char-swap plugin: ${result.charSwapScript || result.charSwapResource}`);
    if (result.favoritesSummaryResource) failures.push(`favorites page made redundant favorites summary request: ${result.favoritesSummaryResource}`);
    if (result.loadingVisible) failures.push("loading indicator still visible after favorites render");
    if (!result.images.length || result.images.some((img) => !img.hasSrc)) failures.push("favorites thumbnails missing src");
    if (result.images.slice(0, 8).some((img) => img.loading !== "lazy")) failures.push("favorites first thumbnails are not lazy-loaded");
    if (result.images.slice(0, 8).some((img) => img.priority && img.priority !== "low" && img.priority !== "auto")) {
      failures.push("favorites first thumbnails use blocking fetch priority");
    }
    if (badLogs.length) failures.push(`browser JS errors: ${badLogs.map((x) => x.text).join(" | ")}`);
    console.log(JSON.stringify({ ok: failures.length === 0, scenario, failures, result, badLogs }, null, 2));
    if (failures.length) process.exit(1);
    return;
  }

  if (scenario === "generated-prompts") {
    const firstPrompt = page.locator(".gen-item-prompt").first();
    const result = await page.evaluate(() => ({
      items: document.querySelectorAll(".gen-item").length,
      promptPanels: document.querySelectorAll(".gen-item-prompt").length,
      sourcePanel: !!document.querySelector("#sourceMetaPanel:not(.hidden)"),
      sourceTextLen: (document.querySelector("#sourceMetaPanel")?.textContent || "").trim().length,
      emptyText: (document.querySelector(".gen-empty")?.textContent || "").trim(),
      summaries: [...document.querySelectorAll(".gen-item-prompt summary")]
        .slice(0, 5)
        .map((el) => el.textContent.trim()),
    }));
    if ((await firstPrompt.count()) > 0) {
      await firstPrompt.locator("summary").click();
      await page.waitForTimeout(300);
    }
    const openPrompt = await page.evaluate(() => {
      const panel = document.querySelector(".gen-item-prompt");
      return {
        open: !!panel?.open,
        textLen: (panel?.textContent || "").trim().length,
        textSample: (panel?.textContent || "").trim().slice(0, 240),
      };
    });
    await browser.close();
    const badLogs = logs.filter((x) => x.type === "pageerror" || /ReferenceError|TypeError|SyntaxError/.test(x.text));
    const failures = [];
    if (!result.items) failures.push(`generated gallery has no items: ${result.emptyText}`);
    if (result.promptPanels < result.items) failures.push(`prompt panel count ${result.promptPanels} < item count ${result.items}`);
    if (!result.sourcePanel || result.sourceTextLen < 20) failures.push("source prompt panel missing or too small");
    if (!openPrompt.open || openPrompt.textLen < 100) failures.push("first generated prompt did not expand with prompt text");
    if (badLogs.length) failures.push(`browser JS errors: ${badLogs.map((x) => x.text).join(" | ")}`);
    console.log(JSON.stringify({ ok: failures.length === 0, scenario, failures, result, openPrompt, badLogs }, null, 2));
    if (failures.length) process.exit(1);
    return;
  }

  if (scenario === "token-settings") {
    const result = await page.evaluate(() => ({
      settings: !!document.querySelector("#charSwapSettings"),
      tokenBox: !!document.querySelector("#charSwapToken"),
      saveConfig: !!document.querySelector("#charSwapSaveAll"),
      replaceTokens: !!document.querySelector("#charSwapReplaceTokens"),
      addToken: !!document.querySelector("#charSwapAddToken"),
      checkTokens: !!document.querySelector("#charSwapCheckTokens"),
      slots: !!document.querySelector("#charSwapTokenSlots"),
      slotText: document.querySelector("#charSwapTokenSlots")?.textContent?.trim() || "",
    }));
    await browser.close();
    const badLogs = logs.filter((x) => x.type === "pageerror" || /ReferenceError|TypeError|SyntaxError/.test(x.text));
    const missing = Object.entries(result)
      .filter(([, value]) => typeof value === "boolean" && !value)
      .map(([key]) => key);
    const failures = [
      ...missing.map((key) => `missing ${key}`),
      ...(badLogs.length ? [`browser JS errors: ${badLogs.map((x) => x.text).join(" | ")}`] : []),
    ];
    console.log(JSON.stringify({ ok: failures.length === 0, scenario, failures, result, badLogs }, null, 2));
    if (failures.length) process.exit(1);
    return;
  }

  if (scenario === "reset-all") {
    const before = await page.evaluate(() => {
      const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
      const action = (name) => document.querySelector(`#charSwapPanel [data-action="${name}"]`);
      return {
        panel: visible(document.querySelector("#charSwapPanel")),
        panelCount: document.querySelectorAll("#charSwapPanel").length,
        reset: visible(action("reset")),
        resetAll: visible(action("reset-all")),
        imageCount: window.__AITAG_CURRENT_DETAIL__?.data?.images?.length || 0,
      };
    });
    page.once("dialog", async (dialog) => { await dialog.accept(); });
    await page.locator('#charSwapPanel [data-action="reset-all"]').click();
    await page.waitForTimeout(1500);
    const after = await page.evaluate(() => ({
      msg: document.querySelector(".char-swap-msg")?.textContent || "",
      title: document.getElementById("charSwapSlotsTitle")?.textContent || "",
      preview: document.getElementById("charSwapDraftPreview")?.textContent || "",
    }));
    await browser.close();
    const badLogs = logs.filter((x) => x.type === "pageerror" || /ReferenceError|TypeError|SyntaxError/.test(x.text));
    const failures = [];
    if (!before.panel) failures.push("char-swap panel not visible");
    if (before.panelCount !== 1) failures.push(`expected exactly one char-swap panel, saw ${before.panelCount}`);
    if (!before.reset || !before.resetAll) failures.push("reset controls missing");
    if (!after.msg.includes("全部") || !after.msg.includes("已恢复")) failures.push("reset-all did not report success");
    if (!after.preview.trim()) failures.push("draft preview is empty after reset-all");
    if (badLogs.length) failures.push(`browser JS errors: ${badLogs.map((x) => x.text).join(" | ")}`);
    console.log(JSON.stringify({ ok: failures.length === 0, scenario, failures, before, after, badLogs }, null, 2));
    if (failures.length) process.exit(1);
    return;
  }

  const before = await page.evaluate(() => {
    const visible = (el) => !!(el && (el.offsetWidth || el.offsetHeight || el.getClientRects().length));
      const action = (name) => document.querySelector(`#charSwapPanel [data-action="${name}"]`);
      return {
        panel: visible(document.querySelector("#charSwapPanel")),
        panelCount: document.querySelectorAll("#charSwapPanel").length,
        batchDrawer: visible(document.getElementById("charSwapBatchDrawer")),
        batchFab: visible(document.getElementById("charSwapQuickFab")),
      addBatchAll: visible(action("add-batch-all-pages")),
      resetAll: visible(action("reset-all")),
      replaceFemaleAll: visible(action("replace-female-all")),
      imageCount: window.__AITAG_CURRENT_DETAIL__?.data?.images?.length || 0,
      preview: document.getElementById("charSwapDraftPreview")?.textContent || "",
      slotText: document.querySelector(".char-swap-slots-draft")?.innerText || "",
    };
  });

  await page.locator("#charSwapPanel button", { hasText: clickText }).first().click();
  await page.waitForSelector(".char-swap-modal-backdrop", { timeout: 10000 });
  const modalText = await page.locator(".char-swap-modal-backdrop").first().innerText();

  const applyButton = page.locator(".char-swap-modal-backdrop button", { hasText: applyText }).first();
  if (await applyButton.count()) {
    await applyButton.click();
    await page.waitForTimeout(1500);
  }

  const after = await page.evaluate(() => ({
    msg: document.querySelector(".char-swap-msg")?.textContent || "",
    preview: document.getElementById("charSwapDraftPreview")?.textContent || "",
    slotText: document.querySelector(".char-swap-slots-draft")?.innerText || "",
    modalCount: document.querySelectorAll(".char-swap-modal-backdrop").length,
  }));

  await browser.close();

  const badLogs = logs.filter((x) => x.type === "pageerror" || /ReferenceError|TypeError|SyntaxError/.test(x.text));
  const failures = [];
  if (!before.panel) failures.push("char-swap panel not visible");
  if (before.panelCount !== 1) failures.push(`expected exactly one char-swap panel, saw ${before.panelCount}`);
  if (!before.batchDrawer || !before.batchFab) failures.push("batch drawer/fab not visible");
  if (before.imageCount > 1 && (!before.addBatchAll || !before.replaceFemaleAll)) failures.push("all-page batch controls hidden");
  if (before.imageCount > 1 && !before.resetAll) failures.push("all-page reset control hidden");
  if (!before.preview.trim()) failures.push("initial draft preview is empty");
  if (!modalText.includes("角色预设")) failures.push("preset modal did not open");
  if (!after.msg.includes("草稿已")) failures.push("preset apply did not report success");
  if (!after.preview.trim()) failures.push("draft preview is empty after apply");
  if (badLogs.length) failures.push(`browser JS errors: ${badLogs.map((x) => x.text).join(" | ")}`);

  const result = { ok: failures.length === 0, failures, before, modalText: modalText.slice(0, 1200), after, badLogs };
  console.log(JSON.stringify(result, null, 2));
  if (failures.length) process.exit(1);
}

main().catch((err) => {
  console.error(err.stack || err.message || String(err));
  process.exit(1);
});
