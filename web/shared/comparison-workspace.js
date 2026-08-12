(function () {
  const STORAGE_KEY = "aitag.butler.comparison.v1";
  const MAX_CANDIDATES = 4;

  function cleanText(value, limit) {
    return String(value || "").replace(/\0/g, "").trim().slice(0, limit || 300);
  }

  function normalizeCandidate(raw) {
    const item = raw && typeof raw === "object" ? raw : {};
    const galleryId = cleanText(item.gallery_id || item.galleryId || "site", 20).toLowerCase();
    const workId = cleanText(item.work_id || item.workId || "", 80);
    const pageIndex = Math.max(0, Number.parseInt(item.page_index || item.pageIndex || "0", 10) || 0);
    if (!["site", "codex", "qqgroup"].includes(galleryId) || !/^\d+$/.test(workId) || workId === "0") {
      throw new Error("这张图片没有可用的图库身份，暂时不能加入对比");
    }
    return {
      candidate_id: `gallery:${galleryId}:${workId}:p${pageIndex}`,
      gallery_id: galleryId,
      work_id: workId,
      page_index: pageIndex,
      title: cleanText(item.title || `作品 ${workId}`, 160),
      thumb: cleanText(item.thumb, 800),
      url: cleanText(item.url || `/i/${workId}?gallery=${galleryId}`, 800),
    };
  }

  class ComparisonWorkspace {
    constructor(storage) {
      this.storage = storage || window.localStorage;
      this.items = [];
      this.load();
    }

    load() {
      try {
        const parsed = JSON.parse(this.storage.getItem(STORAGE_KEY) || "{}");
        const seen = new Set();
        this.items = (Array.isArray(parsed.items) ? parsed.items : []).slice(0, MAX_CANDIDATES)
          .map(normalizeCandidate)
          .filter((item) => {
            if (seen.has(item.candidate_id)) return false;
            seen.add(item.candidate_id);
            return true;
          });
      } catch (_) {
        this.items = [];
      }
      return this.snapshot();
    }

    save() {
      this.storage.setItem(STORAGE_KEY, JSON.stringify({ items: this.items, updated_at: Date.now() }));
      return this.snapshot();
    }

    snapshot() {
      return this.items.map((item) => ({ ...item }));
    }

    add(raw) {
      const item = normalizeCandidate(raw);
      if (this.items.some((current) => current.candidate_id === item.candidate_id)) return this.snapshot();
      if (this.items.length >= MAX_CANDIDATES) throw new Error("候选集最多放 4 张，请先移除一张再加入");
      this.items.push(item);
      return this.save();
    }

    remove(candidateId) {
      this.items = this.items.filter((item) => item.candidate_id !== String(candidateId || ""));
      return this.save();
    }

    clear() {
      this.items = [];
      return this.save();
    }
  }

  window.ComparisonWorkspace = { ComparisonWorkspace, STORAGE_KEY, MAX_CANDIDATES };
})();
