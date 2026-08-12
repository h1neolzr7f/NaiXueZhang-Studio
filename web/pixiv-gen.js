// 由 pixiv.js 拆分：生成进度、预览合并与侧边栏。经典脚本，依赖 pixiv.js 中的全局函数，加载顺序须先于 pixiv.js。

function openGenLightbox(url) {
  if (!url) return;
  const box = document.getElementById("pxLightbox");
  const img = document.getElementById("pxLightboxImg");
  img.src = url;
  box.classList.add("open");
  box.hidden = false;
}

document.getElementById("pxLightbox").addEventListener("click", () => {
  const box = document.getElementById("pxLightbox");
  box.classList.remove("open");
  box.hidden = true;
  document.getElementById("pxLightboxImg").src = "";
});

function buildGenProgress(queue, batch, pipeline, pixivJob) {
  const lines = [];
  let pct = null;
  if (queue.status === "running") {
    lines.push({ label: "NAI 生图", text: queue.message || "正在请求 NovelAI…", active: true });
    pct = null;
  }
  if (batch.status === "running") {
    const done = Number(batch.done) || 0;
    const total = Number(batch.total) || 0;
    const cur = batch.current_work_id ? `#${batch.current_work_id}` : "";
    lines.push({
      label: "批量生成",
      text: `${done}/${total} ${cur} ${batch.message || ""}`.trim(),
      active: true,
    });
    if (total > 0) pct = Math.round((done / total) * 100);
  }
  const pipe = pipeline.job || pipeline || {};
  if (pipe.status === "running") {
    const done = Number(pipe.done) || 0;
    const total = Number(pipe.total) || 0;
    lines.push({
      label: "后处理",
      text: `${done}/${total} ${pipe.message || ""}`.trim(),
      active: true,
    });
    if (total > 0) pct = Math.max(pct || 0, Math.round((done / total) * 100));
  }
  if (pixivJob.status === "running") {
    lines.push({
      label: "起号上传",
      text: pixivJob.message || pixivJob.step || "运行中…",
      active: true,
    });
    pct = null;
  }
  if (!lines.length) {
    lines.push({ label: "状态", text: "空闲 — 主图库试生成会实时出现在下方", active: false });
  }
  return { lines, pct };
}

function genItemUrl(item) {
  if (!item || item._pending) return "";
  return (item.processed_url || item.image_url || "");
}

function batchItemId(bi) {
  const fn = String(bi.filename || "").replace(/\.png$/i, "");
  if (fn) return fn;
  const url = String(bi.image_url || "");
  const name = url.split("/").pop() || "";
  return name.replace(/\.png$/i, "").split("?")[0];
}

function mergePreviewItems(candidates, batch, queue) {
  const map = new Map();
  for (const c of candidates || []) {
    if (c && c.id && !c._pending) map.set(c.id, { ...c });
  }
  for (const bi of batch.items || []) {
    if (!bi || !bi.ok) continue;
    const id = batchItemId(bi);
    if (!id || map.has(id)) continue;
    map.set(id, {
      id,
      image_url: bi.image_url || "",
      processed_url: bi.processed_url || "",
      work_id: bi.work_id,
      created_at: bi.finished_at || batch.finished_at || batch.started_at || "",
      _fromBatch: true,
    });
  }
  const list = Array.from(map.values());
  list.sort((a, b) => String(b.created_at || b.id).localeCompare(String(a.created_at || a.id)));

  const pending = [];
  const batchRunning = batch.status === "running" && batch.generate !== false;
  if (batchRunning && batch.current_work_id) {
    pending.push({
      id: `__pending_batch_${batch.current_work_id}`,
      _pending: true,
      work_id: batch.current_work_id,
      page_index: batch.current_page_index,
      message: batch.message || `批量处理 #${batch.current_work_id}`,
      phase: batch.current_phase || "running",
    });
  } else if (queue.status === "running") {
    pending.push({
      id: `__pending_queue_${queue.work_id || "nai"}`,
      _pending: true,
      work_id: queue.work_id,
      message: queue.message || "NAI 生图中…",
      phase: "generate",
    });
  }
  return [...pending, ...list];
}

