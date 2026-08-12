import {
  DRAFT_KEY,
  aiTypeFrom,
  externalHref,
  imagesFrom,
  localReferenceId,
  localReferenceLabel,
  normalizeDetail,
  normalizeDraftResponse,
  promptTextFrom,
  qualificationFrom,
  tagsFrom,
  titleFrom,
  visibleOnlineItems,
  workFrom,
  workIdFrom,
} from "./tag-assets-model.js?v=8c6d30a93d";

const DISPLAY_BATCH = 12;
const ONLINE_SORTS = new Set(["popular", "recent", "relevance"]);
const ONLINE_LIBRARY_PAGE = window.location.pathname.startsWith("/aitag-library");
const quickTags = ["arknights", "NovelAI", "Skadi", "Texas", "artist", "1girl"];
const state = {
  source: ONLINE_LIBRARY_PAGE ? "aitag-online" : "local",
  page: 1,
  items: [],
  visible: 0,
  total: 0,
  hasMore: false,
  searched: false,
  busy: false,
  requestSeq: 0,
  controller: null,
  selectedItem: null,
  detail: null,
  imageIndex: 0,
  sourceCandidateId: "",
  sourceCandidateSlotIndex: 0,
  targetMode: "none",
  targetItems: [],
  targetReferenceId: "",
  targetReferenceLabel: "",
  targetCandidate: null,
  targetImageIndex: 0,
  targetCandidateId: "",
  targetCandidateSlotIndex: 0,
};
const $ = (id) => document.getElementById(id);

function api(path, options) {
  if (!window.ApiClient) throw new Error("ApiClient 未加载");
  return window.ApiClient.request(path, options || {});
}

const escapeHtml = window.escapeHtml;

function setError(message) {
  const host = $("assetError");
  if (!host) return;
  host.textContent = message || "";
  host.toggleAttribute("hidden", !message);
}

function setBusy(value) {
  state.busy = Boolean(value);
  $("assetLoading")?.toggleAttribute("hidden", !state.busy);
  ["assetSearch", "assetClear", "assetPrepareDraft"].forEach((id) => {
    const element = $(id);
    if (element) element.disabled = state.busy;
  });
  $("assetResults")?.setAttribute("aria-busy", String(state.busy));
}

function onlineFilters() {
  return {
    naiOnly: $("assetNaiOnly")?.checked !== false,
    safeOnly: $("assetSafeOnly")?.checked !== false,
  };
}

function setSourceUi() {
  const online = state.source === "aitag-online";
  $("assetSortField")?.toggleAttribute("hidden", !online);
  $("sourceBadge").textContent = online ? "AITag 在线" : "本地离线";
  $("sourceStatus").textContent = online ? "按需读取元数据" : "无需联网";
  $("sourceHint").textContent = online
    ? "筛选条件会传给后端；前端仅做显示增强。在线候选必须显式保存后才能成为换角目标。"
    : "本地来源可离线搜索；在线服务不可用时会回退到这里。";
  ["assetNaiOnly", "assetSafeOnly"].forEach((id) => { if ($(id)) $(id).disabled = !online; });
}

function fallbackToLocal(message) {
  if (ONLINE_LIBRARY_PAGE) {
    state.items = [];
    state.visible = 0;
    state.total = 0;
    state.hasMore = false;
    state.searched = true;
    renderResults();
    setError(`${message}；这是独立在线库，请稍后重试，或前往“本地资产”继续使用离线角色库。`);
    return;
  }
  state.source = "local";
  $("assetSource").value = "local";
  state.items = [];
  state.visible = 0;
  state.total = 0;
  state.hasMore = false;
  state.searched = false;
  state.detail = null;
  state.selectedItem = null;
  $("assetDetailPanel")?.setAttribute("hidden", "");
  $("assetDraftPanel")?.setAttribute("hidden", "");
  setSourceUi();
  renderResults();
  setError(`${message}；已回退到本地库，请输入关键词后搜索。`);
}

