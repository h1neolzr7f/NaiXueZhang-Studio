// 由 pixiv.js 拆分：候选分组与选择模式。经典脚本，依赖 pixiv.js 中的全局函数，加载顺序须先于 pixiv.js。

function getSelectedGroups() {
  return Array.from(selectedGroupIds)
    .map((gid) => launchGroups.find((g) => String(g.group_id || "") === gid))
    .filter(Boolean);
}

function getMergedSelectionStats() {
  const groups = getSelectedGroups();
  const total = groups.reduce((n, g) => n + (g.image_ids || []).length, 0);
  return { groups, count: groups.length, total };
}

function refreshGroupSelectionUI() {
  const multi = selectedGroupIds.size > 1;
  document.querySelectorAll(".px-cand-group, .px-gen-group-item").forEach((el) => {
    const gid = el.dataset.groupId || "";
    const on = selectedGroupIds.has(gid);
    el.classList.toggle("selected", on);
    el.classList.toggle("multi-selected", multi && on);
  });
}

function updatePickStatusFromGroups(focusGroup) {
  const groups = getSelectedGroups();
  const pickEl = document.getElementById("pickStatus");
  if (!groups.length) {
    setStatus(pickEl, "从生成图库选择一张或一个系列（按系列支持 Ctrl 多选）");
    return;
  }
  if (groups.length === 1) {
    const g = groups[0];
    const title = g.source_title || (g.work_id ? `作品 ${g.work_id}` : "独立生成");
    setStatus(
      pickEl,
      `已选系列：${title}（${(g.image_ids || []).length} 张）`,
      "ok"
    );
    return;
  }
  const total = groups.reduce((n, g) => n + (g.image_ids || []).length, 0);
  const focusTitle = focusGroup
    ? (focusGroup.source_title || (focusGroup.work_id ? `作品 ${focusGroup.work_id}` : "独立生成"))
    : "";
  setStatus(
    pickEl,
    `已选 ${groups.length} 个系列，共 ${total} 张将合并为一篇投稿${focusTitle ? ` · 预览：${focusTitle}` : ""}（Ctrl+点击可增减）`,
    "ok"
  );
}

function renderPipelineStatus(item) {
  const el = document.getElementById("pipelineStatus");
  if (selectedGroupIds.size > 1) {
    const groups = getSelectedGroups();
    const total = groups.reduce((n, g) => n + (g.image_ids || []).length, 0);
    const pending = groups.reduce((n, g) => n + (Number(g.pipeline_pending) || 0), 0);
    setStatus(
      el,
      `已选 ${groups.length} 个系列 · 合并共 ${total} 张\n` +
        (pending ? `其中 ${pending} 张仍待补后处理` : "合并投稿所需后处理已齐全（或已关闭）"),
      pending ? "" : "ok"
    );
    return;
  }
  if (selectedGroupId && selectedImageIds.length > 1) {
    const group = launchGroups.find((g) => g.group_id === selectedGroupId);
    const pending = group ? Number(group.pipeline_pending) || 0 : 0;
    setStatus(
      el,
      `当前系列：${selectedGroupId} · 共 ${selectedImageIds.length} 张\n` +
        (pending ? `其中 ${pending} 张仍待补后处理` : "系列内后处理已齐全（或已关闭）"),
      pending ? "" : "ok"
    );
    return;
  }
  if (!item || !item.pipeline) {
    setStatus(el, "未选图");
    return;
  }
  const p = item.pipeline;
  const labels = [
    ["超分", p.upscale],
    ["打码", p.mosaic_no_target ? true : p.mosaic],
    ["元数据", p.metadata],
  ].map(([name, ok]) => `${name}:${ok ? "✓" : "待补"}`);
  const miss = (p.missing || []).filter((x) => x !== "mosaic_failed").join("、") || "无";
  const skip = p.mosaic_skip || "";
  const noTarget = p.mosaic_no_target ? "\n打码检查：未检测到需打码部位，已作为正常图继续处理" : "";
  const extra = skip ? `\n打码失败：${skip.replace(/^mosaic:skip\(/, "").replace(/\)$/, "")}` : noTarget;
  setStatus(
    el,
    `当前图后处理：${labels.join(" · ")}\n缺失步骤：${miss}${extra}`,
    (p.missing || []).length ? "" : "ok"
  );
}