function renderBatchLog(batch, queue) {
  const panel = document.getElementById("pxGenBatchPanel");
  const summary = document.getElementById("pxGenBatchSummary");
  const log = document.getElementById("pxGenBatchLog");
  const items = batch.items || [];
  const show = batch.status === "running" || items.length > 0;
  if (!show || batch.status === "idle") {
    panel.hidden = true;
    return;
  }
  panel.hidden = false;
  const done = Number(batch.done) || 0;
  const total = Number(batch.total) || 0;
  const head = batch.status === "running"
    ? `进行中 ${done}/${total} · 成功 ${batch.ok_count || 0} · 失败 ${batch.fail_count || 0}`
    : (batch.message || `完成 ${done}/${total}`);
  summary.textContent = [head, batch.message].filter(Boolean).join(" · ");

  const rows = [];
  if (batch.status === "running" && batch.current_work_id) {
    const qmsg = queue.status === "running" ? ` · ${escapeHtml(queue.message)}` : "";
    rows.push(
      `<div class="px-gen-batch-row active">… #${escapeHtml(batch.current_work_id)} p${escapeHtml(batch.current_page_index ?? 0)} ${escapeHtml(batch.message || "处理中")}${qmsg}</div>`
    );
  }
  items.slice().reverse().slice(0, 14).forEach((item) => {
    const ok = !!item.ok;
    const skipped = !!item.skipped;
    const icon = ok ? "✓" : (skipped ? "⊘" : "✗");
    const cls = ok ? "ok" : "fail";
    const wid = item.work_id != null ? `#${escapeHtml(item.work_id)}` : "";
    const pi = item.page_index != null ? ` p${escapeHtml(item.page_index)}` : "";
    let msg = item.message || (ok ? "完成" : "失败");
    if (ok && item.image_url) msg = "生图完成";
    if (item.preview_only) msg = item.message || "草稿就绪";
    const sub = [
      item.summary ? String(item.summary) : "",
      item.error ? String(item.error) : "",
      ok && item.image_url ? "已入预览轮播" : "",
    ].filter(Boolean).join(" · ");
    rows.push(
      `<div class="px-gen-batch-row ${cls}"><span>${icon}</span> ${wid}${pi} ${escapeHtml(msg)}${sub ? `<small>${escapeHtml(sub)}</small>` : ""}</div>`
    );
  });
  log.innerHTML = rows.join("") || '<div class="px-gen-batch-row">等待任务输出…</div>';
}

function previewSignature(merged) {
  return (merged || []).map((i) => (
    i._pending
      ? `p:${i.work_id}:${i.message}:${i.phase}`
      : i.id
  )).join(",");
}

function batchSignature(batch, queue) {
  return [
    batch.status,
    batch.done,
    batch.total,
    batch.current_work_id,
    batch.current_phase,
    batch.message,
    (batch.items || []).length,
    queue.status,
    queue.message,
    queue.work_id,
  ].join("|");
}

function syncGenFocusIndex(items) {
  if (!items.length) {
    genFocusIndex = 0;
    return;
  }
  if (items[0] && items[0]._pending) {
    genFocusIndex = 0;
    return;
  }
  if (selectedId) {
    const hit = items.findIndex((x) => x.id === selectedId && !x._pending);
    if (hit >= 0) {
      genFocusIndex = hit;
      return;
    }
  }
  genFocusIndex = Math.max(0, Math.min(genFocusIndex, items.length - 1));
}

function updateGenWheelFocus({ scroll = true } = {}) {
  const wheel = document.getElementById("pxGenWheel");
  const meta = document.getElementById("pxGenWheelMeta");
  const pickBtn = document.getElementById("pxGenWheelPick");
  const items = wheel.querySelectorAll(".px-gen-wheel-item");
  if (!items.length) {
    meta.textContent = "—";
    if (pickBtn) pickBtn.disabled = true;
    return;
  }
  const idx = Math.max(0, Math.min(genFocusIndex, items.length - 1));
  genFocusIndex = idx;
  const item = candidatesItems[idx];
  const isPending = !!(item && item._pending);
  items.forEach((el, i) => {
    el.classList.toggle("is-focus", i === idx);
    el.classList.toggle("is-selected", el.dataset.id === selectedId);
  });
  if (scroll) {
    const target = items[idx];
    const horizontal = window.matchMedia("(max-width: 1100px)").matches;
    target.scrollIntoView({
      behavior: "smooth",
      block: horizontal ? "nearest" : "center",
      inline: horizontal ? "center" : "nearest",
    });
  }
  if (item) {
    if (isPending) {
      meta.innerHTML = [
        `<div><strong>生成中</strong></div>`,
        item.work_id ? `<div>源作品 #${escapeHtml(item.work_id)}</div>` : "",
        item.page_index != null ? `<div>页码 p${escapeHtml(item.page_index)}</div>` : "",
        `<div>${escapeHtml(item.message || "等待出图…")}</div>`,
        `<div style="color:#7a8499">出图后会自动出现在此处</div>`,
      ].filter(Boolean).join("");
      if (pickBtn) {
        pickBtn.disabled = true;
        pickBtn.textContent = "生成中…";
      }
      document.getElementById("pxGenWheelZoom").disabled = true;
    } else {
      const when = (item.created_at || "").replace("T", " ").slice(0, 16);
      meta.innerHTML = [
        `<div><strong>${idx + 1}/${candidatesItems.length}</strong> ${escapeHtml(item.id)}</div>`,
        item.work_id ? `<div>源作品 #${escapeHtml(item.work_id)}</div>` : "",
        when ? `<div>${escapeHtml(when)}</div>` : "",
        item._fromBatch ? `<div style="color:#6ea8fe">来自批量任务</div>` : "",
        item.id === selectedId ? `<div style="color:#3ddc97">已选中用于上传</div>` : "",
      ].filter(Boolean).join("");
      if (pickBtn) {
        pickBtn.disabled = false;
        pickBtn.textContent = item.id === selectedId ? "已选中" : "选这张";
      }
      document.getElementById("pxGenWheelZoom").disabled = false;
    }
  }
}