function renderQuickTags() {
  const host = $("quickTags");
  host.innerHTML = quickTags.map((tag) => `<button class="asset-quick-button" type="button" data-q="${escapeHtml(tag)}">${escapeHtml(tag)}</button>`).join("");
  host.querySelectorAll("[data-q]").forEach((button) => button.addEventListener("click", () => {
    $("assetQuery").value = button.dataset.q || "";
    search({ reset: true });
  }));
}

function cardQualification(item) {
  const qualification = qualificationFrom(item);
  const reason = qualification.reasons.join("；");
  return `<span class="asset-tag" title="${escapeHtml(reason)}">${escapeHtml(qualification.label)}</span>`;
}

function renderItems() {
  const host = $("assetResults");
  const online = state.source === "aitag-online";
  host.innerHTML = state.items.slice(0, state.visible).map((item) => {
    const work = workFrom(item);
    const workId = workIdFrom(item);
    const title = titleFrom(item);
    const image = imagesFrom(item)[0];
    const tags = tagsFrom(item).slice(0, 9);
    const meta = [online ? "AITag Online" : "Local Offline", aiTypeFrom(item), work.creator || work.user_name, work.create_date]
      .filter(Boolean).join(" · ");
    const selected = online && state.detail?.workId === workId ? " selected" : "";
    const actions = online
      ? `<button type="button" class="asset-view" data-work-id="${escapeHtml(workId)}">查看详情 / 选图</button><a href="${escapeHtml(externalHref(item))}" target="_blank" rel="noreferrer">原页</a>`
      : `<a href="/i/${encodeURIComponent(workId)}">打开本地作品</a><a href="/references?q=${encodeURIComponent(title)}">查找本地角色</a>`;
    return `<article class="asset-card${selected}" data-card-work-id="${escapeHtml(workId)}">
      ${image?.thumbUrl ? `<img class="asset-card-image" loading="lazy" referrerpolicy="no-referrer" src="${escapeHtml(image.thumbUrl)}" alt="" />` : ""}
      <div class="asset-card-title">#${escapeHtml(workId)} ${escapeHtml(title)}</div>
      <div class="asset-card-meta">${escapeHtml(meta)}</div>
      <div class="asset-card-tags">${online ? cardQualification(item) : ""}${tags.map((tag) => `<span class="asset-tag">${escapeHtml(tag)}</span>`).join("")}</div>
      <div class="asset-card-actions">${actions}</div>
    </article>`;
  }).join("");
  host.querySelectorAll(".asset-view").forEach((button) => button.addEventListener("click", () => openDetail(button.dataset.workId)));
}

function renderResults() {
  renderItems();
  const shown = Math.min(state.visible, state.items.length);
  if (!state.searched) {
    $("resultInfo").textContent = state.source === "aitag-online"
      ? "AITag 在线 · 搜索角色，或留空浏览热门榜单。"
      : "本地离线 · 输入关键词开始搜索。";
  } else {
    $("resultInfo").textContent = `${state.source === "aitag-online" ? "AITag 在线" : "本地离线"} · 已显示 ${shown} / ${Number(state.total || state.items.length).toLocaleString()}`;
  }
  const noItems = state.searched && !state.items.length && !state.busy;
  $("assetEmpty")?.toggleAttribute("hidden", !noItems);
  const canReveal = state.visible < state.items.length;
  const load = $("assetLoadMore");
  load.toggleAttribute("hidden", !(canReveal || state.hasMore));
  load.textContent = canReveal ? "显示更多" : "加载下一页";
}

async function hydrateOnlinePreviews(requestSeq, items) {
  const pending = (items || []).slice(0, DISPLAY_BATCH).filter((item) => !imagesFrom(item).length);
  if (!pending.length) return;
  let cursor = 0;
  let changed = false;
  const worker = async () => {
    while (cursor < pending.length) {
      const item = pending[cursor++];
      const workId = workIdFrom(item);
      if (!workId) continue;
      try {
        const result = await api(`/api/nai/aitag/work/${encodeURIComponent(workId)}`, { timeoutMs: 30000 });
        if (requestSeq !== state.requestSeq) return;
        const detail = normalizeDetail(result, item);
        if (!detail.images.length) continue;
        const index = state.items.findIndex((candidate) => workIdFrom(candidate) === workId);
        if (index >= 0) {
          state.items[index] = {
            ...detail.payload,
            work: { ...detail.work, images: detail.images },
            images: detail.images,
          };
          changed = true;
        }
      } catch (_) {
        // A failed preview must not hide an otherwise usable online work.
      }
    }
  };
  await Promise.all(Array.from({ length: Math.min(4, pending.length) }, () => worker()));
  if (changed && requestSeq === state.requestSeq) renderResults();
}

