(() => {
  "use strict";

  const state = { page: 1, pageSize: 60, total: 0, promptToCopy: "" };
  let galleryRequestId = 0;
  let detailRequestId = 0;
  const byId = (id) => document.getElementById(id);
  const titleOf = (work) => String(work.title || work.illust_title || `Pixiv #${work.id || ""}`);
  const authorOf = (work) => String(work.userName || work.user_name || work.userAccount || work.user_account || "未知画师");
  const imageUrl = (path) => "/data/images/" + String(path || "")
    .replaceAll("\\", "/").split("/").filter(Boolean).map(encodeURIComponent).join("/");

  async function json(path) {
    const response = await fetch(path, { headers: { Accept: "application/json" } });
    if (!response.ok) throw new Error((await response.text()) || `HTTP ${response.status}`);
    return response.json();
  }

  function asObject(value) {
    if (value && typeof value === "object") return value;
    if (typeof value !== "string" || !value.trim()) return {};
    try {
      const parsed = JSON.parse(value);
      return parsed && typeof parsed === "object" ? parsed : {};
    } catch (_error) {
      return {};
    }
  }

  function firstText(...values) {
    for (const value of values) {
      if (typeof value === "string" && value.trim()) return value.trim();
      if (typeof value === "number" && Number.isFinite(value)) return String(value);
    }
    return "";
  }

  function promptInfo(image) {
    const ai = asObject(image.ai_json);
    const metadata = asObject(image.metadata);
    const rawComment = image.comment ?? ai.Comment ?? metadata.Comment;
    const comment = asObject(rawComment);
    const prompt = firstText(
      image.prompt_text,
      image.prompt,
      ai.Description,
      ai.description,
      ai.prompt,
      metadata.Description,
      metadata.prompt,
      comment.prompt,
      typeof rawComment === "string" && !Object.keys(comment).length ? rawComment : "",
    );
    const negative = firstText(
      image.negative_prompt,
      ai.negative_prompt,
      ai.uc,
      metadata.negative_prompt,
      metadata.uc,
      comment.negative_prompt,
      comment.uc,
    );
    return {
      prompt,
      negative,
      model: firstText(image.model, ai.model, metadata.model, comment.model),
      sampler: firstText(image.sampler, ai.sampler, metadata.sampler, comment.sampler),
      seed: firstText(image.seed, ai.seed, metadata.seed, comment.seed),
    };
  }

  function normalizeTags(value) {
    let current = value;
    if (typeof current === "string") {
      try { current = JSON.parse(current); }
      catch (_error) { current = current.split(","); }
    }
    if (current && !Array.isArray(current) && typeof current === "object") {
      current = Object.values(current).flat();
    }
    if (!Array.isArray(current)) return [];
    return current.map((item) => {
      if (typeof item === "string") return item.trim();
      if (!item || typeof item !== "object") return "";
      return firstText(item.display_tag, item.tag, item.name, item.value);
    }).filter(Boolean);
  }

  function renderTagList(values) {
    const wrapper = document.createElement("div");
    wrapper.className = "tag-list";
    const tags = Array.from(new Set(values.flatMap(normalizeTags))).slice(0, 80);
    for (const tag of tags) {
      const chip = document.createElement("span");
      chip.textContent = tag;
      wrapper.append(chip);
    }
    if (!tags.length) {
      const empty = document.createElement("span");
      empty.textContent = "暂无标签";
      wrapper.append(empty);
    }
    return wrapper;
  }

  function detailBlock(title, content) {
    const section = document.createElement("section");
    section.className = "detail-block";
    const heading = document.createElement("h3");
    heading.textContent = title;
    section.append(heading, content);
    return section;
  }

  function renderPromptPanel(image) {
    const info = promptInfo(image || {});
    const panel = document.createElement("div");
    const prompt = document.createElement("pre");
    prompt.className = "prompt-text";
    prompt.textContent = info.prompt || "这张图片没有可读取的正向提示词。";
    panel.append(detailBlock("Positive prompt", prompt));
    if (info.negative) {
      const negative = document.createElement("pre");
      negative.className = "prompt-text";
      negative.textContent = info.negative;
      panel.append(detailBlock("Negative prompt", negative));
    }
    return { panel, info };
  }

  function card(work, index) {
    const article = document.createElement("article");
    article.className = "archive-card";
    article.tabIndex = 0;
    article.setAttribute("role", "link");
    article.setAttribute("aria-label", `打开 ${titleOf(work)}`);

    const badge = document.createElement("span");
    badge.className = "card-index";
    badge.textContent = `A${String((state.page - 1) * state.pageSize + index + 1).padStart(3, "0")}`;
    article.append(badge);

    if (work.thumb_path) {
      const image = document.createElement("img");
      image.loading = "lazy";
      image.alt = titleOf(work);
      image.src = imageUrl(work.thumb_path);
      article.append(image);
    } else {
      const placeholder = document.createElement("div");
      placeholder.className = "image-placeholder";
      placeholder.textContent = "NAI";
      placeholder.setAttribute("aria-label", "暂无缩略图");
      article.append(placeholder);
    }

    const body = document.createElement("div");
    body.className = "archive-card-body";
    const title = document.createElement("strong");
    title.className = "archive-card-title";
    title.textContent = titleOf(work);
    const meta = document.createElement("div");
    meta.className = "archive-card-meta";
    const author = document.createElement("span");
    author.textContent = authorOf(work);
    const pages = document.createElement("span");
    pages.textContent = `${work.image_count || work.pageCount || 1} 页`;
    meta.append(author, pages);
    body.append(title, meta);
    article.append(body);

    const activate = () => openDetail(work.id);
    article.addEventListener("click", activate);
    article.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activate();
      }
    });
    return article;
  }

  async function load() {
    const requestId = ++galleryRequestId;
    const query = new URLSearchParams({
      q: byId("query").value.trim(),
      prompt: byId("prompt").value.trim(),
      page: String(state.page),
      page_size: String(state.pageSize),
      sort: byId("sort").value,
    });
    byId("status").textContent = "正在扫描本地索引…";
    try {
      const result = await json(`/api/ai_works_search?${query}`);
      if (requestId !== galleryRequestId) return;
      const items = Array.isArray(result.items) ? result.items : [];
      state.total = Number(result.total || 0);
      const grid = byId("grid");
      grid.replaceChildren(...items.map(card));
      if (!items.length) {
        const empty = document.createElement("div");
        empty.className = "empty-state";
        const heading = document.createElement("strong");
        heading.textContent = "这里暂时没有匹配作品";
        const copy = document.createElement("span");
        copy.textContent = "换一个关键词，或前往采集舱导入经过 NAI 元数据验证的 Pixiv 图片。";
        empty.append(heading, copy);
        grid.append(empty);
      }
      const pages = Math.max(1, Math.ceil(state.total / state.pageSize));
      byId("resultCount").textContent = `${state.total} 件作品 · ${pages} 页`;
      byId("status").textContent = items.length ? `本页已载入 ${items.length} 件` : "当前组合无结果";
      byId("pageText").textContent = `${String(state.page).padStart(2, "0")} / ${String(pages).padStart(2, "0")}`;
      byId("previous").disabled = state.page <= 1;
      byId("next").disabled = state.page >= pages;
    } catch (error) {
      if (requestId !== galleryRequestId) return;
      const grid = byId("grid");
      const empty = document.createElement("div");
      empty.className = "empty-state";
      const heading = document.createElement("strong");
      heading.textContent = "本地索引暂时无法读取";
      const copy = document.createElement("span");
      copy.textContent = "请确认图库服务仍在运行，然后重新检索。";
      empty.append(heading, copy);
      grid.replaceChildren(empty);
      byId("status").textContent = `读取失败：${error.message || error}`;
      byId("resultCount").textContent = "索引不可用";
      byId("previous").disabled = true;
      byId("next").disabled = true;
    }
  }

  function renderDetail(detail, id) {
    const work = detail.work || {};
    const images = Array.isArray(detail.images) ? detail.images : [];
    const body = byId("detailBody");
    const layout = document.createElement("div");
    layout.className = "detail-layout";
    const imageColumn = document.createElement("div");
    imageColumn.className = "detail-images";
    for (const item of images) {
      if (!item.local_path) continue;
      const image = document.createElement("img");
      image.loading = "lazy";
      image.alt = `${titleOf(work)} 第 ${Number(item.page_index || 0) + 1} 页`;
      image.src = imageUrl(item.local_path);
      imageColumn.append(image);
    }
    if (!imageColumn.children.length) {
      const placeholder = document.createElement("div");
      placeholder.className = "image-placeholder";
      placeholder.textContent = "NAI";
      imageColumn.append(placeholder);
    }

    const aside = document.createElement("aside");
    aside.className = "detail-aside";
    const identity = document.createElement("p");
    identity.className = "prompt-text";
    identity.textContent = `${authorOf(work)}\nPixiv #${work.id || id}\n${work.image_count || images.length || 1} 页`;
    aside.append(detailBlock("Archive record", identity));

    const firstImage = images.find((item) => promptInfo(item).prompt) || images[0] || {};
    const promptResult = renderPromptPanel(firstImage);
    aside.append(...promptResult.panel.children);
    state.promptToCopy = promptResult.info.prompt;
    byId("copyPrompt").disabled = !state.promptToCopy;

    const facts = [promptResult.info.model, promptResult.info.sampler, promptResult.info.seed]
      .filter(Boolean).join(" · ");
    if (facts) {
      const model = document.createElement("p");
      model.className = "prompt-text";
      model.textContent = facts;
      aside.append(detailBlock("Generation facts", model));
    }
    aside.append(detailBlock("NAI tags", renderTagList([
      work.tags,
      ...images.map((item) => item.parsed_nai_tags || item.nai_tags),
    ])));
    layout.append(imageColumn, aside);
    body.replaceChildren(layout);
  }

  async function openDetail(id) {
    if (!id) return;
    const requestId = ++detailRequestId;
    const dialog = byId("detail");
    state.promptToCopy = "";
    byId("copyPrompt").disabled = true;
    byId("detailTitle").textContent = `Pixiv #${id}`;
    byId("detailBody").textContent = "正在读取作品详情…";
    if (!dialog.open) dialog.showModal();
    try {
      const detail = await json(`/api/work/${encodeURIComponent(id)}`);
      if (requestId !== detailRequestId) return;
      byId("detailTitle").textContent = titleOf(detail.work || {});
      renderDetail(detail, id);
    } catch (error) {
      if (requestId !== detailRequestId) return;
      byId("detailBody").textContent = `读取失败：${error.message || error}`;
    }
  }

  function closeDetail() {
    byId("detail").close();
  }

  byId("searchForm").addEventListener("submit", (event) => {
    event.preventDefault(); state.page = 1; load();
  });
  byId("sort").addEventListener("change", () => { state.page = 1; load(); });
  byId("previous").addEventListener("click", () => {
    if (state.page > 1) { state.page -= 1; load(); }
  });
  byId("next").addEventListener("click", () => { state.page += 1; load(); });
  byId("closeDetail").addEventListener("click", closeDetail);
  byId("copyPrompt").addEventListener("click", async () => {
    if (!state.promptToCopy) return;
    try {
      await navigator.clipboard.writeText(state.promptToCopy);
      byId("copyPrompt").textContent = "已复制";
      window.setTimeout(() => { byId("copyPrompt").textContent = "复制提示词"; }, 1400);
    } catch (_error) {
      byId("copyPrompt").textContent = "复制失败";
    }
  });
  byId("detail").addEventListener("click", (event) => {
    if (event.target === byId("detail")) closeDetail();
  });
  byId("detail").addEventListener("close", () => {
    detailRequestId += 1;
    state.promptToCopy = "";
    byId("copyPrompt").disabled = true;
  });

  const direct = location.pathname.match(/^\/i\/(\d+)$/);
  load().then(() => { if (direct) openDetail(direct[1]); });
})();
