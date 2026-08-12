(function () {
  const CACHE = new Map();
  const CACHE_MAX = 128;
  const CACHE_TTL_MS = 10 * 60 * 1000;

  function normalizeWorkId(value) {
    if (window.WorkBridge && typeof window.WorkBridge.normalizeWorkId === "function") {
      return window.WorkBridge.normalizeWorkId(value);
    }
    const id = String(value == null ? "" : value).trim();
    return /^\d+$/.test(id) && id !== "0" ? id : "";
  }

  function trimSnippet(text, maxLen) {
    const raw = String(text || "").trim();
    if (!raw) return "";
    const limit = maxLen || 520;
    return raw.length > limit ? `${raw.slice(0, limit)}…` : raw;
  }

  function evictIfNeeded() {
    if (CACHE.size < CACHE_MAX) return;
    const oldest = CACHE.keys().next().value;
    if (oldest) CACHE.delete(oldest);
  }

  async function fetchSnippet(workId, pageIndex, apiBase) {
    const wid = normalizeWorkId(workId);
    if (!wid) return "";
    const page = Number(pageIndex) || 0;
    const key = `${wid}:${page}`;
    const hit = CACHE.get(key);
    if (hit && Date.now() - hit.at < CACHE_TTL_MS) {
      return hit.snippet;
    }
    const data = await ApiClient.get(`/api/studio/preview?work_id=${encodeURIComponent(wid)}&page_index=${page}`);
    const snippet = trimSnippet(data.snippet || "");
    evictIfNeeded();
    CACHE.set(key, { at: Date.now(), snippet });
    return snippet;
  }

  window.PromptPreview = {
    fetchSnippet,
    trimSnippet,
    clear() {
      CACHE.clear();
    },
  };
})();