function buildSearchUrl(source, query, page, purpose) {
  const online = source === "aitag-online";
  const url = new URL(online ? "/api/nai/aitag/search" : "/api/ai_works_search", window.location.origin);
  if (query) url.searchParams.set("q", query);
  url.searchParams.set("page", String(page));
  url.searchParams.set("page_size", online ? (purpose === "target" ? "24" : "60") : "24");
  if (online) {
    const { naiOnly, safeOnly } = onlineFilters();
    const requestedSort = purpose === "target" ? "relevance" : ($("assetSort")?.value || "popular");
    url.searchParams.set("sort", ONLINE_SORTS.has(requestedSort) ? requestedSort : "popular");
    url.searchParams.set("nai_only", String(naiOnly));
    url.searchParams.set("safe_only", String(safeOnly));
  }
  return url.pathname + url.search;
}

async function search({ reset = true } = {}) {
  const source = $("assetSource")?.value || "local";
  const query = ($("assetQuery")?.value || "").trim();
  state.source = source;
  setSourceUi();
  if (reset && source === "local" && !query) {
    state.items = [];
    state.visible = 0;
    state.total = 0;
    state.searched = false;
    renderResults();
    return;
  }
  if (state.busy) return;
  state.controller?.abort();
  const requestSeq = ++state.requestSeq;
  state.page = reset ? 1 : state.page + 1;
  if (reset) { state.items = []; state.visible = 0; }
  state.searched = true;
  setError("");
  setBusy(true);
  state.controller = new AbortController();
  try {
    const data = await api(buildSearchUrl(source, query, state.page, "source"), {
      signal: state.controller.signal,
      timeoutMs: 45000,
    });
    if (requestSeq !== state.requestSeq) return;
    const raw = data.items || data.works || [];
    const incoming = source === "aitag-online" ? visibleOnlineItems(raw, onlineFilters()) : raw;
    const combined = reset ? incoming : state.items.concat(incoming);
    const seen = new Set();
    state.items = combined.filter((item) => {
      const key = workIdFrom(item);
      if (!key || seen.has(key)) return false;
      seen.add(key);
      return true;
    });
    state.visible = reset ? Math.min(DISPLAY_BATCH, state.items.length) : Math.min(state.items.length, state.visible + DISPLAY_BATCH);
    state.total = Number(data.total ?? state.items.length);
    state.hasMore = Boolean(data.has_more);
    $("sourceStatus").textContent = source === "aitag-online" ? "在线已连接 · 只读发现" : "本地索引已读取";
    renderResults();
    if (source === "aitag-online") void hydrateOnlinePreviews(requestSeq, incoming);
  } catch (error) {
    if (error?.name === "AbortError" || requestSeq !== state.requestSeq) return;
    if (!reset) state.page = Math.max(1, state.page - 1);
    if (source === "aitag-online" && Number(error?.status || 0) !== 400) {
      fallbackToLocal(`AITag 在线搜索不可用：${error.message || error}`);
    } else {
      setError(`搜索失败：${error.message || error}`);
      renderResults();
    }
  } finally {
    if (requestSeq === state.requestSeq) {
      state.controller = null;
      setBusy(false);
      renderResults();
    }
  }
}