function setPickMode(mode) {
  pickMode = mode === "single" ? "single" : "series";
  document.getElementById("pickTabSeries").classList.toggle("active", pickMode === "series");
  document.getElementById("pickTabSingle").classList.toggle("active", pickMode === "single");
  document.getElementById("pickSeriesPane").hidden = pickMode !== "series";
  document.getElementById("pickSinglePane").hidden = pickMode !== "single";
}

function renderCandidateGroups(groups) {
  launchGroups = groups || [];
  const box = document.getElementById("candidateGroups");
  const side = document.getElementById("pxGenGroups");
  box.innerHTML = "";
  side.innerHTML = "";
  if (!launchGroups.length) {
    box.innerHTML = '<div class="px-status">暂无生成系列，请先去作品页试生成</div>';
    side.hidden = true;
    return;
  }
  side.hidden = false;
  launchGroups.forEach((group) => {
    const title = group.source_title || (group.work_id ? `作品 ${group.work_id}` : "独立生成");
    const when = (group.latest_at || "").replace("T", " ").slice(0, 16);
    const card = document.createElement("div");
    const gid = String(group.group_id || "");
    const on = selectedGroupIds.has(gid);
    card.className = "px-cand-group"
      + (on ? " selected" : "")
      + (selectedGroupIds.size > 1 && on ? " multi-selected" : "");
    card.dataset.groupId = gid;
    card.title = `${title} · ${group.count} 张 · Ctrl+点击多选`;
    const img = document.createElement("img");
    img.loading = "lazy";
    img.src = group.cover_url || "";
    img.alt = title;
    card.appendChild(img);
    const badge = document.createElement("div");
    badge.className = "badge";
    badge.textContent = String(group.count || 0);
    card.appendChild(badge);
    const sm = document.createElement("small");
    sm.textContent = when || title;
    card.appendChild(sm);
    card.addEventListener("click", (e) => toggleGroupSelect(group, e));
    box.appendChild(card);

    const mini = document.createElement("div");
    mini.className = "px-gen-group-item"
      + (on ? " selected" : "")
      + (selectedGroupIds.size > 1 && on ? " multi-selected" : "");
    mini.title = `${title} · ${group.count} 张 · Ctrl+点击多选`;
    mini.dataset.groupId = gid;
    const miniImg = document.createElement("img");
    miniImg.loading = "lazy";
    miniImg.src = group.cover_url || "";
    miniImg.alt = title;
    mini.appendChild(miniImg);
    const miniBadge = document.createElement("div");
    miniBadge.className = "badge";
    miniBadge.textContent = String(group.count || 0);
    mini.appendChild(miniBadge);
    mini.addEventListener("click", (e) => toggleGroupSelect(group, e));
    side.appendChild(mini);
  });
}

async function loadGroups(opts) {
  const silent = opts && opts.silent;
  const res = await window.ApiClient.raw("/api/pixiv/groups", { timeoutMs: 60000 });
  const data = await res.json();
  renderCandidateGroups(data.groups || []);
  if (!silent && !selectedId && !selectedGroupIds.size && launchGroups.length) {
    setStatus(document.getElementById("pickStatus"), `共 ${launchGroups.length} 个系列，点封面选整组（Ctrl+点击可多选）`);
  }
  const bootGroup = qs("group");
  if (bootGroup && !selectedGroupIds.size) {
    const hit = launchGroups.find((g) => String(g.group_id) === bootGroup);
    if (hit) await selectGroup(hit, { silent: true });
  }
}