function setGenFocusIndex(next, opts) {
  if (!candidatesItems.length) return;
  const max = candidatesItems.length - 1;
  genFocusIndex = Math.max(0, Math.min(next, max));
  updateGenWheelFocus(opts);
}

function mountGenWheelEvents() {
  if (genWheelMounted) return;
  genWheelMounted = true;
  const wrap = document.getElementById("pxGenWheelWrap");
  const wheel = document.getElementById("pxGenWheel");
  wrap.addEventListener("wheel", (e) => {
    if (!candidatesItems.length) return;
    if (!wrap.matches(":hover")) return;
    e.preventDefault();
    const step = Math.abs(e.deltaY) >= 80 ? 1 : 1;
    setGenFocusIndex(genFocusIndex + (e.deltaY > 0 ? step : -step));
  }, { passive: false });
  wheel.addEventListener("click", (e) => {
    const card = e.target.closest(".px-gen-wheel-item");
    if (!card) return;
    const idx = Number(card.dataset.index);
    if (!Number.isFinite(idx)) return;
    if (idx === genFocusIndex) {
      const item = candidatesItems[idx];
      if (item && !item._pending) selectImage(item);
    } else {
      setGenFocusIndex(idx);
    }
  });
  wheel.addEventListener("dblclick", (e) => {
    const card = e.target.closest(".px-gen-wheel-item");
    if (!card) return;
    const idx = Number(card.dataset.index);
    const item = candidatesItems[idx];
    if (item && !item._pending) openGenLightbox(genItemUrl(item));
  });
  document.getElementById("pxGenWheelPick").addEventListener("click", () => {
    const item = candidatesItems[genFocusIndex];
    if (item) selectImage(item);
  });
  document.getElementById("pxGenWheelPickSeries").addEventListener("click", () => {
    if (!selectedGroupId) return;
    const group = launchGroups.find((g) => g.group_id === selectedGroupId);
    if (group) selectGroup(group);
  });
  document.getElementById("pxGenWheelZoom").addEventListener("click", () => {
    const item = candidatesItems[genFocusIndex];
    if (item) openGenLightbox(genItemUrl(item));
  });
  window.addEventListener("keydown", (e) => {
    if (!candidatesItems.length) return;
    const active = document.activeElement;
    const tag = (active && active.tagName) || "";
    if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT") return;
    // 焦点在按钮/链接/可交互控件上时不劫持键盘，把方向键与 Enter 还给页面导航
    if (active && active !== document.body) {
      if (tag === "BUTTON" || tag === "A" || active.isContentEditable) return;
      if (typeof active.closest === "function" && active.closest("button, a, [role='button'], [tabindex]")) return;
    }
    if (e.key === "ArrowDown" || e.key === "PageDown") {
      e.preventDefault();
      setGenFocusIndex(genFocusIndex + 1);
    } else if (e.key === "ArrowUp" || e.key === "PageUp") {
      e.preventDefault();
      setGenFocusIndex(genFocusIndex - 1);
    } else if (e.key === "Enter" && genFocusIndex >= 0) {
      const item = candidatesItems[genFocusIndex];
      if (item) selectImage(item);
    }
  });
}