function selectSourceImage(imageIndex) {
  if (!state.detail) return;
  const image = state.detail.images.find((candidate) => candidate.imageIndex === Number(imageIndex)) || state.detail.images[0];
  if (!image) return;
  state.imageIndex = image.imageIndex;
  const matchingCandidates = state.detail.characterCandidates.filter((candidate) => candidate.imageIndex === state.imageIndex);
  const selectedCandidate = matchingCandidates[0] || null;
  state.sourceCandidateId = selectedCandidate?.candidateId || "";
  state.sourceCandidateSlotIndex = selectedCandidate?.slotIndex || 0;
  if (selectedCandidate && $("assetSlot")) $("assetSlot").value = String(selectedCandidate.slotIndex);
  $("assetDetailImage").innerHTML = `<img referrerpolicy="no-referrer" src="${escapeHtml(image.url || image.thumbUrl)}" alt="${escapeHtml(state.detail.title)}" />`;
  $("assetImageChoices").querySelectorAll("[data-image-index]").forEach((button) => {
    const selected = Number(button.dataset.imageIndex) === state.imageIndex;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-selected", String(selected));
  });
  renderSourceCandidates();
}

function renderSourceCandidates() {
  const host = $("assetSourceCandidates");
  if (!host || !state.detail) return;
  const candidates = state.detail.characterCandidates.filter((candidate) => candidate.imageIndex === state.imageIndex);
  host.toggleAttribute("hidden", candidates.length === 0);
  host.innerHTML = candidates.map((candidate) => `<button type="button" class="asset-candidate-choice${candidate.candidateId === state.sourceCandidateId ? " active" : ""}" data-source-candidate-id="${escapeHtml(candidate.candidateId)}">
    <b>${escapeHtml(candidate.label)}</b><small>槽位 ${candidate.slotIndex + 1}${candidate.role ? ` · ${escapeHtml(candidate.role)}` : ""} · ${escapeHtml(candidate.caption)}</small>
  </button>`).join("");
  host.querySelectorAll("[data-source-candidate-id]").forEach((button) => button.addEventListener("click", () => {
    const selected = candidates.find((candidate) => candidate.candidateId === button.dataset.sourceCandidateId);
    if (!selected) return;
    state.sourceCandidateId = selected.candidateId;
    state.sourceCandidateSlotIndex = selected.slotIndex;
    if ($("assetSlot")) $("assetSlot").value = String(selected.slotIndex);
    renderSourceCandidates();
  }));
}

function renderDetail() {
  const detail = state.detail;
  if (!detail) return;
  $("assetDetailName").textContent = detail.title;
  $("assetDetailSource").textContent = typeof detail.source === "string"
    ? detail.source
    : String(detail.source.label || detail.source.name || "AITag Online");
  const reasons = detail.qualification.reasons.length ? ` · ${detail.qualification.reasons.join("；")}` : "";
  $("assetDetailMeta").textContent = `#${detail.workId} · ${detail.qualification.label}${reasons}`;
  $("assetDetailLicense").textContent = `授权：${detail.license.name} · 状态：${detail.license.status} · 来源：${detail.license.sourceUrl}`;
  $("assetDetailTags").innerHTML = detail.tags.slice(0, 18).map((tag) => `<span class="asset-tag">${escapeHtml(tag)}</span>`).join("");
  $("assetDetailPrompt").textContent = promptTextFrom(detail.payload) || "此作品未公开可显示的 Prompt；草稿由后端 recipe 生成。";
  $("assetExternalLink").href = externalHref(detail.payload);
  $("assetImageChoices").innerHTML = detail.images.map((image, position) => `<button type="button" class="asset-image-choice" data-image-index="${image.imageIndex}" role="option" aria-selected="false">
    ${image.thumbUrl ? `<img loading="lazy" referrerpolicy="no-referrer" src="${escapeHtml(image.thumbUrl)}" alt="" />` : ""}<span>第 ${position + 1} 张 · index ${image.imageIndex}</span>
  </button>`).join("");
  $("assetImageChoices").querySelectorAll("[data-image-index]").forEach((button) => button.addEventListener("click", () => selectSourceImage(button.dataset.imageIndex)));
  state.imageIndex = detail.images[0]?.imageIndex || 0;
  selectSourceImage(state.imageIndex);
  $("assetDraftSafety").textContent = `${detail.generationCalls} 次生成调用`;
  $("assetPrepareDraft").disabled = detail.qualification.qualified === false;
  $("assetDetailPanel").removeAttribute("hidden");
  $("assetDraftPanel").removeAttribute("hidden");
  document.querySelectorAll("[data-card-work-id]").forEach((card) => card.classList.toggle("selected", card.dataset.cardWorkId === detail.workId));
}

