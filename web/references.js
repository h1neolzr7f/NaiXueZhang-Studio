(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const DRAFT_KEY = "aitag.studio.draft.v1";
  const state = {
    mode: "characters",
    items: [],
    selectedId: "",
    selectedStyleId: "",
    detail: null,
    styleDetail: null,
    total: 0,
    offset: 0,
    limit: 60,
    hasMore: false,
    importing: false,
    fileRecords: null,
    requestSeq: 0,
    searchController: null,
    statsLoaded: false,
  };
  const GALLERY_LABELS = { site: "网站图库", codex: "法典图库", qqgroup: "QQ 群图库" };

  function api(path, options) {
    if (!window.ApiClient) throw new Error("ApiClient 未加载");
    return window.ApiClient.request(path, options || {});
  }

  function toast(message, kind) {
    if (!window.UiToast) return;
    if (kind === "ok") window.UiToast.ok(message);
    else if (kind === "err") window.UiToast.err(message);
    else window.UiToast.show(message);
  }

  function setStatus(message, kind) {
    const host = $("refStatus");
    if (!host) return;
    host.textContent = message || "";
    host.className = `ref-status${kind ? ` ${kind}` : ""}`;
  }

  function setImportProgress(done, total, message) {
    const box = $("refImportProgress");
    box?.classList.remove("hidden");
    const percent = total ? Math.round((done / total) * 100) : 0;
    if ($("refImportText")) $("refImportText").textContent = message || `${done} / ${total}`;
    if ($("refImportPercent")) $("refImportPercent").textContent = `${percent}%`;
    if ($("refImportBar")) $("refImportBar").style.width = `${percent}%`;
  }

  function imageInto(host, url, fallback) {
    if (!host) return;
    host.replaceChildren();
    if (!url) {
      const span = document.createElement("span");
      span.textContent = fallback || "NAI";
      host.appendChild(span);
      return;
    }
    const img = document.createElement("img");
    img.alt = "";
    img.loading = "lazy";
    img.referrerPolicy = "no-referrer";
    img.src = url;
    img.addEventListener("error", () => imageInto(host, "", fallback), { once: true });
    host.appendChild(img);
  }

  function labelGender(value) {
    return { female: "女性", male: "男性", other: "其他", unknown: "未标注" }[value] || "未标注";
  }

  function createItem(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `ref-item${item.reference_id === state.selectedId ? " active" : ""}`;
    button.dataset.referenceId = item.reference_id;

    const image = document.createElement("span");
    image.className = "ref-item-image";
    imageInto(image, item.thumb_url || item.image_url, "NAI");

    const body = document.createElement("span");
    body.className = "ref-item-body";
    const name = document.createElement("strong");
    name.textContent = item.label || item.source_id;
    const series = document.createElement("small");
    series.textContent = item.copyright || item.trigger || "未标注作品";
    const caption = document.createElement("span");
    caption.className = "ref-item-caption";
    caption.textContent = item.character_caption || "";
    const foot = document.createElement("span");
    foot.className = "ref-item-foot";
    const gender = document.createElement("span");
    gender.className = "ref-gender";
    gender.textContent = labelGender(item.gender);
    const popularity = document.createElement("span");
    popularity.textContent = item.popularity ? `◈ ${Number(item.popularity).toLocaleString()}` : item.source;
    foot.append(gender, popularity);
    body.append(name, series, caption, foot);
    button.append(image, body);
    button.addEventListener("click", () => selectReference(item.reference_id));
    return button;
  }

  function labelStyleKind(value) {
    return value === "artist" ? "画师" : "画风";
  }

  function createStyleItem(item) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = `ref-item ref-style-item${item.style_id === state.selectedStyleId ? " active" : ""}`;
    button.dataset.styleId = item.style_id;

    const mark = document.createElement("span");
    mark.className = `ref-style-mark ${item.kind === "artist" ? "artist" : "style"}`;
    mark.textContent = item.kind === "artist" ? "ART" : "STYLE";

    const body = document.createElement("span");
    body.className = "ref-item-body";
    const name = document.createElement("strong");
    name.textContent = item.label || item.tag || "未命名画风";
    const source = document.createElement("small");
    source.textContent = `${labelStyleKind(item.kind)} · ${item.source || "未标注来源"}`;
    const tag = document.createElement("span");
    tag.className = "ref-item-caption";
    tag.textContent = item.tag || "";
    const foot = document.createElement("span");
    foot.className = "ref-item-foot";
    const kind = document.createElement("span");
    kind.className = "ref-gender";
    kind.textContent = labelStyleKind(item.kind);
    const linked = document.createElement("span");
    linked.textContent = `关联 ${Number(item.linked_characters || 0)} 个角色`;
    foot.append(kind, linked);
    body.append(name, source, tag, foot);
    button.append(mark, body);
    button.addEventListener("click", () => selectStyleReference(item.style_id));
    return button;
  }

  function renderList(reset) {
    const host = $("refList");
    if (!host) return;
    if (reset) host.replaceChildren();
    if (!state.items.length) {
      host.innerHTML = state.mode === "styles"
        ? '<div class="ref-empty"><b>没有匹配的画风资料</b><span>换一个画师名、画风标签或来源试试。</span></div>'
        : '<div class="ref-empty"><b>没有匹配的角色</b><span>换一个名字、Trigger 或作品名试试。</span></div>';
    } else {
      if (state.mode === "styles") {
        const existing = new Set(Array.from(host.querySelectorAll("[data-style-id]")).map((node) => node.dataset.styleId));
        state.items.forEach((item) => {
          if (!existing.has(item.style_id)) host.appendChild(createStyleItem(item));
        });
      } else {
        const existing = new Set(Array.from(host.querySelectorAll("[data-reference-id]")).map((node) => node.dataset.referenceId));
        state.items.forEach((item) => {
          if (!existing.has(item.reference_id)) host.appendChild(createItem(item));
        });
      }
    }
    if ($("refResultMeta")) $("refResultMeta").textContent = `显示 ${state.items.length} / ${state.total}`;
    $("refLoadMore")?.classList.toggle("hidden", !state.hasMore);
  }

  function queryString(offset) {
    const params = new URLSearchParams({ limit: String(state.limit), offset: String(offset || 0) });
    const values = {
      q: ($("refQuery")?.value || "").trim(),
      source: $("refSource")?.value || "",
    };
    if (state.mode === "styles") values.kind = $("refStyleKind")?.value || "";
    else {
      values.gender = $("refGender")?.value || "";
      values.copyright = $("refCopyright")?.value || "";
    }
    Object.entries(values).forEach(([key, value]) => { if (value) params.set(key, value); });
    return params.toString();
  }

  async function search(reset) {
    const seq = ++state.requestSeq;
    const offset = reset ? 0 : state.offset;
    if (state.searchController) state.searchController.abort();
    state.searchController = new AbortController();
    setStatus("正在检索本地资料…");
    try {
      const endpoint = state.mode === "styles" ? "/api/nai/references/styles" : "/api/nai/references";
      const result = await api(`${endpoint}?${queryString(offset)}`, {
        signal: state.searchController.signal,
      });
      if (seq !== state.requestSeq) return;
      state.items = reset ? result.items : state.items.concat(result.items || []);
      state.total = Number(result.total || 0);
      state.offset = offset + (result.items || []).length;
      state.hasMore = Boolean(result.has_more);
      renderList(Boolean(reset));
      if (reset) syncSelectionAfterSearch();
      const noun = state.mode === "styles" ? "画风资料" : "角色资料";
      setStatus(state.total ? `本地检索完成，共 ${state.total.toLocaleString()} 条${noun}` : `没有找到匹配${noun}`, state.total ? "ok" : "");
    } catch (error) {
      if (error && error.name === "AbortError") return;
      if (seq !== state.requestSeq) return;
      setStatus(String(error.message || error), "err");
    }
  }

  function fillSelect(select, items, placeholder, valueKey, labeler) {
    if (!select) return;
    const current = select.value;
    select.replaceChildren(new Option(placeholder, ""));
    (items || []).forEach((item) => {
      const value = String(item[valueKey] || "");
      if (!value) return;
      select.appendChild(new Option(labeler(item), value));
    });
    if (Array.from(select.options).some((option) => option.value === current)) select.value = current;
  }

  async function loadStats() {
    try {
      const stats = await api("/api/nai/references/stats");
      const roleTotal = Number(stats.total || 0);
      const styleTotal = Number(
        stats.style_total ?? (stats.style_references || []).length,
      );
      if ($("refTotal")) $("refTotal").textContent = roleTotal.toLocaleString();
      if ($("refStyleTotal")) $("refStyleTotal").textContent = styleTotal.toLocaleString();
      if ($("refSourceCount")) $("refSourceCount").textContent = String((stats.sources || []).length);
      fillSelect($("refSource"), stats.sources, "全部来源", "source", (item) => `${item.label} · ${item.record_count}`);
      fillSelect($("refCopyright"), stats.copyrights, "全部作品", "name", (item) => `${item.name} · ${item.count}`);
      if (!state.statsLoaded && $("refImport")) $("refImport").open = roleTotal + styleTotal === 0;
      state.statsLoaded = true;
      return stats;
    } catch (error) {
      setStatus(String(error.message || error), "err");
      return null;
    }
  }

  function renderDetail(item, relatedStyles) {
    state.detail = item;
    state.styleDetail = null;
    $("refDetailEmpty")?.classList.add("hidden");
    $("refStyleDetail")?.classList.add("hidden");
    $("refDetail")?.classList.remove("hidden");
    imageInto($("refDetailImage"), item.thumb_url || item.image_url, "NAI");
    $("refDetailSource").textContent = `${item.source} · ${labelGender(item.gender)}`;
    $("refDetailName").textContent = item.label || item.source_id;
    $("refDetailSeries").textContent = item.copyright || "未标注作品";
    $("refDetailCaption").textContent = item.character_caption || "";
    $("refDetailTrigger").textContent = item.trigger || "—";
    $("refDetailSubject").textContent = item.base_subject_tag || "—";
    $("refDetailDialect").textContent = item.model_dialect || "—";
    $("refDetailPopularity").textContent = Number(item.popularity || 0).toLocaleString();
    $("refDetailId").textContent = item.source_id || "—";
    $("refDetailVersion").textContent = item.provenance?.version || "—";
    $("refDetailLicense").textContent = item.provenance?.license || "未标注";
    const aliases = Array.isArray(item.aliases) ? item.aliases : [];
    $("refAliasArea")?.classList.toggle("hidden", !aliases.length);
    $("refAliasTags")?.replaceChildren(...aliases.map((alias) => {
      const chip = document.createElement("b");
      chip.textContent = alias;
      return chip;
    }));
    const traits = Array.isArray(item.traits) ? item.traits : [];
    $("refTraitArea")?.classList.toggle("hidden", !traits.length);
    $("refTraitTags")?.replaceChildren(...traits.map((trait) => {
      const chip = document.createElement("b");
      chip.textContent = `${trait.facet || "character"} · ${trait.trait || ""}`;
      return chip;
    }));
    const styles = Array.isArray(relatedStyles) ? relatedStyles : [];
    $("refStyleArea")?.classList.toggle("hidden", !styles.length);
    const tags = $("refStyleTags");
    tags?.replaceChildren(...styles.map((style) => {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.textContent = style.tag || style.label || "";
      chip.title = "在独立画风资料中打开";
      chip.addEventListener("click", async () => {
        if ($("refQuery")) $("refQuery").value = style.tag || style.label || "";
        switchMode("styles", { search: false });
        await search(true);
        const match = state.items.find((item) => item.style_id === style.style_id);
        if (match) selectStyleReference(match.style_id);
      });
      return chip;
    }));
  }

  function renderStyleDetail(item) {
    state.styleDetail = item;
    state.detail = null;
    $("refDetailEmpty")?.classList.add("hidden");
    $("refDetail")?.classList.add("hidden");
    $("refStyleDetail")?.classList.remove("hidden");
    $("refStyleDetailSource").textContent = `${item.source || "未标注来源"} · LOCAL`;
    $("refStyleDetailName").textContent = item.label || item.tag || "未命名画风";
    $("refStyleDetailKind").textContent = `${labelStyleKind(item.kind)}资料 · 与角色槽分开保存`;
    $("refStyleDetailTag").textContent = item.tag || "";
    $("refStyleMetricKind").textContent = labelStyleKind(item.kind);
    $("refStyleLinked").textContent = Number(item.linked_characters || 0).toLocaleString();
    $("refStyleMetricSource").textContent = item.source || "—";
    $("refStyleDetailId").textContent = item.style_id || "—";
    $("refStyleDetailLicense").textContent = item.provenance?.license || "未标注";
    updateStyleTarget();
  }

  function showEmptyDetail() {
    $("refDetail")?.classList.add("hidden");
    $("refStyleDetail")?.classList.add("hidden");
    $("refDetailEmpty")?.classList.remove("hidden");
  }

  function syncSelectionAfterSearch() {
    if (state.mode === "styles") {
      const selected = state.items.find((item) => item.style_id === state.selectedStyleId);
      if (selected) renderStyleDetail(selected);
      else {
        state.selectedStyleId = "";
        state.styleDetail = null;
        showEmptyDetail();
      }
    } else if (!state.items.some((item) => item.reference_id === state.selectedId)) {
      state.selectedId = "";
      state.detail = null;
      showEmptyDetail();
    }
  }

  async function selectReference(referenceId) {
    state.selectedId = referenceId;
    document.querySelectorAll(".ref-item").forEach((node) => node.classList.toggle("active", node.dataset.referenceId === referenceId));
    try {
      const encoded = encodeURIComponent(referenceId);
      const [result, related] = await Promise.all([
        api(`/api/nai/references/${encoded}`),
        api(`/api/nai/references/${encoded}/styles`),
      ]);
      renderDetail(result.item, related.items || []);
    } catch (error) {
      toast(String(error.message || error), "err");
    }
  }

  function selectStyleReference(styleId) {
    state.selectedStyleId = styleId;
    document.querySelectorAll(".ref-style-item").forEach((node) => {
      node.classList.toggle("active", node.dataset.styleId === styleId);
    });
    const item = state.items.find((candidate) => candidate.style_id === styleId);
    if (item) renderStyleDetail(item);
  }

  function switchMode(mode, options) {
    const previousMode = state.mode;
    state.mode = mode === "styles" ? "styles" : "characters";
    if (previousMode !== state.mode) {
      state.selectedId = "";
      state.selectedStyleId = "";
      state.detail = null;
      state.styleDetail = null;
    }
    state.items = [];
    state.total = 0;
    state.offset = 0;
    state.hasMore = false;
    const styles = state.mode === "styles";
    $("refModeCharacters")?.classList.toggle("active", !styles);
    $("refModeStyles")?.classList.toggle("active", styles);
    $("refModeCharacters")?.setAttribute("aria-selected", String(!styles));
    $("refModeStyles")?.setAttribute("aria-selected", String(styles));
    $("refGenderField")?.classList.toggle("hidden", styles);
    $("refCopyrightField")?.classList.toggle("hidden", styles);
    $("refStyleKindField")?.classList.toggle("hidden", !styles);
    $("refPrimaryFilters")?.classList.toggle("styles", styles);
    if ($("refListTitle")) $("refListTitle").textContent = styles ? "独立画风资料" : "角色列表";
    if ($("refQuery")) $("refQuery").placeholder = styles
      ? "画师名、画风标签、来源…"
      : "角色名、别名、Trigger、作品…";
    showEmptyDetail();
    const empty = $("refDetailEmpty");
    if (empty) {
      const title = empty.querySelector("b");
      const hint = empty.querySelector("span:last-child");
      if (title) title.textContent = styles ? "选择一条画风资料" : "选择一个角色";
      if (hint) hint.textContent = styles
        ? "这里会显示独立画风标签、来源，以及安全的 Remix 草稿入口。"
        : "这里会显示整理后的 NAI 角色资料卡。";
    }
    const params = new URLSearchParams(window.location.search);
    if (styles) params.set("tab", "styles");
    else params.delete("tab");
    window.history.replaceState(null, "", `${window.location.pathname}${params.toString() ? `?${params}` : ""}`);
    renderList(true);
    updateStyleTarget();
    if (!options || options.search !== false) search(true);
  }

  function parseJsonRecords(text) {
    const parsed = JSON.parse(text);
    if (Array.isArray(parsed)) return parsed;
    if (!parsed || typeof parsed !== "object") return [];
    for (const key of ["records", "characters", "results", "items", "data"]) {
      if (Array.isArray(parsed[key])) return parsed[key];
    }
    return [parsed];
  }

  function parseJsonLines(text) {
    return text.split(/\r?\n/).map((line) => line.trim()).filter(Boolean).map((line, index) => {
      try { return JSON.parse(line); }
      catch (_) { throw new Error(`JSONL 第 ${index + 1} 行无法解析`); }
    });
  }

  function parseCsvRows(text) {
    const rows = [];
    let row = [];
    let cell = "";
    let quoted = false;
    for (let i = 0; i < text.length; i += 1) {
      const ch = text[i];
      if (ch === '"') {
        if (quoted && text[i + 1] === '"') { cell += '"'; i += 1; }
        else quoted = !quoted;
      } else if (ch === "," && !quoted) {
        row.push(cell); cell = "";
      } else if ((ch === "\n" || ch === "\r") && !quoted) {
        if (ch === "\r" && text[i + 1] === "\n") i += 1;
        row.push(cell); cell = "";
        if (row.some((value) => value.trim())) rows.push(row);
        row = [];
      } else cell += ch;
    }
    row.push(cell);
    if (row.some((value) => value.trim())) rows.push(row);
    if (rows.length < 2) return [];
    const headers = rows.shift().map((value) => value.trim().replace(/^\ufeff/, ""));
    return rows.map((values) => Object.fromEntries(headers.map((header, index) => [header, values[index] || ""])));
  }

  async function readImportFile(file) {
    const text = await file.text();
    const name = file.name.toLowerCase();
    if (name.endsWith(".csv")) return parseCsvRows(text);
    if (name.endsWith(".jsonl") || name.endsWith(".ndjson")) return parseJsonLines(text);
    try { return parseJsonRecords(text); }
    catch (_) { return parseJsonLines(text); }
  }

  async function chooseFile() {
    const file = $("refImportFile")?.files?.[0];
    state.fileRecords = null;
    $("refImportStart").disabled = true;
    if (!file) return;
    $("refFileLabel").textContent = file.name;
    try {
      const records = await readImportFile(file);
      if (!records.length || records.some((item) => !item || typeof item !== "object" || Array.isArray(item))) {
        throw new Error("文件中没有可用的角色对象");
      }
      state.fileRecords = records;
      $("refImportStart").disabled = false;
      setStatus(`已读取 ${records.length.toLocaleString()} 条，等待导入`, "ok");
    } catch (error) {
      setStatus(String(error.message || error), "err");
      toast(String(error.message || error), "err");
    }
  }

  async function importRecords() {
    if (state.importing || !state.fileRecords?.length) return;
    state.importing = true;
    $("refImportStart").disabled = true;
    const records = state.fileRecords;
    const totals = { inserted: 0, updated: 0, unchanged: 0, rejected: 0 };
    try {
      for (let start = 0; start < records.length; start += 1000) {
        const batch = records.slice(start, start + 1000);
        setImportProgress(start, records.length, `正在写入 ${start + 1}–${Math.min(start + batch.length, records.length)} 条`);
        const result = await api("/api/nai/references/import", {
          method: "POST",
          body: {
            records: batch,
            source: ($("refImportSource")?.value || "animadex").trim(),
            source_label: ($("refImportSource")?.value || "AnimaDex").trim(),
            version: ($("refImportVersion")?.value || "").trim(),
            license: ($("refImportLicense")?.value || "").trim(),
            model: $("refModel")?.value || "nai-diffusion-4-5-full",
          },
          timeoutMs: 120000,
        });
        Object.keys(totals).forEach((key) => { totals[key] += Number(result[key] || 0); });
      }
      setImportProgress(records.length, records.length, "导入完成");
      const report = `导入 ${totals.inserted} · 更新 ${totals.updated} · 未变化 ${totals.unchanged} · 拒绝 ${totals.rejected}`;
      setStatus(report, "ok");
      toast(report, "ok");
      await loadStats();
      await search(true);
    } catch (error) {
      setStatus(`导入中断：${String(error.message || error)}`, "err");
      toast(String(error.message || error), "err");
    } finally {
      state.importing = false;
      $("refImportStart").disabled = false;
    }
  }

  function currentDraft() {
    try { return JSON.parse(localStorage.getItem(DRAFT_KEY) || "null") || {}; }
    catch (_) { return {}; }
  }

  function commentFromDraft(draft) {
    if (draft.comment && typeof draft.comment === "object") return draft.comment;
    const texts = draft.texts || {};
    const charCaptions = Array.isArray(texts.char_captions) ? texts.char_captions : [];
    return {
      prompt: texts.prompt || texts.base_caption || "",
      uc: texts.uc || "",
      v4_prompt: {
        caption: {
          base_caption: texts.base_caption || texts.prompt || "",
          char_captions: charCaptions.map((caption) => ({ char_caption: caption, centers: [{ x: 0.5, y: 0.5 }] })),
        },
      },
    };
  }

  async function applyToStudio() {
    if (!state.selectedId || !state.detail) return;
    const draft = currentDraft();
    const slotIndex = Number($("refSlot")?.value || 0);
    $("refApply").disabled = true;
    $("refApply").textContent = "正在整理本地草稿…";
    try {
      const result = await api(`/api/nai/references/${encodeURIComponent(state.selectedId)}/apply`, {
        method: "POST",
        body: {
          comment: commentFromDraft(draft),
          slot_index: slotIndex,
          model: $("refModel")?.value || "nai-diffusion-4-5-full",
        },
      });
      const nextDraft = Object.assign({}, draft, {
        comment: result.comment,
        texts: result.texts,
        reference: {
          referenceId: state.selectedId,
          source: state.detail.source,
          sourceId: state.detail.source_id,
          label: state.detail.label,
          slotIndex,
        },
        ts: Date.now(),
      });
      localStorage.setItem(DRAFT_KEY, JSON.stringify(nextDraft));
      toast(result.message || "角色资料已放入 Studio 草稿", "ok");
      window.location.href = `/studio?reference=${encodeURIComponent(state.selectedId)}&slot=${slotIndex + 1}`;
    } catch (error) {
      toast(String(error.message || error), "err");
      $("refApply").disabled = false;
      $("refApply").textContent = "应用并打开 Studio →";
    }
  }

  async function applyStyleToRemix() {
    if (!state.selectedStyleId || !state.styleDetail) {
      toast("请先选择一条画风资料", "err");
      return;
    }
    const workId = String($("refStyleWorkId")?.value || "").trim();
    if (!/^\d{1,20}$/.test(workId) || workId === "0") {
      toast("请输入正确的来源作品 ID", "err");
      $("refStyleWorkId")?.focus();
      return;
    }
    const page = Math.max(1, Math.min(1000, Number($("refStylePage")?.value || 1)));
    const button = $("refStyleApply");
    if (button) {
      button.disabled = true;
      button.textContent = "正在准备本地 Remix 草稿…";
    }
    try {
      const result = await api(`/api/nai/references/styles/${encodeURIComponent(state.selectedStyleId)}/draft`, {
        method: "POST",
        body: {
          gallery_id: $("refStyleGallery")?.value || "site",
          work_id: workId,
          page_index: page - 1,
          mode: $("refStyleMode")?.value || "preset",
        },
        timeoutMs: 60000,
      });
      localStorage.setItem(DRAFT_KEY, JSON.stringify({ ...result.draft, ts: Date.now() }));
      toast(result.message || "画风 Remix 草稿已准备", "ok");
      window.location.href = result.studio_url || "/studio?remix=1";
    } catch (error) {
      toast(String(error.message || error), "err");
      if (button) {
        button.disabled = false;
        button.textContent = "准备换画风草稿并打开 Studio →";
        updateStyleTarget();
      }
    }
  }

  function styleTarget() {
    const workId = String($("refStyleWorkId")?.value || "").trim();
    const galleryId = $("refStyleGallery")?.value || "site";
    const page = Math.max(1, Math.min(1000, Number($("refStylePage")?.value || 1)));
    return {
      valid: /^\d{1,20}$/.test(workId) && workId !== "0",
      workId,
      galleryId,
      page,
    };
  }

  function updateStyleTarget() {
    const target = styleTarget();
    const text = $("refStyleTargetText");
    const link = $("refStyleTargetLink");
    if (target.valid) {
      if (text) text.textContent = `${GALLERY_LABELS[target.galleryId] || "图库"} · 作品 ${target.workId} · p${target.page}`;
      if (link) {
        link.textContent = "查看来源";
        link.href = `/i/${encodeURIComponent(target.workId)}?gallery=${encodeURIComponent(target.galleryId)}`;
      }
    } else {
      if (text) text.textContent = "尚未选择作品";
      if (link) { link.textContent = "去图库选择"; link.href = "/"; }
    }
    if ($("refStyleApply")) $("refStyleApply").disabled = !(target.valid && state.selectedStyleId);
  }

  function debounce(fn, wait) {
    let timer = 0;
    return function () {
      clearTimeout(timer);
      timer = window.setTimeout(fn, wait);
    };
  }

  async function submitManualCharacter() {
    const label = ($("refManualLabel")?.value || "").trim();
    if (!label) {
      toast("请填写角色显示名", "err");
      $("refManualLabel")?.focus();
      return;
    }
    const btn = $("refManualSubmit");
    if (btn) { btn.disabled = true; btn.textContent = "保存中…"; }
    try {
      const payload = {
        label,
        gender: $("refManualGender")?.value || "female",
        copyright: ($("refManualCopyright")?.value || "").trim() || "自定义角色",
        trigger: ($("refManualTrigger")?.value || "").trim(),
        character_caption: ($("refManualCaption")?.value || "").trim(),
      };
      const res = await api("/api/nai/references/custom", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      toast(res.message || "角色添加成功", "ok");
      if ($("refManualLabel")) $("refManualLabel").value = "";
      if ($("refManualCopyright")) $("refManualCopyright").value = "";
      if ($("refManualTrigger")) $("refManualTrigger").value = "";
      if ($("refManualCaption")) $("refManualCaption").value = "";
      if ($("refManualAdd")) $("refManualAdd").open = false;
      await loadStats();
      if ($("refQuery")) $("refQuery").value = label;
      await search(true);
    } catch (err) {
      toast(err.message || "添加角色失败", "err");
    } finally {
      if (btn) { btn.disabled = false; btn.textContent = "保存并加入资料库 →"; }
    }
  }

  function bind() {

    const delayedSearch = debounce(() => search(true), 250);
    $("refQuery")?.addEventListener("input", delayedSearch);
    $("refQuery")?.addEventListener("keydown", (event) => {
      if (event.key === "Enter") { event.preventDefault(); search(true); }
    });
    ["refGender", "refSource", "refCopyright", "refStyleKind"].forEach((id) => $(id)?.addEventListener("change", () => search(true)));
    $("refModeCharacters")?.addEventListener("click", () => switchMode("characters"));
    $("refModeStyles")?.addEventListener("click", () => switchMode("styles"));
    $("refRefresh")?.addEventListener("click", async () => { await loadStats(); await search(true); });
    $("refLoadMore")?.addEventListener("click", () => search(false));
    $("refImportFile")?.addEventListener("change", chooseFile);
    $("refImportStart")?.addEventListener("click", importRecords);
    $("refManualSubmit")?.addEventListener("click", submitManualCharacter);
    $("refApply")?.addEventListener("click", applyToStudio);

    $("refStyleApply")?.addEventListener("click", applyStyleToRemix);
    ["refStyleGallery", "refStyleWorkId", "refStylePage"].forEach((id) => {
      $(id)?.addEventListener(id === "refStyleWorkId" ? "input" : "change", updateStyleTarget);
    });
    $("refCopyCaption")?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(state.detail?.character_caption || "");
        toast("NAI 角色槽内容已复制", "ok");
      } catch (_) { toast("复制失败，请手动选择文本", "err"); }
    });
    $("refCopyStyle")?.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(state.styleDetail?.tag || "");
        toast("NAI 画风标签已复制", "ok");
      } catch (_) { toast("复制失败，请手动选择文本", "err"); }
    });
  }

  async function boot() {
    bind();
    const params = new URLSearchParams(window.location.search);
    const initialQuery = params.get("q");
    if (initialQuery && $("refQuery")) $("refQuery").value = initialQuery.slice(0, 200);
    const tab = params.get("tab");
    switchMode(tab === "styles" ? "styles" : "characters", { search: false });
    const lastWork = window.WorkBridge?.load?.();
    if (lastWork?.workId) {
      if ($("refStyleWorkId")) $("refStyleWorkId").value = String(lastWork.workId);
      if ($("refStyleGallery")) $("refStyleGallery").value = String(lastWork.galleryId || "site");
      if ($("refStylePage")) $("refStylePage").value = String(Number(lastWork.pageIndex || 0) + 1);
    }
    updateStyleTarget();
    await loadStats();
    await search(true);
  }

  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", boot);
  else boot();
})();