function renderGenSidebarItems(items) {
  const wheel = document.getElementById("pxGenWheel");
  const countEl = document.getElementById("pxGenCount");
  countEl.textContent = String(items.length);
  candidatesItems = items.slice(0, 40);
  syncGenFocusIndex(candidatesItems);
  wheel.innerHTML = "";
  if (!candidatesItems.length) {
    wheel.innerHTML = '<div class="px-gen-empty">暂无生成图<br>去主图库作品页试生成</div>';
    document.getElementById("pxGenWheelMeta").textContent = "—";
    document.getElementById("pxGenWheelPick").disabled = true;
    return;
  }
  candidatesItems.forEach((item, idx) => {
    const card = document.createElement("div");
    card.className = "px-gen-wheel-item" + (item._pending ? " is-pending" : "");
    card.dataset.index = String(idx);
    card.dataset.id = item.id || "";
    card.title = item._pending
      ? (item.message || "生成中")
      : ((item.created_at || "").replace("T", " ").slice(0, 16) || item.id);
    if (item._pending) {
      const spin = document.createElement("div");
      spin.className = "px-gen-pending-spin";
      card.appendChild(spin);
      const badge = document.createElement("div");
      badge.className = "px-gen-pending-badge";
      badge.textContent = item.phase === "prepare" ? "配方处理" : "NAI 生图";
      card.appendChild(badge);
      const text = document.createElement("div");
      text.className = "px-gen-pending-text";
      text.textContent = item.message || `作品 #${item.work_id || "?"}`;
      card.appendChild(text);
    } else {
      const img = document.createElement("img");
      img.loading = "lazy";
      img.src = genItemUrl(item) + "?t=" + encodeURIComponent(item.created_at || idx);
      img.alt = item.id || "";
      card.appendChild(img);
    }
    wheel.appendChild(card);
  });
  mountGenWheelEvents();
  requestAnimationFrame(() => updateGenWheelFocus({ scroll: false }));
  requestAnimationFrame(() => updateGenWheelFocus({ scroll: true }));
}

async function refreshGenSidebar() {
  const statusLine = document.getElementById("pxGenStatusLine");
  const barWrap = document.getElementById("pxGenBarWrap");
  const bar = document.getElementById("pxGenBar");
  try {
    const [queueRes, batchRes, pipeRes, pixivRes, candRes] = await Promise.all([
      window.ApiClient.raw("/api/nai/queue").then((r) => r.json()).catch(() => ({ queue: {} })),
      window.ApiClient.raw("/api/plugin/char-swap/batch/status").then((r) => r.json()).catch(() => ({ batch: {} })),
      window.ApiClient.raw("/api/pipeline/status").then((r) => r.json()).catch(() => ({ job: {} })),
      window.ApiClient.raw("/api/pixiv/status").then((r) => r.json()).catch(() => ({ job: {} })),
      window.ApiClient.raw("/api/pixiv/candidates").then((r) => r.json()).catch(() => ({ items: [] })),
    ]);
    const queue = queueRes.queue || {};
    const batch = batchRes.batch || {};
    const pipeline = pipeRes || {};
    const pixivJob = pixivRes.job || {};
    const items = candRes.items || [];

    const { lines, pct } = buildGenProgress(queue, batch, pipeline, pixivJob);
    statusLine.innerHTML = lines.map((ln) =>
      `<div class="px-gen-progress-line${ln.active ? " active" : ""}"><strong>${escapeHtml(ln.label)}</strong> ${escapeHtml(ln.text)}</div>`
    ).join("");

    const busy = lines.some((ln) => ln.active);
    genPollBusy = busy;
    if (busy && pct != null) {
      barWrap.hidden = false;
      bar.style.width = `${pct}%`;
    } else if (busy) {
      barWrap.hidden = false;
      bar.style.width = "35%";
    } else {
      barWrap.hidden = true;
      bar.style.width = "0%";
    }

    renderBatchLog(batch, queue);

    const merged = mergePreviewItems(items, batch, queue);
    const sig = items.map((i) => i.id).join(",");
    const pSig = previewSignature(merged);
    const bSig = batchSignature(batch, queue);

    if (pSig !== lastPreviewSig || bSig !== lastBatchSig) {
      lastPreviewSig = pSig;
      lastBatchSig = bSig;
      const hadPending = candidatesItems.some((x) => x._pending);
      const hasPending = merged.some((x) => x._pending);
      if (hasPending && !hadPending) genFocusIndex = 0;
      renderGenSidebarItems(merged);
    } else if (busy) {
      updateGenWheelFocus({ scroll: false });
    }

    if (sig !== lastCandidateSig || items.length !== lastCandidateCount) {
      lastCandidateSig = sig;
      lastCandidateCount = items.length;
      await loadCandidates({ silent: true, items, skipSidebar: true });
    }
  } catch (e) {
    statusLine.innerHTML = `<div class="px-gen-progress-line">${escapeHtml(e.message || "加载失败")}</div>`;
  }
}

function scheduleGenSidebarPoll() {
  if (genSidebarTimer) clearTimeout(genSidebarTimer);
  if (document.hidden) {
    genPollPaused = true;
    genSidebarTimer = setTimeout(scheduleGenSidebarPoll, 5000);
    return;
  }
  genPollPaused = false;
  const delay = genPollBusy ? 900 : 4000;
  genSidebarTimer = setTimeout(async () => {
    await refreshGenSidebar();
    scheduleGenSidebarPoll();
  }, delay);
}

