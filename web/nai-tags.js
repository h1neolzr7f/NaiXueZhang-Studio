(() => {
  "use strict";

  const state = { facet: "character", selected: new Set(), page: 1, pageSize: 60 };
  const byId = (id) => document.getElementById(id);
  const cloud = byId("tagCloud");
  const grid = byId("workGrid");
  const status = byId("status");
  const selectedTags = byId("selectedTags");
  let facetRequestId = 0;
  let worksRequestId = 0;
  const imageUrl = (path) => "/data/images/" + String(path || "")
    .replaceAll("\\", "/").split("/").filter(Boolean).map(encodeURIComponent).join("/");
  const selectionKey = (facet, tag) => `${facet}:${tag}`;
  const selectedValues = () => Array.from(state.selected);

  function updateSelectionSummary() {
    const count = state.selected.size;
    byId("selectionCount").textContent = count ? `已组合 ${count} 个标签` : "已选择 0 项";
    byId("clearSelections").disabled = count === 0;
  }

  function renderSelected() {
    selectedTags.replaceChildren();
    state.selected.forEach((key) => {
      const separator = key.indexOf(":");
      const facet = key.slice(0, separator);
      const tag = key.slice(separator + 1);
      const button = document.createElement("button");
      button.className = "tag-chip active";
      button.type = "button";
      button.textContent = `${facet} · ${tag} ×`;
      button.setAttribute("aria-label", `移除 ${tag}`);
      button.addEventListener("click", () => {
        state.selected.delete(key);
        state.page = 1;
        renderSelected();
        loadFacet();
        loadWorks();
      });
      selectedTags.append(button);
    });
    updateSelectionSummary();
  }

  async function loadFacet() {
    const requestId = ++facetRequestId;
    const facet = state.facet;
    status.textContent = "正在读取本地索引…";
    try {
      const data = await ApiClient.get("/api/nai-tags?facet=" + encodeURIComponent(facet) + "&limit=120");
      if (requestId !== facetRequestId || facet !== state.facet) return;
      cloud.replaceChildren();
      for (const item of data.items || []) {
        const key = selectionKey(facet, item.tag);
        const button = document.createElement("button");
        button.className = `tag-chip${state.selected.has(key) ? " active" : ""}`;
        button.type = "button";
        button.append(document.createTextNode(item.display_tag || item.tag));
        const count = document.createElement("small");
        count.textContent = String(item.work_count || 0);
        button.append(count);
        button.addEventListener("click", () => {
          if (state.selected.has(key)) state.selected.delete(key);
          else state.selected.add(key);
          state.page = 1;
          renderSelected();
          loadFacet();
          loadWorks();
        });
        cloud.append(button);
      }
      if (!cloud.children.length) {
        const empty = document.createElement("p");
        empty.className = "status-line";
        empty.textContent = "暂无分类数据；完成一次 Pixiv NAI 采集后会自动建立索引。";
        cloud.append(empty);
      }
      status.textContent = `${(data.items || []).length} 个热门标签`;
    } catch (error) {
      if (requestId !== facetRequestId || facet !== state.facet) return;
      cloud.replaceChildren();
      const empty = document.createElement("p");
      empty.className = "status-line";
      empty.textContent = "当前分面暂时不可用，请稍后重试。";
      cloud.append(empty);
      status.textContent = `索引读取失败：${error.message || error}`;
    }
  }

  function workCard(work, index) {
    const card = document.createElement("a");
    card.className = "work-card";
    card.href = `/i/${encodeURIComponent(work.id)}`;
    const badge = document.createElement("span");
    badge.className = "card-index";
    badge.textContent = `T${String((state.page - 1) * state.pageSize + index + 1).padStart(3, "0")}`;
    card.append(badge);
    if (work.thumb_path) {
      const image = document.createElement("img");
      image.loading = "lazy";
      image.alt = work.title || `Pixiv #${work.id}`;
      image.src = imageUrl(work.thumb_path);
      card.append(image);
    } else {
      const placeholder = document.createElement("div");
      placeholder.className = "image-placeholder";
      placeholder.textContent = "NAI";
      card.append(placeholder);
    }
    const body = document.createElement("div");
    body.className = "work-card-body";
    const title = document.createElement("strong");
    title.className = "work-card-title";
    title.textContent = work.title || `Pixiv #${work.id}`;
    const meta = document.createElement("div");
    meta.className = "work-card-meta";
    const bookmarks = document.createElement("span");
    bookmarks.textContent = `收藏 ${work.total_bookmarks || 0}`;
    const pages = document.createElement("span");
    pages.textContent = `${work.image_count || 1} 页`;
    meta.append(bookmarks, pages);
    body.append(title, meta);
    card.append(body);
    return card;
  }

  function renderWorks(data) {
    const items = Array.isArray(data.items) ? data.items : [];
    const total = Number(data.total || 0);
    grid.replaceChildren(...items.map(workCard));
    if (!items.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const heading = document.createElement("strong");
      heading.textContent = "没有命中这个标签组合";
      const copy = document.createElement("span");
      copy.textContent = "移除一个条件或切换分类分面，再看看图谱里有什么。";
      empty.append(heading, copy);
      grid.append(empty);
    }
    const pages = Math.max(1, Math.ceil(total / state.pageSize));
    byId("resultTitle").textContent = state.selected.size ? `${total} 件组合结果` : `${total} 件图库作品`;
    byId("pageText").textContent = `${String(state.page).padStart(2, "0")} / ${String(pages).padStart(2, "0")}`;
    byId("previous").disabled = state.page <= 1;
    byId("next").disabled = state.page >= pages;
  }

  async function loadWorks() {
    const requestId = ++worksRequestId;
    const query = new URLSearchParams({
      page: String(state.page),
      page_size: String(state.pageSize),
      sort: byId("sort").value,
    });
    selectedValues().forEach((value) => query.append("selection", value));
    try {
      const data = await ApiClient.get(`/api/nai-tags/works?${query}`);
      if (requestId !== worksRequestId) return;
      renderWorks(data);
    } catch (error) {
      if (requestId !== worksRequestId) return;
      grid.replaceChildren();
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.textContent = `分类查询失败：${String(error.message || error)}`;
      grid.append(empty);
    }
  }

  document.querySelectorAll("[data-facet]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll("[data-facet]").forEach((item) => {
      const active = item === button;
      item.classList.toggle("active", active);
      item.setAttribute("aria-pressed", String(active));
    });
    state.facet = button.dataset.facet;
    loadFacet();
  }));
  byId("clearSelections").addEventListener("click", () => {
    state.selected.clear();
    state.page = 1;
    renderSelected();
    loadFacet();
    loadWorks();
  });
  byId("sort").addEventListener("change", () => { state.page = 1; loadWorks(); });
  byId("previous").addEventListener("click", () => {
    if (state.page > 1) { state.page -= 1; loadWorks(); }
  });
  byId("next").addEventListener("click", () => { state.page += 1; loadWorks(); });

  renderSelected();
  loadFacet();
  loadWorks();
})();