async function openDetail(workId) {
  const fallbackItem = state.items.find((item) => workIdFrom(item) === String(workId)) || { work_id: String(workId) };
  state.selectedItem = fallbackItem;
  $("assetDetailPanel").removeAttribute("hidden");
  $("assetDetailLoading").removeAttribute("hidden");
  setError("");
  try {
    const result = await api(`/api/nai/aitag/work/${encodeURIComponent(workId)}`, { timeoutMs: 45000 });
    state.detail = normalizeDetail(result, fallbackItem);
    if (!state.detail.images.length) throw new Error("在线作品没有可选图片");
    renderDetail();
    $("assetDetailPanel").scrollIntoView({ behavior: "smooth", block: "start" });
  } catch (error) {
    fallbackToLocal(`AITag 在线详情不可用：${error.message || error}`);
  } finally {
    $("assetDetailLoading")?.setAttribute("hidden", "");
  }
}

async function importOnlineReference(workId, imageIndex, label, candidateId = "", slotIndex = 0) {
  const body = { work_id: workId, image_index: Number(imageIndex), slot_index: Number(slotIndex) };
  if (candidateId) body.candidate_id = candidateId;
  const result = await api("/api/nai/aitag/import", {
    method: "POST",
    body,
    timeoutMs: 60000,
  });
  const referenceId = String(result.reference_id || "").trim();
  if (!referenceId) throw new Error(result.message || "在线候选未返回稳定 reference_id");
  return { referenceId, label: result.label || label || referenceId };
}

async function saveSourceReference() {
  if (!state.detail) return;
  const button = $("assetSaveSource");
  button.disabled = true;
  button.textContent = "正在保存…";
  try {
    const saved = await importOnlineReference(
      state.detail.workId,
      state.imageIndex,
      state.detail.title,
      state.sourceCandidateId,
      state.sourceCandidateSlotIndex,
    );
    button.textContent = `已保存：${saved.label}`;
    $("assetTargetStatus").textContent = `当前在线图片已保存到本地角色库：${saved.label}`;
  } catch (error) {
    button.disabled = false;
    button.textContent = "保存当前图片到本地角色库";
    setError(`保存失败：${error.message || error}`);
  }
}

function resetTargetSelection(message) {
  state.targetItems = [];
  state.targetReferenceId = "";
  state.targetReferenceLabel = "";
  state.targetCandidate = null;
  state.targetImageIndex = 0;
  state.targetCandidateId = "";
  state.targetCandidateSlotIndex = 0;
  $("assetTargetResults").replaceChildren();
  $("assetTargetSelection").setAttribute("hidden", "");
  $("assetTargetSelection").replaceChildren();
  $("assetTargetStatus").textContent = message || "尚未选择目标角色。";
}

function setTargetMode() {
  state.targetMode = $("assetTargetSource")?.value || "none";
  const needsSearch = state.targetMode !== "none";
  $("assetTargetQueryField")?.toggleAttribute("hidden", !needsSearch);
  $("assetTargetSearch")?.toggleAttribute("hidden", !needsSearch);
  resetTargetSelection(state.targetMode === "none"
    ? "当前不换角色；将建立原在线作品的可编辑草稿。"
    : (state.targetMode === "local" ? "搜索并选择一个稳定的本地角色资料。" : "在线候选必须先显式保存为本地角色，才能用于换角。"));
}

function targetThumbnail(item) {
  return item?.thumb_url || item?.image_url || imagesFrom(item)[0]?.thumbUrl || "";
}

