// Global state and cache management for the CharSwap plugin

export const BATCH_MAX_DEFAULT = 250;
export const extractCache = new Map();
export const draftPageCache = new Map();
const DRAFT_CACHE_VERSION = 8;
const DRAFT_CACHE_KEY = "charSwapDraftPageCache.v8";
const LEGACY_DRAFT_CACHE_KEYS = [
  "charSwapDraftPageCache",
  "charSwapDraftPageCache.v6",
  "charSwapDraftPageCache.v7",
];
const DRAFT_CACHE_MAX = 80;

export const state = {
  workId: null,
  pageIndex: 0,
  galleryId: "site",
  original: null,
  draft: null,
  draftChars: [],
  styleSlots: [],
  styleBundle: { groups: [], combined: "", combined_all: "" },
  pluginConfig: null,
  lastRemoved: [],
  generating: false,
  collapsed: false,
  seedBeforeRandom: null,
  imagePageCount: 1,
  workTitle: "",

  // Timers and status
  batchPollTimer: null,
  lastBatchStatus: "idle",
  genSidebarTimer: null,
  genSidebarPoll: { noGroup: false, busy: false }
};

export function normalizeWorkId(value) {
  const id = String(value == null ? "" : value).trim();
  return /^\d+$/.test(id) && id !== "0" ? id : "";
}

export function normalizeGalleryId(value) {
  const id = String(value == null ? "" : value).trim().toLowerCase();
  return ["site", "codex", "qqgroup"].includes(id) ? id : "site";
}

export function persistDraftCache() {
  try {
    LEGACY_DRAFT_CACHE_KEYS.forEach((key) => localStorage.removeItem(key));
    const entries = [...draftPageCache.entries()].slice(-DRAFT_CACHE_MAX);
    localStorage.setItem(DRAFT_CACHE_KEY, JSON.stringify(entries));
  } catch { }
}

export function restoreDraftCache() {
  try {
    draftPageCache.clear();
    let raw = localStorage.getItem(DRAFT_CACHE_KEY);
    let migratedFromV6 = false;
    if (!raw) {
      raw = localStorage.getItem("charSwapDraftPageCache.v6");
      migratedFromV6 = !!raw;
    }
    if (!raw) return;
    const entries = JSON.parse(raw);
    if (!Array.isArray(entries)) {
      localStorage.removeItem(DRAFT_CACHE_KEY);
      return;
    }
    entries.forEach(([key, value]) => {
      const normalizedKey = migratedFromV6 && /^\d+:\d+$/.test(String(key || ""))
        ? `site:${key}`
        : String(key || "");
      if (
        normalizedKey
        && value
        && value.draft
        && (value.cacheVersion === DRAFT_CACHE_VERSION || (migratedFromV6 && value.cacheVersion === 6))
      ) {
        draftPageCache.set(normalizedKey, { ...value, cacheVersion: DRAFT_CACHE_VERSION });
      }
    });
    if (migratedFromV6) persistDraftCache();
  } catch {
    try { localStorage.removeItem(DRAFT_CACHE_KEY); } catch { }
  }
}

// Initialize cache from localStorage
restoreDraftCache();

export function draftCacheKey(workId, pageIndex, galleryId = state.galleryId) {
  return `${normalizeGalleryId(galleryId)}:${normalizeWorkId(workId)}:${Number(pageIndex || 0)}`;
}

export function buildStyleBundleFallback(slots) {
  const tags = (slots || []).map((s) => s.tag).filter(Boolean);
  const combined = tags.join(", ");
  return {
    groups: [{ label: "style_tags", combined, tags }],
    combined,
    combined_all: combined,
  };
}

export function saveCurrentDraftToCache() {
  if (state.workId == null || !state.draft) return;
  draftPageCache.set(draftCacheKey(state.workId, state.pageIndex), {
    cacheVersion: DRAFT_CACHE_VERSION,
    draft: JSON.parse(JSON.stringify(state.draft)),
    draftChars: JSON.parse(JSON.stringify(state.draftChars)),
    styleSlots: JSON.parse(JSON.stringify(state.styleSlots || [])),
    styleBundle: JSON.parse(JSON.stringify(state.styleBundle || buildStyleBundleFallback(state.styleSlots))),
    lastRemoved: JSON.parse(JSON.stringify(state.lastRemoved || [])),
  });
  persistDraftCache();
}

export function loadDraftFromCache(workId, pageIndex) {
  const key = draftCacheKey(workId, pageIndex);
  const cached = draftPageCache.get(key);
  if (!cached) return false;
  if (cached.cacheVersion !== DRAFT_CACHE_VERSION) {
    draftPageCache.delete(key);
    persistDraftCache();
    return false;
  }
  state.draft = JSON.parse(JSON.stringify(cached.draft));
  state.draftChars = JSON.parse(JSON.stringify(cached.draftChars || []));
  state.styleSlots = JSON.parse(JSON.stringify(cached.styleSlots || []));
  state.styleBundle = JSON.parse(JSON.stringify(cached.styleBundle || buildStyleBundleFallback(state.styleSlots)));
  state.lastRemoved = JSON.parse(JSON.stringify(cached.lastRemoved || []));
  return true;
}

export function clearDraftCacheForPage(workId, pageIndex) {
  const key = draftCacheKey(workId, pageIndex);
  draftPageCache.delete(key);
  persistDraftCache();
}

export function clearDraftCacheForWork(workId, galleryId = state.galleryId) {
  const id = normalizeWorkId(workId);
  if (!id) return;
  const prefix = `${normalizeGalleryId(galleryId)}:${id}:`;
  let deleted = false;
  for (const key of draftPageCache.keys()) {
    if (key.startsWith(prefix)) {
      draftPageCache.delete(key);
      deleted = true;
    }
  }
  if (deleted) persistDraftCache();
}

const SOURCE_KEY = "charSwapSource";

export function saveSource(source) {
  try {
    localStorage.setItem(SOURCE_KEY, JSON.stringify(source));
  } catch {}
}

export function loadSource() {
  try {
    const raw = localStorage.getItem(SOURCE_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch {
    return null;
  }
}

export function clearSource() {
  try {
    localStorage.removeItem(SOURCE_KEY);
  } catch {}
}
