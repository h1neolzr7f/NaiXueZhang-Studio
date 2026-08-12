(function () {
  "use strict";

  const STORAGE_KEY = "aitag.director.selection.v1";
  const MAX_SOURCES = 40;
  const state = {
    catalog: null,
    sourceKind: "generated",
    pickMode: "series",
    galleryId: "site",
    page: 1,
    pageSize: 24,
    total: 0,
    sourceRows: [],
    selected: new Map(),
    preview: null,
    previewFingerprint: "",
    taskId: "",
    pollTimer: 0,
    pollInFlight: false,
    eventSource: null,
    taskRevision: -1,
    taskEpoch: 0,
    sourceRequestSeq: 0,
  };

  const $ = (id) => document.getElementById(id);
  const api = window.ApiClient;

  function safeText(value) {
    return String(value == null ? "" : value);
  }

  const escapeHtml = window.escapeHtml;

  function localResultUrl(value) {
    const text = safeText(value).trim();
    return text.startsWith("/data/generated/") ? text : "";
  }

  function loadSelection() {
    try {
      const rows = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]");
      if (!Array.isArray(rows)) return;
      rows.slice(0, MAX_SOURCES).forEach((row) => {
        if (row && row.source_id) state.selected.set(row.source_id, row);
      });
    } catch (_) { /* start clean */ }
  }

  function saveSelection() {
    try {
      localStorage.setItem(STORAGE_KEY, JSON.stringify(Array.from(state.selected.values())));
    } catch (_) { /* local persistence is optional */ }
  }

  function sourcePayload(row) {
    if (row.kind === "generated") return { kind: "generated", image_id: row.image_id };
    return {
      kind: "gallery",
      gallery_id: row.gallery_id,
      work_id: Number(row.work_id),
      page_index: Number(row.page_index || 0),
    };
  }

  function selectedPayload() {
    return Array.from(state.selected.values()).map(sourcePayload);
  }

  function currentRecipe() {
    const tool = $("directorTool").value;
    const recipe = { tool };
    if (tool === "colorize") {
      recipe.prompt = $("directorColorPrompt")?.value || "";
      recipe.defry = Number($("directorDefry")?.value || 0);
    } else if (tool === "emotion") {
      recipe.emotion = $("directorEmotion")?.value || "happy";
      recipe.prompt = $("directorEmotionPrompt")?.value || "";
      recipe.level = Number($("directorEmotionLevel")?.value || 3);
    }
    return recipe;
  }

  function requestFingerprint() {
    return JSON.stringify({ sources: selectedPayload(), recipe: currentRecipe() });
  }

  function invalidatePreview() {
    state.preview = null;
    state.previewFingerprint = "";
    $("directorConfirmBilling").checked = false;
    $("directorConfirmBilling").disabled = true;
    $("directorRunButton").disabled = true;
    $("directorPreviewResult").innerHTML = '<div class="director-empty compact">配置已变化，请重新做零费用预检。</div>';
    updateSteps("recipe");
  }

  function updateSteps(active) {
    const order = ["select", "recipe", "preview", "run", "report"];
    const activeIndex = order.indexOf(active);
    document.querySelectorAll(".director-steps [data-step]").forEach((node) => {
      const index = order.indexOf(node.dataset.step);
      node.classList.toggle("active", index === activeIndex);
      node.classList.toggle("done", index < activeIndex);
    });
  }

  function renderSelectedCount() {
    const groups = new Set(Array.from(state.selected.values()).map((row) => row.group_id).filter(Boolean));
    const suffix = groups.size ? ` · ${groups.size} 个系列` : "";
    $("directorSelectedCount").textContent = `已选 ${state.selected.size} / ${MAX_SOURCES}${suffix}`;
  }

  function rowItems(row) {
    if (row.kind !== "generated_group") return [row];
    return Array.isArray(row.items) ? row.items : [];
  }

  function rowSelectionState(row) {
    const items = rowItems(row);
    if (row.kind === "generated_group" && !items.length) {
      const selectedCount = Array.from(state.selected.values())
        .filter((item) => item.group_id === row.group_id).length;
      const expected = Number(row.count || 0);
      return {
        all: expected > 0 && selectedCount === expected,
        partial: selectedCount > 0 && selectedCount < expected,
      };
    }
    const selectedCount = items.filter((item) => state.selected.has(item.source_id)).length;
    return { all: !!items.length && selectedCount === items.length, partial: selectedCount > 0 && selectedCount < items.length };
  }

  async function ensureGroupItems(row) {
    if (row.kind !== "generated_group" || rowItems(row).length) return row;
    const payload = await api.get(`/api/director/source-groups/${encodeURIComponent(row.group_id)}`);
    const expanded = payload && payload.source;
    if (!expanded || !Array.isArray(expanded.items) || !expanded.items.length) {
      throw new Error("该系列没有可用的本地图片");
    }
    const index = state.sourceRows.findIndex((item) => item.group_id === row.group_id);
    if (index >= 0) state.sourceRows[index] = expanded;
    return expanded;
  }

  async function toggleSource(row, event) {
    try {
      row = await ensureGroupItems(row);
    } catch (error) {
      window.alert(`读取系列失败：${safeText(error.message)}`);
      return;
    }
    const items = rowItems(row);
    const selection = rowSelectionState(row);
    const multi = !!(event && (event.ctrlKey || event.metaKey));
    if (multi && selection.all) {
      items.forEach((item) => state.selected.delete(item.source_id));
    } else {
      const retained = multi ? state.selected.size : 0;
      const additions = items.filter((item) => !multi || !state.selected.has(item.source_id));
      if (retained + additions.length > MAX_SOURCES) {
        window.alert(`单次最多选择 ${MAX_SOURCES} 张来源图；当前系列有 ${items.length} 张。`);
        return;
      }
      if (!multi) state.selected.clear();
      additions.forEach((item) => state.selected.set(item.source_id, item));
    }
    saveSelection();
    renderSources();
    renderSelectedCount();
    invalidatePreview();
    updateSteps(state.selected.size ? "recipe" : "select");
  }

  function renderSources() {
    const grid = $("directorSourceGrid");
    grid.innerHTML = "";
    if (!state.sourceRows.length) {
      grid.innerHTML = '<div class="director-empty">当前条件下没有可用于导演的本地图片。</div>';
      return;
    }
    state.sourceRows.forEach((row) => {
      const card = document.createElement("button");
      card.type = "button";
      const selection = rowSelectionState(row);
      const overLimit = row.kind === "generated_group" && Number(row.count || rowItems(row).length) > MAX_SOURCES;
      card.className = `director-source-card${selection.all ? " selected" : ""}${selection.partial ? " partial" : ""}${overLimit ? " over-limit" : ""}`;
      card.setAttribute("aria-pressed", selection.all ? "true" : "false");
      if (overLimit) card.title = `该系列超过 ${MAX_SOURCES} 张，请切到“单张”挑选本次要处理的图片`;
      const image = document.createElement("img");
      image.loading = "lazy";
      image.decoding = "async";
      image.alt = safeText(row.label);
      image.src = safeText(row.thumb_url || row.image_url);
      const check = document.createElement("span");
      check.className = "director-card-check";
      check.textContent = "✓";
      check.setAttribute("aria-hidden", "true");
      if (row.kind === "generated_group") {
        const count = document.createElement("span");
        count.className = "director-series-count";
        count.textContent = `${Number(row.count || rowItems(row).length)} 张`;
        card.appendChild(count);
      }
      const meta = document.createElement("span");
      meta.className = "director-card-meta";
      const title = document.createElement("strong");
      title.textContent = safeText(row.label);
      const identity = document.createElement("small");
      identity.textContent = row.kind === "generated_group"
        ? (overLimit
          ? `超过 ${MAX_SOURCES} 张上限 · 请切到单张挑选`
          : `${safeText(row.group_id)} · ${safeText(row.created_at).replace("T", " ").slice(0, 16)}`)
        : safeText(row.source_id);
      meta.append(title, identity);
      card.append(image, check, meta);
      card.addEventListener("click", async (event) => {
        card.disabled = true;
        await toggleSource(row, event);
        card.disabled = false;
      });
      grid.appendChild(card);
    });
  }

  async function loadSources() {
    const requestSeq = ++state.sourceRequestSeq;
    const grid = $("directorSourceGrid");
    grid.innerHTML = '<div class="director-empty">正在读取本地图片…</div>';
    const params = new URLSearchParams({
      kind: state.sourceKind,
      mode: state.sourceKind === "generated" ? state.pickMode : "single",
      q: $("directorSearch").value.trim(),
      gallery_id: state.galleryId,
      page: String(state.page),
      page_size: String(state.pageSize),
    });
    try {
      const payload = await api.get(`/api/director/sources?${params}`);
      if (requestSeq !== state.sourceRequestSeq) return;
      state.sourceRows = Array.isArray(payload.items) ? payload.items : [];
      state.total = Number(payload.total || state.sourceRows.length);
      renderSources();
      const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
      $("directorPageInfo").textContent = `第 ${state.page} / ${pages} 页`;
      $("directorPrevPage").disabled = state.page <= 1;
      $("directorNextPage").disabled = state.page >= pages;
    } catch (error) {
      if (requestSeq !== state.sourceRequestSeq) return;
      grid.innerHTML = `<div class="director-empty director-empty-error">读取失败：${escapeHtml(error.message)}</div>`;
    }
  }

  function renderRecipeFields() {
    const toolId = $("directorTool").value;
    const tool = state.catalog.tools.find((item) => item.id === toolId);
    $("directorToolDescription").textContent = tool?.description || "";
    const host = $("directorDynamicFields");
    host.innerHTML = "";
    if (toolId === "colorize") {
      host.innerHTML = `
        <label class="director-field"><span>上色提示词（可选）</span><textarea id="directorColorPrompt" placeholder="例如：red hair, blue eyes, black dress"></textarea></label>
        <label class="director-field"><span>Defry 降噪等级（0–5）</span><input id="directorDefry" type="range" min="0" max="5" step="1" value="0" /></label>`;
    } else if (toolId === "emotion") {
      const options = state.catalog.emotions.map((value) => `<option value="${escapeHtml(value)}">${escapeHtml(value)}</option>`).join("");
      host.innerHTML = `
        <div class="director-inline-fields">
          <label class="director-field"><span>目标表情</span><select id="directorEmotion">${options}</select></label>
          <label class="director-field"><span>表情强度（0–5）</span><input id="directorEmotionLevel" type="number" min="0" max="5" value="3" /></label>
        </div>
        <label class="director-field"><span>补充标签（可选）</span><textarea id="directorEmotionPrompt" placeholder="例如：red eyes, fang"></textarea></label>`;
      $("directorEmotion").value = "happy";
    }
    host.querySelectorAll("input, select, textarea").forEach((node) => node.addEventListener("input", invalidatePreview));
    invalidatePreview();
  }

  async function loadCatalog() {
    try {
      state.catalog = await api.get("/api/director/catalog");
      const select = $("directorTool");
      select.innerHTML = "";
      state.catalog.tools.forEach((tool) => {
        const option = document.createElement("option");
        option.value = tool.id;
        option.textContent = tool.label;
        select.appendChild(option);
      });
      renderRecipeFields();
      const readiness = state.catalog.readiness || {};
      const configured = Boolean(readiness.configured ?? readiness.available);
      const verified = Boolean(readiness.verified);
      $("directorReadiness").dataset.state = verified ? "ready" : configured ? "warning" : "error";
      $("directorReadiness").innerHTML = `
        <span class="director-live-dot"></span>
        <div><strong>${verified ? "NAI 导演已验证" : configured ? "NAI 导演已配置，尚未验证" : "NAI 导演尚未配置"}</strong>
        <small>${verified ? `${Number(readiness.verified_slot_count || 0)} 个槽位最近验证通过` : configured ? `已配置 ${Number(readiness.slot_count || 0)} 个槽位；可到设置页手动检查，不会在打开页面时联网` : "请到设置页添加 pst- Token"}</small></div>`;
    } catch (error) {
      $("directorReadiness").dataset.state = "error";
      $("directorReadiness").innerHTML = `<span class="director-live-dot"></span><div><strong>能力检查失败</strong><small>${escapeHtml(error.message)}</small></div>`;
    }
  }

  function renderPreview(preview) {
    const host = $("directorPreviewResult");
    const failures = Array.isArray(preview.failures) ? preview.failures : [];
    const blockingIssues = Array.isArray(preview.blocking_issues) ? preview.blocking_issues : [];
    const messages = failures.concat(blockingIssues).map((row) => escapeHtml(row.message)).join("；");
    const configuredSlots = Number(preview.provider?.slot_count || 0);
    const verifiedSlots = Number(preview.provider?.verified_slot_count || 0);
    const providerLabel = !preview.provider_ready
      ? "未配置或暂不可用"
      : preview.provider?.verified
        ? `${verifiedSlots} 个已验证（共 ${configuredSlots} 个）`
        : `${configuredSlots} 个已配置，尚未验证`;
    host.innerHTML = `
      <div class="director-preview-card${preview.ready ? "" : " error"}">
        <div class="director-preview-row"><span>可用来源</span><strong>${Number(preview.eligible_count || 0)} / ${Number(preview.source_count || 0)}</strong></div>
        <div class="director-preview-row"><span>预计交付</span><strong>${Number(preview.estimated_outputs || 0)} 张</strong></div>
        <div class="director-preview-row"><span>预检调用上游</span><strong>0 次</strong></div>
        <div class="director-preview-row"><span>NAI 导演槽位</span><strong>${providerLabel}</strong></div>
        <div class="director-preview-row"><span>Anlas 费用</span><strong>上游未预估，执行前确认</strong></div>
        ${messages ? `<div class="director-report-note">${messages}</div>` : ""}
      </div>`;
  }

  async function previewBatch() {
    if (!state.selected.size) {
      window.alert("请先至少选择一张来源图。 ");
      return;
    }
    const button = $("directorPreviewButton");
    button.disabled = true;
    button.textContent = "正在本地预检…";
    try {
      const payload = { sources: selectedPayload(), recipe: currentRecipe() };
      const preview = await api.post("/api/director/preview", payload);
      state.preview = preview;
      state.previewFingerprint = requestFingerprint();
      renderPreview(preview);
      $("directorConfirmBilling").disabled = !preview.ready;
      $("directorConfirmBilling").checked = false;
      $("directorRunButton").disabled = true;
      updateSteps(preview.ready ? "preview" : "recipe");
    } catch (error) {
      $("directorPreviewResult").innerHTML = `<div class="director-preview-card error">预检失败：${escapeHtml(error.message)}</div>`;
    } finally {
      button.disabled = false;
      button.textContent = "零费用预检";
    }
  }

  function statusLabel(batch) {
    const status = safeText(batch?.status || batch);
    if (status === "running" && batch?.cancel_requested) return "停止中";
    if (status === "done" && Number(batch?.fail_count || 0) > 0) return "部分完成";
    return ({ running: "执行中", done: "已完成", cancelled: "已停止", error: "执行失败", unknown: "结果待核对" })[status] || "待开始";
  }

  function renderReport(report) {
    const host = $("directorReport");
    const outputs = Array.isArray(report.outputs) ? report.outputs : [];
    const availableOutputs = outputs.filter((row) => row.available !== false);
    const outputHtml = availableOutputs.map((row) => {
      const url = localResultUrl(row.image_url);
      if (!url) return "";
      const escapedUrl = escapeHtml(url);
      return `<a href="${escapedUrl}" target="_blank" rel="noopener"><img loading="lazy" src="${escapedUrl}" alt="导演结果" /></a>`;
    }).join("");
    const unavailableOutputCount = Number(
      report.unavailable_output_count || Math.max(0, outputs.length - availableOutputs.length),
    );
    const failures = Array.isArray(report.failures) ? report.failures : [];
    const failureHtml = failures.map((row) => `
      <div class="director-report-note warning"><strong>${escapeHtml(row.source_id || "未知来源")}</strong>：${escapeHtml(row.message || "处理失败")}${row.billing_uncertain ? "（扣费状态待核对）" : row.retry_safe ? "（可重新预检后重试）" : ""}</div>`).join("");
    host.hidden = false;
    host.innerHTML = `
      <h3>${escapeHtml(report.title || "NAI 批量导演交付报告")}</h3>
      <div class="director-report-summary">
        <div class="director-stat"><strong>${Number(report.success_sources || 0)}</strong>成功来源</div>
        <div class="director-stat"><strong>${Number(report.failed_sources || 0)}</strong>失败来源</div>
        <div class="director-stat"><strong>${Number(report.output_count || 0)}</strong>交付图片</div>
      </div>
      <div class="director-output-grid">${outputHtml}</div>
      ${unavailableOutputCount ? `<p class="director-report-note warning">${unavailableOutputCount} 张历史结果已移入回收站或不在本机，报告记录仍保留。</p>` : ""}
      ${failureHtml}
      <p class="director-report-note">${escapeHtml(report.billing_message || "")}</p>
      ${report.persistence_degraded ? `<p class="director-report-note warning">任务结果已保留在内存，但状态落盘异常：${escapeHtml(report.persistence_error || "请检查磁盘")}</p>` : ""}
      ${report.review_message ? `<p class="director-report-note warning">${escapeHtml(report.review_message)}</p>` : ""}`;
  }

  function renderTask(batch) {
    $("directorTaskEmpty").hidden = true;
    $("directorTask").hidden = false;
    const status = safeText(batch.status);
    const progress = batch.progress || {};
    const percent = Number(progress.percent || 0);
    $("directorTaskBadge").textContent = statusLabel(batch);
    $("directorTaskBadge").className = `director-task-badge ${status}`;
    $("directorTaskMessage").textContent = safeText(batch.message || "准备中");
    $("directorTaskPercent").textContent = `${percent.toFixed(1)}%`;
    $("directorProgressBar").style.width = `${Math.max(0, Math.min(percent, 100))}%`;
    $("directorProgress").setAttribute("aria-valuenow", String(Math.max(0, Math.min(percent, 100))));
    $("directorTaskStats").innerHTML = `
      <div class="director-stat"><strong>${Number(batch.done || 0)}/${Number(batch.total || 0)}</strong>已处理</div>
      <div class="director-stat"><strong>${Number(batch.ok_count || 0)}</strong>成功</div>
      <div class="director-stat"><strong>${Number(batch.fail_count || 0)}</strong>失败</div>
      <div class="director-stat"><strong>${Number(batch.report?.output_count || 0)}</strong>结果</div>`;
    const phaseLabels = {
      init: "准备任务清单",
      prepare_source: "读取并校验当前来源图",
      director_request: "等待 NovelAI 导演返回；返回前不会开始下一张",
    };
    $("directorCurrentStep").textContent = `当前：${phaseLabels[batch.current_phase] || statusLabel(batch)}${batch.current_source_id ? ` · ${batch.current_source_id}` : ""}`;
    $("directorNextStep").textContent = `下一步：${safeText(batch.next_step || "继续处理任务清单")}`;
    $("directorEta").textContent = `预计剩余：${safeText(batch.eta_text || "正在估算")}`;
    $("directorCancelButton").hidden = status !== "running" || Boolean(batch.cancel_requested);
    $("directorCancelButton").disabled = Boolean(batch.cancel_requested);
    $("directorRetryButton").hidden = !batch.can_retry || status === "running";
    if (batch.terminal) {
      renderReport(batch.report || {});
      updateSteps("report");
    } else {
      updateSteps("run");
    }
  }

  function stopTaskUpdates() {
    clearTimeout(state.pollTimer);
    state.pollTimer = 0;
    if (state.eventSource) {
      state.eventSource.close();
      state.eventSource = null;
    }
  }

  function acceptTaskUpdate(batch, epoch) {
    if (epoch !== state.taskEpoch || safeText(batch.task_id) !== state.taskId) return;
    const revision = Number(batch.revision ?? -1);
    if (revision >= 0 && revision < state.taskRevision) return;
    state.taskRevision = Math.max(state.taskRevision, revision);
    renderTask(batch);
    if (batch.terminal) stopTaskUpdates();
  }

  async function pollTask(epoch = state.taskEpoch) {
    clearTimeout(state.pollTimer);
    if (!state.taskId || state.pollInFlight || document.hidden || epoch !== state.taskEpoch) return;
    state.pollInFlight = true;
    try {
      const payload = await api.get(`/api/director/jobs/${encodeURIComponent(state.taskId)}`);
      const batch = payload.batch || {};
      acceptTaskUpdate(batch, epoch);
      if (!batch.terminal && epoch === state.taskEpoch) state.pollTimer = setTimeout(() => pollTask(epoch), 2500);
    } catch (error) {
      $("directorCurrentStep").textContent = `读取任务状态失败：${safeText(error.message)}，稍后自动重试。`;
      if (epoch === state.taskEpoch) state.pollTimer = setTimeout(() => pollTask(epoch), 3500);
    } finally {
      state.pollInFlight = false;
    }
  }

  function startTaskUpdates() {
    stopTaskUpdates();
    state.taskEpoch += 1;
    state.taskRevision = -1;
    const epoch = state.taskEpoch;
    if (window.EventSource && state.taskId) {
      const stream = new EventSource(`/api/director/jobs-stream?task_id=${encodeURIComponent(state.taskId)}`);
      state.eventSource = stream;
      stream.addEventListener("status", (event) => {
        try { acceptTaskUpdate(JSON.parse(event.data).batch || {}, epoch); }
        catch (_) { /* malformed event falls back on reconnect */ }
      });
      stream.onerror = () => {
        if (state.eventSource === stream) {
          stream.close();
          state.eventSource = null;
          state.pollTimer = setTimeout(() => pollTask(epoch), 800);
        }
      };
      return;
    }
    pollTask(epoch);
  }

  async function runBatch() {
    if (!state.preview || state.previewFingerprint !== requestFingerprint()) {
      window.alert("图片或配置已经变化，请重新预检。 ");
      return;
    }
    if (!$("directorConfirmBilling").checked) return;
    const button = $("directorRunButton");
    button.disabled = true;
    button.textContent = "正在提交…";
    try {
      const result = await api.post("/api/director/jobs", {
        sources: selectedPayload(),
        recipe: currentRecipe(),
        confirmed: true,
        preview_id: safeText(state.preview.preview_id),
      });
      state.taskId = safeText(result.task_id);
      renderTask(result.batch || {});
      startTaskUpdates();
    } catch (error) {
      window.alert(`启动失败：${safeText(error.message)}`);
      button.disabled = false;
    } finally {
      button.textContent = "确认并开始批量导演";
    }
  }

  async function cancelTask() {
    if (!state.taskId) return;
    try {
      const result = await api.post(`/api/director/jobs/${encodeURIComponent(state.taskId)}/cancel`, {});
      renderTask(result.batch || {});
      startTaskUpdates();
    } catch (error) {
      window.alert(`停止失败：${safeText(error.message)}`);
    }
  }

  async function retryTask() {
    if (!state.taskId) return;
    try {
      const retryPreview = await api.post(`/api/director/jobs/${encodeURIComponent(state.taskId)}/retry/preview`, {});
      const count = Number(retryPreview.retry_source_count || retryPreview.source_count || 0);
      const accepted = window.confirm(`将重试 ${count} 张失败或未完成图片。实际 Anlas 费用由 NovelAI 决定，确定继续吗？`);
      if (!accepted) return;
      const result = await api.post(`/api/director/jobs/${encodeURIComponent(state.taskId)}/retry`, {
        confirmed: true,
        preview_id: safeText(retryPreview.preview_id),
      });
      state.taskId = safeText(result.task_id);
      renderTask(result.batch || {});
      startTaskUpdates();
    } catch (error) {
      window.alert(`重试失败：${safeText(error.message)}`);
    }
  }

  function bindEvents() {
    document.querySelectorAll("[data-source-kind]").forEach((button) => button.addEventListener("click", () => {
      state.sourceKind = button.dataset.sourceKind;
      state.page = 1;
      document.querySelectorAll("[data-source-kind]").forEach((node) => node.classList.toggle("active", node === button));
      $("directorGallery").hidden = state.sourceKind !== "gallery";
      $("directorPickModes").hidden = state.sourceKind !== "generated";
      $("directorSearch").placeholder = state.sourceKind === "generated"
        ? (state.pickMode === "series" ? "搜索系列、作品 ID" : "搜索图片 ID、作品或模型")
        : "搜索作品或图片";
      loadSources();
    }));
    document.querySelectorAll("[data-pick-mode]").forEach((button) => button.addEventListener("click", () => {
      state.pickMode = button.dataset.pickMode === "single" ? "single" : "series";
      state.page = 1;
      document.querySelectorAll("[data-pick-mode]").forEach((node) => node.classList.toggle("active", node === button));
      $("directorPickHint").textContent = state.pickMode === "series"
        ? "点封面选择整组，Ctrl+点击可多选系列"
        : "点图片选择单张，Ctrl+点击可多选";
      $("directorSearch").placeholder = state.pickMode === "series" ? "搜索系列、作品 ID" : "搜索图片 ID、作品或模型";
      loadSources();
    }));
    $("directorGallery").addEventListener("change", (event) => {
      state.galleryId = event.target.value;
      state.page = 1;
      loadSources();
    });
    $("directorSearchButton").addEventListener("click", () => { state.page = 1; loadSources(); });
    $("directorSearch").addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); state.page = 1; loadSources(); }
    });
    $("directorPrevPage").addEventListener("click", () => { if (state.page > 1) { state.page -= 1; loadSources(); } });
    $("directorNextPage").addEventListener("click", () => { state.page += 1; loadSources(); });
    $("directorClearSelection").addEventListener("click", () => {
      state.selected.clear(); saveSelection(); renderSelectedCount(); renderSources(); invalidatePreview(); updateSteps("select");
    });
    $("directorTool").addEventListener("change", renderRecipeFields);
    $("directorPreviewButton").addEventListener("click", previewBatch);
    $("directorConfirmBilling").addEventListener("change", () => {
      $("directorRunButton").disabled = !$("directorConfirmBilling").checked || !state.preview?.ready;
      if ($("directorConfirmBilling").checked) updateSteps("run");
    });
    $("directorRunButton").addEventListener("click", runBatch);
    $("directorCancelButton").addEventListener("click", cancelTask);
    $("directorRetryButton").addEventListener("click", retryTask);
    document.addEventListener("visibilitychange", () => {
      if (!document.hidden && state.taskId && !state.eventSource) pollTask(state.taskEpoch);
    });
  }

  async function initialize() {
    loadSelection();
    renderSelectedCount();
    bindEvents();
    await Promise.all([loadCatalog(), loadSources()]);
    if (state.selected.size) updateSteps("recipe");
    try {
      const latest = await api.get("/api/director/jobs/status");
      if (latest.batch?.task_id && latest.batch.status !== "idle") {
        state.taskId = latest.batch.task_id;
        renderTask(latest.batch);
        if (!latest.batch.terminal) startTaskUpdates();
      }
    } catch (_) { /* no prior task */ }
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", initialize);
  else initialize();
})();