function renderTargetItems() {
  const online = state.targetMode === "aitag-online";
  $("assetTargetResults").innerHTML = state.targetItems.map((item) => {
    const id = online ? workIdFrom(item) : localReferenceId(item);
    const label = online ? titleFrom(item) : localReferenceLabel(item);
    const detail = online ? `AITag #${workIdFrom(item)} · 需显式保存` : (item.copyright || item.trigger || item.source || "本地角色");
    const thumb = targetThumbnail(item);
    return `<button type="button" class="asset-target-item" data-target-id="${escapeHtml(id)}">
      ${thumb ? `<img loading="lazy" referrerpolicy="no-referrer" src="${escapeHtml(thumb)}" alt="" />` : "<span></span>"}
      <span><b>${escapeHtml(label)}</b><small>${escapeHtml(detail)}</small></span>
    </button>`;
  }).join("");
  $("assetTargetResults").querySelectorAll("[data-target-id]").forEach((button) => button.addEventListener("click", () => selectTarget(button.dataset.targetId)));
}

async function searchTargets() {
  if (state.targetMode === "none") return;
  const query = ($("assetTargetQuery")?.value || "").trim();
  $("assetTargetSearch").disabled = true;
  $("assetTargetStatus").textContent = "正在查找目标角色…";
  try {
    if (state.targetMode === "local") {
      const params = new URLSearchParams({ limit: "24", offset: "0" });
      if (query) params.set("q", query);
      const data = await api(`/api/nai/references?${params}`);
      state.targetItems = data.items || [];
    } else {
      const data = await api(buildSearchUrl("aitag-online", query, 1, "target"), { timeoutMs: 45000 });
      state.targetItems = visibleOnlineItems(data.items || data.works || [], onlineFilters());
    }
    renderTargetItems();
    $("assetTargetStatus").textContent = state.targetItems.length ? `找到 ${state.targetItems.length} 个候选，请选择。` : "没有找到候选角色。";
  } catch (error) {
    if (state.targetMode === "aitag-online" && Number(error?.status || 0) !== 400) fallbackToLocal(`AITag 在线候选不可用：${error.message || error}`);
    else $("assetTargetStatus").textContent = `目标搜索失败：${error.message || error}`;
  } finally {
    $("assetTargetSearch").disabled = false;
  }
}

function selectLocalTarget(item) {
  state.targetReferenceId = localReferenceId(item);
  state.targetReferenceLabel = localReferenceLabel(item);
  state.targetCandidate = null;
  const host = $("assetTargetSelection");
  host.innerHTML = `<strong>已选择本地目标：${escapeHtml(state.targetReferenceLabel)}</strong><span>稳定 reference_id：${escapeHtml(state.targetReferenceId)}</span>`;
  host.removeAttribute("hidden");
  $("assetTargetStatus").textContent = `将把 ${state.targetReferenceLabel} 换入槽位 ${Number($("assetSlot").value || 0) + 1}。`;
}

function renderOnlineCandidateSelection() {
  const detail = state.targetCandidate;
  const host = $("assetTargetSelection");
  const candidates = detail.characterCandidates.length
    ? detail.characterCandidates
    : detail.images.map((image, position) => ({ candidateId: "", imageIndex: image.imageIndex, slotIndex: 0, label: `第 ${position + 1} 张`, caption: "" }));
  host.innerHTML = `<strong>在线候选：${escapeHtml(detail.title)}</strong>
    <span>先选择明确的图片/角色槽候选，再显式保存到本地角色库。</span>
    <div class="asset-target-candidate-images">${candidates.map((candidate) => {
      const image = detail.images.find((item) => item.imageIndex === candidate.imageIndex);
      const active = candidate.candidateId
        ? candidate.candidateId === state.targetCandidateId
        : candidate.imageIndex === state.targetImageIndex;
      return `<button type="button" class="asset-image-choice${active ? " active" : ""}" data-target-candidate-id="${escapeHtml(candidate.candidateId)}" data-target-image-index="${candidate.imageIndex}" data-target-slot-index="${candidate.slotIndex}">
      ${image?.thumbUrl ? `<img loading="lazy" referrerpolicy="no-referrer" src="${escapeHtml(image.thumbUrl)}" alt="" />` : ""}<span>${escapeHtml(candidate.label)} · 槽位 ${candidate.slotIndex + 1}</span></button>`;
    }).join("")}</div>
    <button id="assetSaveTarget" type="button" class="asset-button asset-button-primary">保存到本地并选为目标</button>`;
  host.removeAttribute("hidden");
  host.querySelectorAll("[data-target-image-index]").forEach((button) => button.addEventListener("click", () => {
    state.targetImageIndex = Number(button.dataset.targetImageIndex || 0);
    state.targetCandidateId = String(button.dataset.targetCandidateId || "");
    state.targetCandidateSlotIndex = Number(button.dataset.targetSlotIndex || 0);
    renderOnlineCandidateSelection();
  }));
  $("assetSaveTarget").addEventListener("click", saveOnlineTarget);
}

async function selectTarget(id) {
  $("assetTargetResults").querySelectorAll("[data-target-id]").forEach((button) => button.classList.toggle("active", button.dataset.targetId === id));
  if (state.targetMode === "local") {
    const item = state.targetItems.find((candidate) => localReferenceId(candidate) === id);
    if (item) selectLocalTarget(item);
    return;
  }
  const fallback = state.targetItems.find((candidate) => workIdFrom(candidate) === id);
  if (!fallback) return;
  $("assetTargetStatus").textContent = "正在读取在线候选图片…";
  try {
    const result = await api(`/api/nai/aitag/work/${encodeURIComponent(id)}`, { timeoutMs: 45000 });
    state.targetCandidate = normalizeDetail(result, fallback);
    state.targetImageIndex = state.targetCandidate.images[0]?.imageIndex || 0;
    const firstCandidate = state.targetCandidate.characterCandidates[0] || null;
    state.targetCandidateId = firstCandidate?.candidateId || "";
    state.targetCandidateSlotIndex = firstCandidate?.slotIndex || 0;
    if (firstCandidate) state.targetImageIndex = firstCandidate.imageIndex;
    state.targetReferenceId = "";
    renderOnlineCandidateSelection();
    $("assetTargetStatus").textContent = "在线候选尚未保存，当前不能作为换角目标。";
  } catch (error) {
    fallbackToLocal(`AITag 在线候选详情不可用：${error.message || error}`);
  }
}

async function saveOnlineTarget() {
  if (!state.targetCandidate) return;
  const button = $("assetSaveTarget");
  button.disabled = true;
  button.textContent = "正在保存…";
  try {
    const saved = await importOnlineReference(
      state.targetCandidate.workId,
      state.targetImageIndex,
      state.targetCandidate.title,
      state.targetCandidateId,
      state.targetCandidateSlotIndex,
    );
    state.targetReferenceId = saved.referenceId;
    state.targetReferenceLabel = saved.label;
    $("assetTargetSelection").innerHTML = `<strong>已保存并选择：${escapeHtml(saved.label)}</strong><span>本地 reference_id：${escapeHtml(saved.referenceId)}</span>`;
    $("assetTargetStatus").textContent = `在线候选已成为稳定本地角色，将换入槽位 ${Number($("assetSlot").value || 0) + 1}。`;
  } catch (error) {
    button.disabled = false;
    button.textContent = "保存到本地并选为目标";
    $("assetTargetStatus").textContent = `保存在线候选失败：${error.message || error}`;
  }
}

async function prepareDraft() {
  if (!state.detail) return setError("请先打开一个 AITag 在线作品并选择图片。");
  if (state.detail.qualification.qualified === false) {
    return setError(`该作品不符合在线草稿条件：${state.detail.qualification.reasons.join("；") || "后端未通过资格校验"}`);
  }
  if (state.targetMode !== "none" && !state.targetReferenceId) {
    return setError(state.targetMode === "aitag-online" ? "请先把在线候选显式保存到本地角色库。" : "请先选择一个本地角色。 ");
  }
  const button = $("assetPrepareDraft");
  button.disabled = true;
  button.textContent = "正在准备零生成草稿…";
  setError("");
  const payload = {
    image_index: Number(state.imageIndex),
    slot_index: Number($("assetSlot")?.value || 0),
  };
  if (state.targetReferenceId) payload.target_reference_id = state.targetReferenceId;
  try {
    const result = await api(`/api/nai/aitag/work/${encodeURIComponent(state.detail.workId)}/draft`, {
      method: "POST",
      body: payload,
      timeoutMs: 60000,
    });
    const normalized = normalizeDraftResponse(result);
    const storedDraft = {
      ...normalized.draft,
      draftId: normalized.draftId,
      recipe: normalized.recipe,
      sourceKind: "aitag-online",
      onlineReference: {
        workId: state.detail.workId,
        imageIndex: Number(state.imageIndex),
        title: state.detail.title,
        targetReferenceId: state.targetReferenceId || "",
      },
      generationCalls: normalized.generationCalls,
    };
    localStorage.setItem(DRAFT_KEY, JSON.stringify(storedDraft));
    $("assetDraftSafety").textContent = normalized.persisted
      ? `generation_calls: 0 · 服务端草稿 ${normalized.draftId}`
      : "generation_calls: 0 · 服务端保存失败，已保留浏览器本地草稿";
    window.location.href = normalized.studioUrl;
  } catch (error) {
    button.disabled = false;
    button.textContent = "建立草稿并打开 Studio →";
    if (Number(error?.status || 0) !== 400) fallbackToLocal(`AITag 在线草稿不可用：${error.message || error}`);
    else setError(`建立草稿失败：${error.message || error}`);
  }
}

function clearSearch() {
  $("assetQuery").value = "";
  state.items = [];
  state.visible = 0;
  state.total = 0;
  state.hasMore = false;
  state.searched = false;
  setError("");
  renderResults();
}

function loadMore() {
  if (state.busy) return;
  if (state.visible < state.items.length) {
    state.visible = Math.min(state.items.length, state.visible + DISPLAY_BATCH);
    renderResults();
  } else if (state.hasMore) search({ reset: false });
}

function bind() {
  if (ONLINE_LIBRARY_PAGE) {
    state.source = "aitag-online";
    $("assetSource").value = "aitag-online";
    $("assetSafeOnly").checked = false;
    $("assetSourceField")?.setAttribute("hidden", "");
    $("assetTitle").textContent = "AITag 在线库";
    $("assetControlsTitle").textContent = "浏览在线初始资产";
    $("resultInfo").textContent = "正在加载 AITag 在线热门资产…";
  }
  renderQuickTags();
  setSourceUi();
  renderResults();
  $("assetSearch").addEventListener("click", () => search({ reset: true }));
  $("assetLoadMore").addEventListener("click", loadMore);
  $("assetClear").addEventListener("click", clearSearch);
  $("assetSource").addEventListener("change", () => {
    state.source = $("assetSource").value || "local";
    clearSearch();
    setSourceUi();
  });
  $("assetSort").addEventListener("change", () => { if (state.source === "aitag-online") search({ reset: true }); });
  ["assetNaiOnly", "assetSafeOnly"].forEach((id) => $(id).addEventListener("change", () => {
    if (state.source === "aitag-online" && state.searched) search({ reset: true });
  }));
  $("assetQuery").addEventListener("keydown", (event) => { if (event.key === "Enter") search({ reset: true }); });
  $("assetDetailClose").addEventListener("click", () => {
    $("assetDetailPanel").setAttribute("hidden", "");
    $("assetDraftPanel").setAttribute("hidden", "");
  });
  $("assetSaveSource").addEventListener("click", saveSourceReference);
  $("assetTargetSource").addEventListener("change", setTargetMode);
  $("assetTargetSearch").addEventListener("click", searchTargets);
  $("assetTargetQuery").addEventListener("keydown", (event) => { if (event.key === "Enter") searchTargets(); });
  $("assetPrepareDraft").addEventListener("click", prepareDraft);
  setTargetMode();
  if (ONLINE_LIBRARY_PAGE) {
    const requestedWork = new URL(window.location.href).searchParams.get("work") || "";
    search({ reset: true }).then(() => {
      if (requestedWork) openDetail(requestedWork);
    });
  }
}

document.addEventListener("DOMContentLoaded", bind);
